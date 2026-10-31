"""Outcome-trained acquisition from a strictly limited sentinel observation.

The policy reads four cheap unit counts, then requests twelve other records from
the same completed window. A separate evaluator owns the unobserved counts.
This is recorded-data selection, not stimulation or future prediction.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from cognition.biology import digest, write_new
from cognition.nwb_spikes import SOURCE, validate_binned

SENTINELS = (0, 12, 24, 36)
EXTRAS = 12
TRAIN, DEVELOPMENT, TOTAL = 320, 480, 720
RIDGES = (0.01, 1.0, 100.0)
NEIGHBORS = (3, 9, 27)
RULES = {
    "version": 1,
    "source": SOURCE,
    "bin_seconds": 0.25,
    "partition_seconds": [[0, 80], [80, 120], [120, 180]],
    "sentinels": list(SENTINELS),
    "extra_records": EXTRAS,
    "inputs": "Current and four preceding sentinel windows; no extra-unit observations or future counts",
    "action": "Select twelve extra unit records from the same completed window after reading four sentinels",
    "target": "Unit-specific log1p counts; utility is exact acquired spike entries",
    "ridge_strengths": list(RIDGES),
    "neighbor_choices": list(NEIGHBORS),
    "normalization": "Train-only mean and standard deviation; no test fitting",
    "score_ties": "Round acquisition scores to nine decimal places, then prefer smaller unit ID",
    "selection": "Maximum validation acquired spike entries, then ascending method name and parameter; report all methods",
    "neural": {
        "seed": 7321,
        "neurons": 64,
        "dt_ms": 1,
        "window_ms": 250,
        "tau_ms": [50, 250, 1000],
        "refractory_ms": 4,
        "bias": 0.65,
        "drive": "1.7*tanh(log1p(sentinel_count)/3) into sixteen fixed neurons per sentinel",
        "connection_probability": 0.12,
        "excitation": 0.08,
        "inhibition": 0.06,
        "synaptic_tau_ms": [40, 80],
        "features": "64 per-window spike counts and 64 end-window membrane values",
        "learning": "Training outcomes fit the decoder only; no label or reward enters the reservoir",
    },
    "wall_seconds": 240,
    "scope": "One short recorded organoid; offline acquisition, no live tissue, causal connectivity, biological learning, general intelligence or consciousness claim",
}


def sentinel_values(value):
    values = np.asarray(value, dtype=float)
    if (
        values.shape != (4,)
        or not np.isfinite(values).all()
        or np.any(values < 0)
        or np.any(values != np.floor(values))
        or np.any(values > 100000)
    ):
        raise ValueError("Only four finite nonnegative sentinel counts are accepted")
    return values


class SentinelHistory:
    """A public observation boundary with no whole-window input argument."""

    def __init__(self):
        self.history = np.zeros((5, 4))

    def observe(self, four_counts):
        values = sentinel_values(four_counts)
        self.history[:-1] = self.history[1:]
        self.history[-1] = np.log1p(values)
        return self.history.ravel().copy()


def history_features(sentinels):
    state = SentinelHistory()
    return np.asarray([state.observe(row) for row in sentinels])


def feature_map(history, neural, method):
    history = np.asarray(history, dtype=float)
    if method == "linear_history":
        return history
    if method == "quadratic_history":
        i, j = np.triu_indices(history.shape[1])
        return np.column_stack((history, history[:, i] * history[:, j]))
    if method == "history_plus_spiking":
        neural = np.asarray(neural, dtype=float)
        if neural.shape != (len(history), 128) or not np.isfinite(neural).all():
            raise ValueError("128 measured neural features required per window")
        return np.column_stack((history, neural))
    raise ValueError("Unknown observation feature map")


def fit_ridge(x, targets, strength):
    """Conventional multi-output ridge; observations determine the decoder."""
    x, targets = np.asarray(x, dtype=float), np.asarray(targets, dtype=float)
    if (
        x.ndim != 2
        or targets.shape != (len(x), 49)
        or not np.isfinite(x).all()
        or not np.isfinite(targets).all()
        or strength not in RIDGES
    ):
        raise ValueError(
            "Finite training features, 49 targets and fixed ridge required"
        )
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1e-9)
    a = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    penalty = np.eye(a.shape[1]) * strength
    penalty[0, 0] = 0
    weights = np.linalg.solve(a.T @ a + penalty, a.T @ targets)
    return {
        "strength": strength,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "weights": weights.tolist(),
    }


def predict_ridge(fit, x):
    x = np.asarray(x, dtype=float)
    mean, scale, weights = (np.asarray(fit[k]) for k in ("mean", "scale", "weights"))
    if (
        x.ndim != 2
        or mean.shape != (x.shape[1],)
        or scale.shape != mean.shape
        or weights.shape != (len(mean) + 1, 49)
        or not all(np.isfinite(v).all() for v in (x, mean, scale, weights))
        or np.any(scale <= 0)
    ):
        raise ValueError("Invalid retained readout")
    return np.column_stack((np.ones(len(x)), (x - mean) / scale)) @ weights


def choose_units(scores):
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (49,) or not np.isfinite(scores).all():
        raise ValueError("49 finite acquisition scores required")
    scores = np.round(scores, 9)
    candidates = np.array([i for i in range(49) if i not in SENTINELS])
    return candidates[np.lexsort((candidates, -scores[candidates]))[:EXTRAS]]


def capture(counts, actions):
    counts, actions = np.asarray(counts), np.asarray(actions)
    if (
        counts.ndim != 2
        or counts.shape[1] != 49
        or actions.shape != (len(counts), EXTRAS)
        or actions.dtype.kind not in "iu"
        or np.any(actions < 0)
        or np.any(actions >= 49)
        or any(len(set(row)) != EXTRAS or set(row) & set(SENTINELS) for row in actions)
    ):
        raise ValueError(
            "Exactly twelve distinct nonsentinel units per window required"
        )
    acquired = counts[:, SENTINELS].sum(axis=1) + np.take_along_axis(
        counts, actions, axis=1
    ).sum(axis=1)
    total = int(counts.sum())
    return {
        "acquired_spikes": int(acquired.sum()),
        "total_spikes": total,
        "fraction": float(acquired.sum() / total) if total else None,
        "unit_records_read": len(counts) * (len(SENTINELS) + EXTRAS),
        "available_unit_records": len(counts) * 49,
        "per_window_acquired": acquired.tolist(),
        "actions": actions.tolist(),
    }


def candidate_predictions(method, history, neural, training, fit=None, neighbors=None):
    if method == "static_rates":
        return np.tile(np.asarray(training).mean(axis=0), (len(history), 1))
    if method == "sentinel_knn":
        if neighbors not in NEIGHBORS:
            raise ValueError("Unknown nearest-neighbor parameter")
        reference = np.asarray(fit["reference"])
        targets = np.asarray(fit["targets"])
        scale = np.asarray(fit["scale"])
        distances = (((history[:, None, :] - reference) / scale) ** 2).sum(axis=2)
        indices = np.argsort(distances, axis=1, kind="stable")[:, :neighbors]
        return targets[indices].mean(axis=1)
    return predict_ridge(fit, feature_map(history, neural, method))


def select(development_counts, neural):
    """Accept exactly development data. A later interval cannot affect selection."""
    counts = np.asarray(development_counts)
    if counts.shape != (DEVELOPMENT, 49) or np.asarray(neural).shape != (
        DEVELOPMENT,
        128,
    ):
        raise ValueError("Selection accepts only the 480 development windows")
    history = history_features(counts[:, SENTINELS])
    targets = np.log1p(counts[:TRAIN])
    validation = counts[TRAIN:]
    methods = {}
    candidates = [i for i in range(49) if i not in SENTINELS]
    rotating = np.asarray(
        [
            [candidates[(i * EXTRAS + j) % len(candidates)] for j in range(EXTRAS)]
            for i in range(TRAIN, DEVELOPMENT)
        ]
    )
    methods["rotating"] = {
        "fit": None,
        "parameter": None,
        "validation": capture(validation, rotating),
    }
    for method in (
        "static_rates",
        "sentinel_knn",
        "linear_history",
        "quadratic_history",
        "history_plus_spiking",
    ):
        trials = []
        parameters = (
            NEIGHBORS
            if method == "sentinel_knn"
            else ((0,) if method == "static_rates" else RIDGES)
        )
        for parameter in parameters:
            if method == "static_rates":
                fit = None
            elif method == "sentinel_knn":
                fit = {
                    "reference": history[:TRAIN].tolist(),
                    "targets": targets.tolist(),
                    "scale": np.maximum(history[:TRAIN].std(axis=0), 1e-9).tolist(),
                }
            else:
                fit = fit_ridge(
                    feature_map(history, neural, method)[:TRAIN], targets, parameter
                )
            predictions = candidate_predictions(
                method,
                history[TRAIN:],
                np.asarray(neural)[TRAIN:],
                counts[:TRAIN],
                fit,
                parameter,
            )
            actions = np.asarray([choose_units(row) for row in predictions])
            observed = capture(validation, actions)
            trials.append({"parameter": parameter, "fit": fit, "validation": observed})
        selected = min(
            trials,
            key=lambda item: (
                -item["validation"]["acquired_spikes"],
                item["parameter"],
            ),
        )
        methods[method] = selected | {
            "parameter_trials": [
                {
                    "parameter": v["parameter"],
                    "acquired_spikes": v["validation"]["acquired_spikes"],
                }
                for v in trials
            ]
        }

    def key(name):
        return (-methods[name]["validation"]["acquired_spikes"], name)

    return {
        "schema": 1,
        "rules_sha256": digest(RULES),
        "development_counts_sha256": digest(counts.tolist()),
        "development_neural_sha256": digest(np.asarray(neural).tolist()),
        "methods": methods,
        "selected_method": min(methods, key=key),
        "selected_nonspiking_baseline": min(
            (name for name in methods if name != "history_plus_spiking"), key=key
        ),
    }


class SentinelReservoir:
    """Small task-specific reservoir; the original AdaptiveBrain is untouched."""

    def __init__(self):
        import brian2 as b

        self.b = b
        b.start_scope()
        b.prefs.codegen.target = "numpy"
        b.prefs.logging.delete_log_on_exit = False
        b.defaultclock.dt = b.ms
        rng = np.random.default_rng(RULES["neural"]["seed"])
        self.cells = b.NeuronGroup(
            64,
            """
            dv/dt = (0.65 + drive + ge - gi - v)/(tau*ms) : 1 (unless refractory)
            dge/dt = -ge/(40*ms) : 1
            dgi/dt = -gi/(80*ms) : 1
            drive : 1
            tau : 1
        """,
            threshold="v > 1",
            reset="v = 0",
            refractory=4 * b.ms,
            method="euler",
        )
        self.cells.tau = np.resize([50, 250, 1000], 64)
        self.cells.v = 0
        self.excitation = b.Synapses(self.cells, self.cells, on_pre="ge_post += 0.08")
        i, j = np.where((rng.random((64, 64)) < 0.12) & ~np.eye(64, dtype=bool))
        self.excitation.connect(i=i[i < 48], j=j[i < 48])
        self.inhibition = b.Synapses(self.cells, self.cells, on_pre="gi_post += 0.06")
        self.inhibition.connect(i=i[i >= 48], j=j[i >= 48])
        self.network = b.Network(self.cells, self.excitation, self.inhibition)
        self.windows = 0

    def observe(self, four_counts):
        values = sentinel_values(four_counts)
        self.cells.drive = np.repeat(1.7 * np.tanh(np.log1p(values) / 3), 16)
        monitor = self.b.SpikeMonitor(self.cells)
        self.network.add(monitor)
        try:
            self.network.run(250 * self.b.ms, namespace={})
            result = np.r_[
                np.asarray(monitor.count[:], dtype=float),
                np.asarray(self.cells.v[:], dtype=float),
            ]
        finally:
            self.network.remove(monitor)
        self.windows += 1
        return result

    def state_sha256(self):
        return digest(
            {
                key: np.asarray(getattr(self.cells, key)[:]).tolist()
                for key in ("v", "ge", "gi", "drive", "tau")
            }
            | {"windows": self.windows}
        )


class RecordPicker:
    """Execute a frozen learned policy one four-count observation at a time.

    Start at the recording's first window, including its declared warm-up. This
    object has no argument for the twelve acquired records or any future data.
    It grants no provider access and performs no network requests.
    """

    def __init__(self, selection, development_counts):
        if selection["rules_sha256"] != digest(RULES):
            raise ValueError("Unrecognized acquisition policy")
        development = np.asarray(development_counts)
        if (
            development.shape != (DEVELOPMENT, 49)
            or digest(development.tolist()) != selection["development_counts_sha256"]
        ):
            raise ValueError("The exact original development recording is required")
        self.selection = selection
        self.method = selection["selected_method"]
        if self.method not in selection["methods"]:
            raise ValueError("Missing selected acquisition method")
        self.record = selection["methods"][self.method]
        self.training = development[:TRAIN].copy()
        self.identity = digest(selection)
        self.history = SentinelHistory()
        self.reservoir = (
            SentinelReservoir() if self.method == "history_plus_spiking" else None
        )
        self.window = 0

    def observe(self, four_counts):
        if self.window >= TOTAL:
            raise ValueError("The enrolled 720-window recording has ended")
        h = self.history.observe(four_counts)[None, :]
        n = self.reservoir.observe(four_counts)[None, :] if self.reservoir else None
        if self.method == "rotating":
            candidates = [i for i in range(49) if i not in SENTINELS]
            actions = [
                candidates[(self.window * EXTRAS + j) % len(candidates)]
                for j in range(EXTRAS)
            ]
        else:
            predicted = candidate_predictions(
                self.method,
                h,
                n,
                self.training,
                self.record["fit"],
                self.record["parameter"],
            )[0]
            actions = choose_units(predicted).tolist()
        result = {
            "window": self.window,
            "policy_sha256": self.identity,
            "sentinel_unit_ids": list(SENTINELS),
            "extra_unit_ids": actions,
        }
        self.window += 1
        return result


def evaluate(binned, neural, selection):
    counts = validate_binned(binned)
    if (
        counts.shape != (TOTAL, 49)
        or binned["bin_seconds"] != 0.25
        or binned["unit_ids"] != list(range(49))
    ):
        raise ValueError("Exactly 180 recorded seconds required")
    if selection["rules_sha256"] != digest(RULES) or selection[
        "development_counts_sha256"
    ] != digest(counts[:DEVELOPMENT].tolist()):
        raise ValueError("Frozen development or protocol changed")
    neural = np.asarray(neural)
    if neural.shape != (TOTAL, 128) or selection["development_neural_sha256"] != digest(
        neural[:DEVELOPMENT].tolist()
    ):
        raise ValueError("Frozen development neural features changed")
    history = history_features(counts[:, SENTINELS])
    targets = counts[DEVELOPMENT:]
    candidates = [i for i in range(49) if i not in SENTINELS]
    methods = {}
    for method, record in selection["methods"].items():
        if method == "rotating":
            actions = np.asarray(
                [
                    [
                        candidates[(i * EXTRAS + j) % len(candidates)]
                        for j in range(EXTRAS)
                    ]
                    for i in range(DEVELOPMENT, TOTAL)
                ]
            )
        else:
            predictions = candidate_predictions(
                method,
                history[DEVELOPMENT:],
                neural[DEVELOPMENT:],
                counts[:TRAIN],
                record["fit"],
                record["parameter"],
            )
            actions = np.asarray([choose_units(row) for row in predictions])
        methods[method] = capture(targets, actions)
    return {
        "schema": 1,
        "rules_sha256": digest(RULES),
        "source_sha256": digest(binned),
        "selection_sha256": digest(selection),
        "selected_method": selection["selected_method"],
        "selected_nonspiking_baseline": selection["selected_nonspiking_baseline"],
        "test_windows": TOTAL - DEVELOPMENT,
        "records_read_fraction": (len(SENTINELS) + EXTRAS) / 49,
        "methods": methods,
        "scope": RULES["scope"],
    }


def run(nwb, development_path, directory):
    """Freeze validation choices before importing/scoring the later recording."""
    from cognition.nwb_spikes import import_nwb

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    development = json.loads(Path(development_path).read_bytes())
    counts = validate_binned(development)
    if counts.shape != (DEVELOPMENT, 49) or development["bin_seconds"] != 0.25:
        raise ValueError("Use the unchanged first-120-second development derivative")
    write_new(
        directory / "protocol.json",
        {
            "rules": RULES,
            "rules_sha256": digest(RULES),
            "development_sha256": digest(development),
        },
    )
    started = time.monotonic()
    reservoir = SentinelReservoir()
    initial = reservoir.state_sha256()
    neural = []

    def advance(rows):
        for row in rows:
            if time.monotonic() - started > RULES["wall_seconds"]:
                write_new(
                    directory / "interrupted.json",
                    {
                        "windows": len(neural),
                        "wall_seconds": time.monotonic() - started,
                    },
                )
                raise TimeoutError("Acquisition experiment wall admission exhausted")
            neural.append(reservoir.observe(row[list(SENTINELS)]).tolist())

    advance(counts)
    selected = select(counts, neural)
    write_new(directory / "selection.json", selected)
    # Only the independently evaluated later interval is opened after selection.
    import_nwb(nwb, directory / "binned.json", seconds=180)
    binned = json.loads((directory / "binned.json").read_bytes())
    full = validate_binned(binned)
    if not np.array_equal(full[:DEVELOPMENT], counts):
        raise ValueError("Original development counts changed")
    advance(full[DEVELOPMENT:])
    write_new(
        directory / "neural.json",
        {
            "features": neural,
            "initial_state_sha256": initial,
            "final_state_sha256": reservoir.state_sha256(),
            "windows": len(neural),
        },
    )
    result = evaluate(binned, neural, selected)
    result["wall_seconds"] = time.monotonic() - started
    write_new(directory / "result.json", result)
    return {
        "selected_method": result["selected_method"],
        "selected_nonspiking_baseline": result["selected_nonspiking_baseline"],
        "test": {
            name: {
                k: v
                for k, v in value.items()
                if k not in ("actions", "per_window_acquired")
            }
            for name, value in result["methods"].items()
        },
        "wall_seconds": result["wall_seconds"],
    }


def verify(directory):
    """Reconstruct fits, all model selection, actions and acquired outcomes."""
    directory = Path(directory)

    def read(name):
        return json.loads((directory / (name + ".json")).read_bytes())

    protocol, selection, binned, neural, result = map(
        read, ("protocol", "selection", "binned", "neural", "result")
    )
    if protocol["rules"] != RULES or protocol["rules_sha256"] != digest(RULES):
        raise ValueError("Recorded rules changed")
    original_development = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "research/biology/dandi-01/binned.json"
        ).read_bytes()
    )
    if protocol["development_sha256"] != digest(original_development):
        raise ValueError("Original development metadata changed")
    counts = validate_binned(binned)
    expected = select(
        counts[:DEVELOPMENT], np.asarray(neural["features"])[:DEVELOPMENT]
    )
    if (
        set(expected) != set(selection)
        or expected["selected_method"] != selection["selected_method"]
    ):
        raise ValueError("Selection evidence changed")

    # Numeric fits can differ at floating-point rounding across BLAS versions.
    def compare(a, b):
        if isinstance(a, dict):
            return (
                isinstance(b, dict)
                and set(a) == set(b)
                and all(compare(a[k], b[k]) for k in a)
            )
        if isinstance(a, list):
            return (
                isinstance(b, list)
                and len(a) == len(b)
                and all(compare(x, y) for x, y in zip(a, b))
            )
        if type(a) is float:
            return type(b) in (int, float) and np.isclose(a, b, rtol=1e-8, atol=1e-9)
        return a == b

    if not compare(expected, selection):
        raise ValueError("Training fits or validation results do not reproduce")
    checked = evaluate(binned, neural["features"], selection)
    if any(
        not compare(value, result.get(key)) for key, value in checked.items()
    ) or set(result) != set(checked) | {"wall_seconds"}:
        raise ValueError("Retained test actions or outcomes changed")
    return {
        "verified": True,
        "selected_method": checked["selected_method"],
        "scope": "Independent readout, selection and acquired-count replay; not original biological provenance or regeneration of Brian2 features",
    }
