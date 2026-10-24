"""Outcome-trained spiking choices with source-bound delayed feedback.

This numerical policy does not execute tools or authenticate buyer outcomes.
Its caller must verify source, contract, metered work and acceptance before
supplying a reward. Hard spike counts determine action values; conventional
selected-action regression and a declared surrogate train their input synapses.
Stored originating observations, not unrelated current observations, receive
delayed feedback. This is a task-conditioned specialist, not a consciousness
claim or an automatic migration of an established recurrent circuit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from numbers import Real

import numpy as np

VERSION = "nuria.spiking-decision-policy.v1"
HASH = re.compile(r"[a-f0-9]{64}\Z")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
PARAMETERS = ("W", "H", "V", "C")
RECEIPT_WINDOW = 512
MAX_PENDING = 32
MAX_INTEGER = 2**53 - 1
WEIGHT_BOUND = 20.0


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError("A complete lowercase SHA256 identity is required")
    return value


def _finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _array(value, shape, *, bound=WEIGHT_BOUND):
    try:
        objects = np.asarray(value, dtype=object)
    except (ValueError, TypeError):
        raise ValueError("Policy state must contain bounded numeric arrays") from None
    if objects.shape != shape or any(
        not isinstance(x, Real) or isinstance(x, (bool, np.bool_)) for x in objects.flat
    ):
        raise ValueError(
            "Policy state must contain real numbers, not booleans or strings"
        )
    array = objects.astype(np.float64)
    if (
        array.shape != shape
        or not np.isfinite(array).all()
        or np.any(np.abs(array) > bound)
    ):
        raise ValueError("Policy numerical state has the wrong shape or bounds")
    return array.astype(np.float64, copy=True)


class SpikingDecisionPolicy:
    """Select a declared tool; learn only its checked scalar outcome.

    Membrane resets for each observation. Learned weights, Adam moments,
    replay, random generator, pending origins and feedback receipts persist.
    The continuous and frozen-input mechanisms are matched controls. The
    finite receipt window detects exact retained retries; the authoritative
    caller must also keep its durable, lifetime contract-consumption ledger.
    """

    def __init__(
        self,
        *,
        source_id,
        feature_dimension,
        arm_ids=("abstain", "reuse", "search_small", "search_full"),
        seed=20261025,
        width=64,
        ticks=16,
        mechanism="spiking",
        frozen_input=False,
        learning_rate=0.003,
        replay_capacity=256,
        updates=4,
        batch_size=32,
        exploration=0.1,
        warmup=32,
        state=None,
    ):
        if (
            not isinstance(source_id, str)
            or not SOURCE.fullmatch(source_id)
            or not _integer(feature_dimension, 1, 512)
            or not isinstance(arm_ids, (list, tuple))
            or not 2 <= len(arm_ids) <= 16
            or any(not isinstance(x, str) or not SOURCE.fullmatch(x) for x in arm_ids)
            or len(set(arm_ids)) != len(arm_ids)
            or not _integer(seed, 0, 2**32 - 1)
            or not _integer(width, 1, 256)
            or not _integer(ticks, 1, 64)
            or mechanism not in ("spiking", "continuous")
            or type(frozen_input) is not bool
            or not _finite(learning_rate, 1e-6, 0.1)
            or not _integer(replay_capacity, 1, RECEIPT_WINDOW)
            or not _integer(updates, 1, 16)
            or not _integer(batch_size, 1, 128)
            or not _finite(exploration, 0, 1)
            or not _integer(warmup, 0, 4096)
        ):
            raise ValueError(
                "Policy source, architecture or learning limits are invalid"
            )
        self.configuration = {
            "source_id": source_id,
            "feature_dimension": feature_dimension,
            "arm_ids": list(arm_ids),
            "seed": seed,
            "width": width,
            "ticks": ticks,
            "mechanism": mechanism,
            "frozen_input": frozen_input,
            "learning_rate": float(learning_rate),
            "replay_capacity": replay_capacity,
            "updates": updates,
            "batch_size": batch_size,
            "exploration": float(exploration),
            "warmup": warmup,
        }
        self.arms = list(arm_ids)
        self.shapes = {
            "W": (feature_dimension, width),
            "H": (width,),
            "V": (width, len(self.arms)),
            "C": (len(self.arms),),
        }
        self.rng = np.random.default_rng(seed + 1009)
        initial = np.random.default_rng(seed)
        self.W = initial.normal(0, math.sqrt(2 / feature_dimension), self.shapes["W"])
        self.H = np.full(width, 0.15)
        self.V = initial.normal(0, 0.05, self.shapes["V"])
        self.C = np.zeros(len(self.arms))
        self.first = {k: np.zeros(v) for k, v in self.shapes.items()}
        self.second = {k: np.zeros(v) for k, v in self.shapes.items()}
        self.optimizer_step = self.feedback_cursor = self.choices = 0
        self.arm_counts = [0] * len(self.arms)
        self.pending, self.receipts, self.replay = {}, {}, []
        if state is not None:
            try:
                self._restore(state)
            except (KeyError, TypeError, IndexError, OverflowError):
                raise ValueError(
                    "Policy checkpoint fields or types are malformed"
                ) from None

    def _features(self, value):
        array = _array(value, (self.configuration["feature_dimension"],), bound=1)
        if np.any(array < 0):
            raise ValueError("Policy features must be normalized in [0,1]")
        return array

    def _restore(self, state):
        if (
            type(state) is not dict
            or set(state)
            != {
                "schema",
                "configuration",
                "weights",
                "optimizer",
                "rng",
                "choices",
                "arm_counts",
                "feedback_cursor",
                "pending",
                "receipts",
                "replay",
            }
            or state["schema"] != VERSION
            or canonical(state["configuration"]) != canonical(self.configuration)
            or type(state["weights"]) is not dict
            or set(state["weights"]) != set(PARAMETERS)
            or not _integer(state["choices"], 0, MAX_INTEGER)
            or not _integer(state["feedback_cursor"], 0, MAX_INTEGER)
            or type(state["arm_counts"]) is not list
            or len(state["arm_counts"]) != len(self.arms)
            or any(not _integer(n, 0, MAX_INTEGER) for n in state["arm_counts"])
            or sum(state["arm_counts"]) != state["choices"]
            or type(state["pending"]) is not dict
            or len(state["pending"]) > MAX_PENDING
            or type(state["receipts"]) is not dict
            or len(state["receipts"]) > RECEIPT_WINDOW
            or type(state["replay"]) is not list
            or len(state["replay"]) > self.configuration["replay_capacity"]
            or state["choices"] != state["feedback_cursor"] + len(state["pending"])
            or set(state["receipts"])
            != {
                str(i)
                for i in range(
                    max(1, state["feedback_cursor"] - RECEIPT_WINDOW + 1),
                    state["feedback_cursor"] + 1,
                )
            }
        ):
            raise ValueError("Policy checkpoint identity or bounded structure differs")
        optimizer = state["optimizer"]
        if (
            type(optimizer) is not dict
            or set(optimizer) != {"step", "first", "second"}
            or not _integer(optimizer["step"], 0, MAX_INTEGER)
            or optimizer["step"]
            != state["feedback_cursor"] * self.configuration["updates"]
            or any(
                type(optimizer[k]) is not dict or set(optimizer[k]) != set(PARAMETERS)
                for k in ("first", "second")
            )
        ):
            raise ValueError("Policy optimizer checkpoint is malformed")
        weights = {
            k: _array(state["weights"][k], shape) for k, shape in self.shapes.items()
        }
        first = {
            k: _array(optimizer["first"][k], shape, bound=1.0000001)
            for k, shape in self.shapes.items()
        }
        second = {
            k: _array(optimizer["second"][k], shape, bound=1.0000001)
            for k, shape in self.shapes.items()
        }
        if (
            any(np.any(x < 0) for x in second.values())
            or optimizer["step"] == 0
            and any(np.any(x) for bank in (first, second) for x in bank.values())
        ):
            raise ValueError("Policy optimizer moments violate their bounds")
        if self.configuration["frozen_input"] and (
            not np.array_equal(weights["W"], self.W)
            or not np.array_equal(weights["H"], self.H)
            or any(np.any(bank[k]) for bank in (first, second) for k in ("W", "H"))
        ):
            raise ValueError("Frozen input weights or their optimizer changed")
        for decision_id, pending in state["pending"].items():
            _hash(decision_id)
            self._validate_origin(pending, decision_id)
        for seq, receipt in state["receipts"].items():
            if (
                not isinstance(seq, str)
                or not seq.isascii()
                or not seq.isdecimal()
                or str(int(seq)) != seq
                or not max(0, state["feedback_cursor"] - RECEIPT_WINDOW)
                < int(seq)
                <= state["feedback_cursor"]
            ):
                raise ValueError(
                    "Policy receipt sequence is outside its retained window"
                )
            if type(receipt) is not dict or set(receipt) != {
                "fingerprint",
                "origin",
                "result",
            }:
                raise ValueError("Policy receipt checkpoint is malformed")
            _hash(receipt["fingerprint"])
            self._validate_origin(receipt["origin"], receipt["result"]["decision_id"])
            if receipt["result"].get("seq") != int(seq):
                raise ValueError("Policy receipt result has a different sequence")
            self._validate_result(receipt)
        retained = {r["result"]["receipt_id"]: r for r in state["receipts"].values()}
        if len(retained) != len(state["receipts"]):
            raise ValueError("A policy receipt identity was credited twice")
        origins = list(state["pending"].values()) + [
            r["origin"] for r in state["receipts"].values()
        ]
        decisions = [x["selection"]["decision_id"] for x in origins]
        ordinals = [x["selection"]["ordinal"] for x in origins]
        if (
            len(set(decisions)) != len(decisions)
            or len(set(ordinals)) != len(ordinals)
            or any(x > state["choices"] for x in ordinals)
        ):
            raise ValueError(
                "Pending and credited origins overlap or exceed the choice history"
            )
        ordered = [
            state["receipts"][str(seq)] for seq in sorted(map(int, state["receipts"]))
        ]
        for first_receipt, next_receipt in zip(ordered, ordered[1:]):
            if (
                first_receipt["result"]["after_model_sha256"]
                != next_receipt["result"]["before_model_sha256"]
            ):
                raise ValueError("The retained learning model chain is discontinuous")
        if ordered:
            current_model = digest(
                {
                    "schema": VERSION,
                    "configuration": self.configuration,
                    "weights": {k: value.tolist() for k, value in weights.items()},
                }
            )
            if ordered[-1]["result"]["after_model_sha256"] != current_model:
                raise ValueError(
                    "Policy weights differ from their latest learning receipt"
                )
        for record in state["replay"]:
            if (
                type(record) is not dict
                or set(record) != {"features", "arm", "reward", "receipt_id"}
                or not _integer(record["arm"], 0, len(self.arms) - 1)
                or not _finite(record["reward"], -1, 1)
            ):
                raise ValueError("Policy replay contains an invalid selected outcome")
            self._features(record["features"])
            _hash(record["receipt_id"])
            receipt = retained.get(record["receipt_id"])
            if (
                receipt is None
                or record["features"] != receipt["origin"]["features"]
                or self.arms[record["arm"]] != receipt["result"]["arm"]
                or record["reward"] != receipt["result"]["reward"]
            ):
                raise ValueError("Replay differs from the checked selected outcome")
        expected_replay = [
            {
                "features": r["origin"]["features"],
                "arm": self.arms.index(r["result"]["arm"]),
                "reward": float(r["result"]["reward"]),
                "receipt_id": r["result"]["receipt_id"],
            }
            for r in ordered[-self.configuration["replay_capacity"] :]
        ]
        if canonical(state["replay"]) != canonical(expected_replay):
            raise ValueError(
                "Policy replay must be the exact ordered recent outcome suffix"
            )
        rng = copy.deepcopy(state["rng"])
        if type(rng) is not dict or rng.get("bit_generator") != "PCG64":
            raise ValueError("Policy random generator identity differs")
        try:
            self.rng.bit_generator.state = rng
        except (TypeError, ValueError, KeyError, OverflowError):
            raise ValueError("Policy random generator checkpoint is invalid") from None
        for k, value in weights.items():
            setattr(self, k, value)
        self.first, self.second = first, second
        self.optimizer_step = optimizer["step"]
        self.choices, self.arm_counts = (
            state["choices"],
            copy.deepcopy(state["arm_counts"]),
        )
        self.feedback_cursor = state["feedback_cursor"]
        self.pending, self.receipts, self.replay = [
            copy.deepcopy(state[k]) for k in ("pending", "receipts", "replay")
        ]

    def _validate_origin(self, origin, decision_id):
        _hash(decision_id)
        if type(origin) is not dict or set(origin) != {
            "features",
            "legal_arms",
            "selection",
        }:
            raise ValueError(
                "A policy origin must retain its exact observation and choice"
            )
        self._features(origin["features"])
        legal = origin["legal_arms"]
        selection = origin["selection"]
        if (
            not isinstance(legal, list)
            or not legal
            or len(set(legal)) != len(legal)
            or any(a not in self.arms for a in legal)
            or type(selection) is not dict
            or selection.get("decision_id") != decision_id
            or selection.get("arm") not in legal
            or selection.get("features_sha256") != digest(origin["features"])
            or selection.get("source_id") != self.configuration["source_id"]
            or set(selection)
            != {
                "schema",
                "source_id",
                "decision_id",
                "ordinal",
                "arm",
                "legal_arms",
                "values",
                "spike_counts",
                "reason",
                "features_sha256",
                "model_sha256",
            }
            or selection["schema"] != VERSION
            or selection["legal_arms"] != legal
            or not _integer(selection["ordinal"], 1, MAX_INTEGER)
            or type(selection["values"]) is not dict
            or set(selection["values"]) != set(legal)
            or any(not _finite(x, -1, 1) for x in selection["values"].values())
            or type(selection["spike_counts"]) is not list
            or len(selection["spike_counts"]) != self.configuration["width"]
            or any(
                not _integer(x, 0, self.configuration["ticks"])
                for x in selection["spike_counts"]
            )
            or selection["reason"]
            not in (
                "declared_initial_coverage",
                "declared_exploration",
                "learned_value",
            )
        ):
            raise ValueError(
                "Policy origin is not bound to its source and allowed choice"
            )
        _hash(selection["model_sha256"])

    def _validate_result(self, receipt):
        result = receipt["result"]
        if (
            type(result) is not dict
            or set(result)
            != {
                "schema",
                "seq",
                "decision_id",
                "arm",
                "receipt_id",
                "reward",
                "origin_features_sha256",
                "before_model_sha256",
                "after_model_sha256",
                "optimizer_step",
                "selected_regression_losses",
            }
            or result["schema"] != VERSION
            or not _integer(result["seq"], 1, MAX_INTEGER)
            or not _integer(result["optimizer_step"], 1, MAX_INTEGER)
            or result["arm"] != receipt["origin"]["selection"]["arm"]
            or result["origin_features_sha256"] != digest(receipt["origin"]["features"])
            or not _finite(result["reward"], -1, 1)
            or result["optimizer_step"] != result["seq"] * self.configuration["updates"]
            or type(result["selected_regression_losses"]) is not list
            or len(result["selected_regression_losses"])
            != self.configuration["updates"]
            or any(not _finite(x, 0, 4) for x in result["selected_regression_losses"])
        ):
            raise ValueError("A retained policy result is malformed")
        for key in ("receipt_id", "before_model_sha256", "after_model_sha256"):
            _hash(result[key])
        raw = {
            k: result[k] for k in ("seq", "decision_id", "arm", "receipt_id", "reward")
        }
        if digest(raw) != receipt["fingerprint"]:
            raise ValueError("A retained policy outcome fingerprint differs")

    def forward(self, batch):
        try:
            objects = np.asarray(batch, dtype=object)
        except (TypeError, ValueError):
            raise ValueError(
                "Policy forward inputs must form a numeric rectangle"
            ) from None
        if (
            objects.ndim != 2
            or not 1 <= len(objects) <= 128
            or objects.shape[1] != self.configuration["feature_dimension"]
        ):
            raise ValueError("Policy forward inputs must be a bounded normalized batch")
        x = _array(batch, objects.shape, bound=1)
        if np.any(x < 0):
            raise ValueError("Policy forward inputs must be normalized in [0,1]")
        current = x @ self.W + self.H
        if self.configuration["mechanism"] == "continuous":
            hidden = np.clip((current - 0.1) / 0.8, 0, 1)
            derivative = ((current > 0.1) & (current < 0.9)) / 0.8
            counts = np.zeros_like(hidden, dtype=np.int64)
        else:
            voltage, eligibility = np.zeros_like(current), np.zeros_like(current)
            counts, derivative = (
                np.zeros_like(current, dtype=np.int64),
                np.zeros_like(current),
            )
            for _ in range(self.configuration["ticks"]):
                voltage = 0.8 * voltage + current
                eligibility = 0.8 * eligibility + 1
                fired = voltage >= 1
                counts += fired
                derivative += (
                    np.minimum(1.0, 1 / (1 + 4 * np.abs(voltage - 1)) ** 2)
                    * eligibility
                )
                voltage -= fired
            hidden = counts / self.configuration["ticks"]
            derivative = np.clip(derivative / self.configuration["ticks"], 0, 1.5)
        values = np.tanh(hidden @ self.V + self.C)
        return values, hidden, derivative, counts

    def choose(self, features, decision_id, legal_arms):
        _hash(decision_id)
        x = self._features(features).tolist()
        if (
            type(legal_arms) is not list
            or not legal_arms
            or len(set(legal_arms)) != len(legal_arms)
            or any(arm not in self.arms for arm in legal_arms)
        ):
            raise ValueError(
                "A decision requires a nonempty declared legal-action mask"
            )
        legal = [arm for arm in self.arms if arm in legal_arms]
        previous = self.pending.get(decision_id)
        if previous is None:
            previous = next(
                (
                    r["origin"]
                    for r in self.receipts.values()
                    if r["result"]["decision_id"] == decision_id
                ),
                None,
            )
        if previous:
            if previous["features"] != x or previous["legal_arms"] != legal:
                raise ValueError("A previously committed policy request changed")
            return copy.deepcopy(previous["selection"])
        if len(self.pending) >= MAX_PENDING or self.choices >= MAX_INTEGER:
            raise ValueError("Policy pending decisions exceed their finite bound")
        values, _, _, counts = self.forward([x])
        indices = [self.arms.index(arm) for arm in legal]
        if self.choices < self.configuration["warmup"]:
            selected = min(indices, key=lambda i: (self.arm_counts[i], i))
            reason = "declared_initial_coverage"
        elif self.rng.random() < self.configuration["exploration"]:
            selected = indices[int(self.rng.integers(len(indices)))]
            reason = "declared_exploration"
        else:
            selected = max(indices, key=lambda i: (values[0, i], -i))
            reason = "learned_value"
        selection = {
            "schema": VERSION,
            "source_id": self.configuration["source_id"],
            "decision_id": decision_id,
            "ordinal": self.choices + 1,
            "arm": self.arms[selected],
            "legal_arms": legal,
            "values": {a: float(values[0, self.arms.index(a)]) for a in legal},
            "spike_counts": counts[0].astype(int).tolist(),
            "reason": reason,
            "features_sha256": digest(x),
            "model_sha256": self.model_hash(),
        }
        self.choices += 1
        self.arm_counts[selected] += 1
        self.pending[decision_id] = {
            "features": x,
            "legal_arms": legal,
            "selection": selection,
        }
        return copy.deepcopy(selection)

    def _update(self, records):
        x = np.asarray([r["features"] for r in records])
        actions = np.asarray([r["arm"] for r in records])
        rewards = np.asarray([r["reward"] for r in records])
        values, hidden, derivative, _ = self.forward(x)
        delta = np.zeros_like(values)
        chosen = values[np.arange(len(records)), actions]
        delta[np.arange(len(records)), actions] = (
            2 * (chosen - rewards) * (1 - chosen**2) / len(records)
        )
        upstream = (delta @ self.V.T) * derivative
        gradients = {
            "V": hidden.T @ delta,
            "C": delta.sum(axis=0),
            "W": x.T @ upstream,
            "H": upstream.sum(axis=0),
        }
        if self.configuration["frozen_input"]:
            gradients["W"].fill(0)
            gradients["H"].fill(0)
        norm = math.sqrt(sum(float(np.sum(g * g)) for g in gradients.values()))
        self.optimizer_step += 1
        for key, gradient in gradients.items():
            if key in ("W", "H") and self.configuration["frozen_input"]:
                continue
            gradient = gradient / max(1.0, norm)
            self.first[key] = 0.9 * self.first[key] + 0.1 * gradient
            self.second[key] = 0.999 * self.second[key] + 0.001 * gradient**2
            mean = self.first[key] / (1 - 0.9**self.optimizer_step)
            variance = self.second[key] / (1 - 0.999**self.optimizer_step)
            value = getattr(self, key) - self.configuration["learning_rate"] * mean / (
                np.sqrt(variance) + 1e-8
            )
            setattr(self, key, np.clip(value, -WEIGHT_BOUND, WEIGHT_BOUND))
        return float(np.mean((chosen - rewards) ** 2))

    def learn(self, receipt):
        if (
            type(receipt) is not dict
            or set(receipt) != {"seq", "decision_id", "arm", "receipt_id", "reward"}
            or not _integer(receipt["seq"], 1, MAX_INTEGER)
            or not _finite(receipt["reward"], -1, 1)
        ):
            raise ValueError(
                "Only a bounded selected-action outcome may train the policy"
            )
        decision_id, receipt_id = (
            _hash(receipt["decision_id"]),
            _hash(receipt["receipt_id"]),
        )
        fingerprint, seq = digest(receipt), receipt["seq"]
        if seq <= self.feedback_cursor:
            old = self.receipts.get(str(seq))
            if old is None or old["fingerprint"] != fingerprint:
                raise ValueError(
                    "Feedback changed or is outside its retained receipt window"
                )
            return copy.deepcopy(old["result"])
        if seq != self.feedback_cursor + 1:
            raise ValueError("Policy feedback requires the next source-local sequence")
        if self.optimizer_step > MAX_INTEGER - self.configuration["updates"]:
            raise ValueError("Policy optimizer exhausted its exact counter range")
        origin = self.pending.get(decision_id)
        if (
            not origin
            or receipt["arm"] != origin["selection"]["arm"]
            or any(
                r["result"]["receipt_id"] == receipt_id for r in self.receipts.values()
            )
        ):
            raise ValueError("Outcome has no matching uncredited selected action")
        before = self.state()
        before_model = self.model_hash()
        try:
            self.replay.append(
                {
                    "features": origin["features"],
                    "arm": self.arms.index(receipt["arm"]),
                    "reward": float(receipt["reward"]),
                    "receipt_id": receipt_id,
                }
            )
            self.replay = self.replay[-self.configuration["replay_capacity"] :]
            losses = []
            for _ in range(self.configuration["updates"]):
                indices = self.rng.choice(
                    len(self.replay),
                    size=min(self.configuration["batch_size"], len(self.replay)),
                    replace=False,
                )
                losses.append(self._update([self.replay[int(i)] for i in indices]))
            result = {
                "schema": VERSION,
                "seq": seq,
                "decision_id": decision_id,
                "arm": receipt["arm"],
                "receipt_id": receipt_id,
                "reward": receipt["reward"],
                "origin_features_sha256": digest(origin["features"]),
                "before_model_sha256": before_model,
                "after_model_sha256": self.model_hash(),
                "optimizer_step": self.optimizer_step,
                "selected_regression_losses": losses,
            }
            self.pending.pop(decision_id)
            self.feedback_cursor = seq
            self.receipts[str(seq)] = {
                "fingerprint": fingerprint,
                "origin": origin,
                "result": result,
            }
            while len(self.receipts) > RECEIPT_WINDOW:
                self.receipts.pop(str(min(map(int, self.receipts))))
            self.state()  # Check finite JSON before returning an update.
            return copy.deepcopy(result)
        except BaseException:
            self._restore(before)
            raise

    def model_hash(self):
        return digest(
            {
                "schema": VERSION,
                "configuration": self.configuration,
                "weights": {k: getattr(self, k).tolist() for k in PARAMETERS},
            }
        )

    def state(self):
        state = {
            "schema": VERSION,
            "configuration": self.configuration,
            "weights": {k: getattr(self, k).tolist() for k in PARAMETERS},
            "optimizer": {
                "step": self.optimizer_step,
                "first": {k: v.tolist() for k, v in self.first.items()},
                "second": {k: v.tolist() for k, v in self.second.items()},
            },
            "rng": self.rng.bit_generator.state,
            "choices": self.choices,
            "arm_counts": self.arm_counts,
            "feedback_cursor": self.feedback_cursor,
            "pending": self.pending,
            "receipts": self.receipts,
            "replay": self.replay,
        }
        return json.loads(canonical(state))

    def state_hash(self):
        return digest(self.state())
