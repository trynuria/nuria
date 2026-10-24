"""Learn a discrete recurrent spike memory, then check reachable-state closure.

This is a deliberately clocked binary spiking model, not Nuria's Brian2 LIF
model. The complete inter-symbol state is the vector of queued binary memory
spikes. Transition and memory populations fire on alternating clock phases.
All membrane values reset on each phase; no analog state is retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import deque
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

torch.set_num_threads(2)
torch.set_num_interop_threads(1)


class Spike(torch.autograd.Function):
    @staticmethod
    def forward(ctx, voltage):
        ctx.save_for_backward(voltage)
        return (voltage >= 0).to(voltage.dtype)

    @staticmethod
    def backward(ctx, grad):
        (voltage,) = ctx.saved_tensors
        return grad / (1 + 2 * voltage.abs()).square()


class SpikeMemory(nn.Module):
    def __init__(self, silenced=(), noise_bits=0):
        super().__init__()
        self.transition = nn.Linear(9 + 12, 64)
        self.memory = nn.Linear(64, 12)
        self.output = nn.Linear(12, 6)
        self.silenced = tuple(silenced)
        self.noise_bits = noise_bits

    def forward(self, data):
        state = torch.zeros(data.shape[1], 12)
        predictions, memories = [], []
        for offset, event in enumerate(data):
            incoming = state
            if self.training and offset and self.noise_bits:
                indices = torch.rand(state.shape).argsort(-1)
                count = torch.randint(self.noise_bits + 1, (len(state), 1))
                mask = torch.zeros_like(state).scatter_(
                    1, indices, (torch.arange(12)[None] < count).to(state.dtype)
                )
                incoming = state * (1 - mask) + (1 - state) * mask
            transition_spikes = Spike.apply(
                self.transition(torch.cat([event, incoming], -1))
            )
            state = Spike.apply(self.memory(transition_spikes))
            if self.silenced:
                mask = torch.ones(12)
                mask[list(self.silenced)] = 0
                state = state * mask
            predictions.append(self.output(state))
            memories.append(state)
        return torch.stack(predictions), torch.stack(memories)


def action(pair, symbol):
    x, y = pair
    return (1 - x, y) if symbol == 0 else (y, x) if symbol == 1 else pair


def fingerprint(pair):
    # Evaluator-side task specification, never called by training data creation.
    return tuple(
        bit
        for symbol in (-1, 0, 1)
        for bit in (pair if symbol == -1 else action(pair, symbol))
    )


class Device:
    """Resettable one-bit observation interface; hidden state stays in teacher."""

    audit = {"observations": 0, "pulses": 0, "probe_forks": 0, "sample_queries": []}

    def __init__(self, initial):
        self._lamps = tuple(initial)

    def pulse(self, symbol):
        x, y = self._lamps
        if symbol == 0:
            self._lamps = (1 - x, y)
        elif symbol == 1:
            self._lamps = (y, x)
        elif symbol != 2:
            raise ValueError("Unknown device command")
        self.audit["pulses"] += 1

    def query(self, suffix):
        fork = Device(self._lamps)
        self.audit["probe_forks"] += 1
        for symbol in suffix:
            fork.pulse(symbol)
        # Only this scalar physical reading crosses the observation interface.
        observation = int(fork._lamps[0])
        self.audit["observations"] += 1
        if len(self.audit["sample_queries"]) < 256:
            self.audit["sample_queries"].append(
                {"suffix": list(suffix), "observed_bit": observation}
            )
        return observation


def observe_queries(device):
    # Readout ordering matches the evaluator's x/y, flip and swap consequences,
    # but every value is obtained through an actual one-bit query and a suffix.
    return tuple(
        device.query(suffix) for suffix in ((), (1,), (0,), (0, 1), (1,), (1, 1))
    )


def event_initial(pair, nuisance):
    v = [0] * 9
    v[pair[0]], v[2 + pair[1]], v[7 + nuisance] = 1, 1, 1
    return v


def event_action(symbol, nuisance):
    v = [0] * 9
    v[4 + symbol], v[7 + nuisance] = 1, 1
    return v


def batch(rng, size, length, replay):
    histories = []
    for index in range(size):
        matching = [h for h in replay if len(h["symbols"]) == length]
        replayed = bool(matching) and rng.random() < 0.4
        if replayed:
            h = matching[int(rng.integers(len(matching)))]
            pair, symbols = tuple(h["initial"]), list(h["symbols"])
            nuisances = h["nuisances"]
        else:
            pair = tuple(int(x) for x in rng.integers(0, 2, 2))
            symbols = rng.integers(0, 3, length).tolist()
            nuisances = [int(rng.integers(2))] * (length + 1)
        device = Device(pair)
        observed = observe_queries(device)
        data, labels = [event_initial(observed[:2], nuisances[0])], [observed]
        for offset, symbol in enumerate(symbols, 1):
            device.pulse(symbol)
            data.append(event_action(symbol, nuisances[offset]))
            labels.append(observe_queries(device))
        histories.append((data, labels))
    x, y = zip(*histories)
    return torch.tensor(x, dtype=torch.float32).transpose(0, 1), torch.tensor(
        y, dtype=torch.float32
    ).transpose(0, 1)


def serialize(model, scale=4096):
    result = {
        "format": "clocked-binary-spikes-v1",
        "scale": scale,
        "state_neurons": 12,
        "transition_neurons": 64,
        "silenced_memory_neurons": list(model.silenced),
        "phase": "canonical memory-spike boundary; transition membrane reset",
        "alphabet": ["flip", "swap", "noop"],
        "tick_order": [
            "transition population fires from input and queued memory spikes",
            "memory population fires from transition spikes",
            "output projection thresholded at zero",
        ],
        "arrays": {},
    }
    for name, value in model.state_dict().items():
        result["arrays"][name] = (
            np.rint(value.detach().numpy() * scale).astype(int).tolist()
        )
    return result


def matrices(spec):
    result = {k: np.asarray(v, dtype=np.int64) for k, v in spec["arrays"].items()}
    result["silenced"] = spec.get("silenced_memory_neurons", [])
    return result


def tick(values, state, event):
    hidden = (
        values["transition.weight"] @ np.r_[event, state] + values["transition.bias"]
        >= 0
    ).astype(np.int64)
    next_state = (values["memory.weight"] @ hidden + values["memory.bias"] >= 0).astype(
        np.int64
    )
    next_state[values["silenced"]] = 0
    output = (values["output.weight"] @ next_state + values["output.bias"] >= 0).astype(
        np.int64
    )
    return next_state, output


def closure(spec, max_states=100_000):
    """Evaluator traverses the product of task state and all reachable spikes.

    State labels are used only here; the learner receives counterexample input
    words plus observed query responses, never a state ID or a prescribed code.
    """
    weights = matrices(spec)
    queue, visited, edges = deque(), set(), 0
    for x in range(2):
        for y in range(2):
            for nuisance in range(2):
                state, out = tick(
                    weights, np.zeros(12, int), event_initial((x, y), nuisance)
                )
                if tuple(out) != fingerprint((x, y)):
                    return {
                        "passed": False,
                        "reason": "initial output",
                        "counterexample": {
                            "initial": [x, y],
                            "symbols": [],
                            "nuisances": [nuisance],
                        },
                        "visited": 0,
                    }
                queue.append((state, (x, y), nuisance, (x, y), [], [nuisance]))
    while queue:
        state, pair, nuisance, initial, word, nuisances = queue.popleft()
        key = (tuple(state), pair, nuisance)
        if key in visited:
            continue
        visited.add(key)
        if len(visited) > max_states:
            return {
                "passed": False,
                "reason": "state budget exhausted",
                "visited": len(visited),
            }
        for symbol in range(3):
            # Nuisance may change adversarially at EVERY step, not merely
            # follow the fixed-per-example training pattern.
            for next_nuisance in range(2):
                target = action(pair, symbol)
                next_state, output = tick(
                    weights, state, event_action(symbol, next_nuisance)
                )
                edges += 1
                if tuple(output) != fingerprint(target):
                    return {
                        "passed": False,
                        "reason": "composition counterexample",
                        "counterexample": {
                            "initial": list(initial),
                            "symbols": word + [symbol],
                            "nuisances": nuisances + [next_nuisance],
                        },
                        "observed": output.tolist(),
                        "expected": list(fingerprint(target)),
                        "visited": len(visited),
                        "checked_edges": edges,
                    }
                queue.append(
                    (
                        next_state,
                        target,
                        next_nuisance,
                        initial,
                        word + [symbol],
                        nuisances + [next_nuisance],
                    )
                )
    return {
        "passed": True,
        "reason": "complete reachable product graph closed",
        "reachable_product_states": len(visited),
        "checked_edges": edges,
        "claim": "Correct probe outputs for every finite action string at the declared clock boundaries, including arbitrary binary nuisance changes",
    }


def train(args):
    args.out.mkdir(parents=True, exist_ok=False)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    model = SpikeMemory(args.silence, args.noise_bits)
    initial_check = None
    if args.parent:
        parent = json.loads(args.parent.read_text())
        model.load_state_dict(
            {
                key: torch.tensor(value, dtype=torch.float32) / parent["scale"]
                for key, value in parent["arrays"].items()
            }
        )
        initial_check = closure(serialize(model))
    opt = torch.optim.Adam(model.parameters(), lr=0.003)
    replay, rounds = [], []
    if initial_check and initial_check.get("counterexample"):
        replay.append(initial_check["counterexample"])
    started = time.monotonic()
    for iteration in range(args.rounds):
        last = {}
        for step in range(args.updates):
            length = (
                len(replay[int(rng.integers(len(replay)))]["symbols"])
                if replay and rng.random() < 0.5
                else int(rng.integers(1, 5))
            )
            data, targets = batch(rng, 128, length, replay)
            logits, states = model(data)
            loss = F.binary_cross_entropy_with_logits(logits, targets)
            # Compression uses only observed future-query fingerprints. This is
            # the COMPLETE recurrent state in this explicitly clocked model.
            features = states[-1]
            distances = (features[:, None] - features[None]).square().mean(-1)
            equivalent = (targets[-1, :, None] == targets[-1, None]).all(-1)
            same = distances[equivalent].mean()
            separated = F.relu(0.25 - distances[~equivalent]).mean()
            loss = loss + args.compression * (same + separated)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            last = {
                "loss": float(loss.detach()),
                "prediction_accuracy": float(
                    ((logits > 0) == (targets > 0.5)).all(-1).float().mean()
                ),
                "same_state_distance": float(same.detach()),
            }
        spec = serialize(model)
        check = closure(spec)
        record = {
            "round": iteration,
            "elapsed_seconds": time.monotonic() - started,
            "training": last,
            "closure": check,
        }
        rounds.append(record)
        (args.out / f"network-{iteration:02d}.json").write_text(
            json.dumps(spec, separators=(",", ":")) + "\n"
        )
        (args.out / "progress.json").write_text(json.dumps(rounds, indent=2) + "\n")
        print(json.dumps(record), flush=True)
        if check["passed"]:
            break
        if check.get("counterexample"):
            replay.append(check["counterexample"])
    summary = {
        "seed": args.seed,
        "noise_bits_in_training": args.noise_bits,
        "compression": args.compression,
        "updates_per_round": args.updates,
        "rounds": rounds,
        "initial_check": initial_check,
        "parent_network_sha256": hashlib.sha256(args.parent.read_bytes()).hexdigest()
        if args.parent
        else None,
        "counterexample_histories": replay,
        "query_audit": Device.audit,
        "parameters": sum(p.numel() for p in model.parameters()),
        "torch_version": torch.__version__,
        "runtime": "Integer quantization at scale4096; two synchronous threshold/reset populations; no Brian2 or production imports",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (args.out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seed", type=int, default=9101)
    p.add_argument("--rounds", type=int, default=8)
    p.add_argument("--updates", type=int, default=1000)
    p.add_argument("--compression", type=float, default=0.15)
    p.add_argument("--parent", type=Path)
    p.add_argument("--silence", type=int, nargs="*", default=[])
    p.add_argument("--noise-bits", type=int, default=0)
    train(p.parse_args())


if __name__ == "__main__":
    main()
