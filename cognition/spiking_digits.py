"""Outcome-trained, bounded digit transcription using hard spike counts.

This task-specific NumPy model is separate from the retained 1,024-cell Brian2
circuit. Its input and output weights learn through conventional Adam and an
explicit surrogate gradient. It does not model human neurons or establish a
special computational advantage of spikes over continuous activations.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np

VERSION = "nuria-spiking-digits-v1"
PARAMETERS = ("W", "H", "V", "C")
MAX_BATCH = 4096
MAX_STEPS = 2**53 - 1
WEIGHT_BOUND = 100.0


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _array(value, shape, bound):
    try:
        result = np.asarray(value)
    except (TypeError, ValueError):
        raise ValueError("Digit model array is malformed") from None
    if (
        result.shape != shape
        or result.dtype.kind not in "fiu"
        or not np.isfinite(result).all()
        or np.any(np.abs(result) > bound)
    ):
        raise ValueError("Digit model array exceeds its finite numerical bounds")
    return result.astype(float, copy=True)


class SpikingDigits:
    """Classify one bounded 8 by 8 image; verified labels train actual weights.

    The spiking branch executes sixteen discrete leaky integrate-and-fire ticks
    by default. Each cell emits at most one spike per tick, subtracts one from
    membrane potential on firing, and contributes its spike fraction to the ten
    output logits. The continuous comparison uses the declared clipped-rate
    approximation instead. Input-frozen controls may train only the output head.

    Checkpoints are JSON values with explicit configuration identity. Restoring
    with a different seed, architecture or learning mechanism is refused. The
    caller must authenticate buyer labels before setting ``verified=True``;
    this numerical model does not itself authenticate external receipts.
    """

    def __init__(
        self,
        *,
        seed=202611,
        width=64,
        ticks=16,
        kind="snn",
        frozen_input=False,
        state=None,
    ):
        if (
            type(seed) is not int
            or not 0 <= seed < 2**32
            or type(width) is not int
            or not 1 <= width <= 256
            or type(ticks) is not int
            or not 1 <= ticks <= 64
            or kind not in ("snn", "continuous")
            or type(frozen_input) is not bool
        ):
            raise ValueError("Digit model configuration is invalid")
        self.configuration = {
            "seed": seed,
            "width": width,
            "ticks": ticks,
            "kind": kind,
            "frozen_input": frozen_input,
        }
        self.width, self.ticks = width, ticks
        self.kind, self.frozen_input = kind, frozen_input
        self._shapes = {
            "W": (64, width),
            "H": (width,),
            "V": (width, 10),
            "C": (10,),
        }
        if state is None:
            rng = np.random.default_rng(seed)
            self.W = rng.normal(0, np.sqrt(2 / 64), (64, width))
            self.H = np.full(width, 0.15)
            self.V = rng.normal(0, 0.1, (width, 10))
            self.C = np.zeros(10)
            self.step = 0
            self._first = {key: np.zeros(shape) for key, shape in self._shapes.items()}
            self._second = {key: value.copy() for key, value in self._first.items()}
        else:
            self._restore(state)

    def _restore(self, state):
        if (
            not isinstance(state, dict)
            or set(state) != {"schema", "configuration", "weights", "optimizer"}
            or state["schema"] != VERSION
            or state["configuration"] != self.configuration
            or not isinstance(state["weights"], dict)
            or set(state["weights"]) != set(PARAMETERS)
        ):
            raise ValueError("Digit checkpoint identity or fields differ")
        if any(
            type(state["configuration"][key]) is not type(value)
            for key, value in self.configuration.items()
        ):
            raise ValueError("Digit checkpoint configuration types differ")
        optimizer = state["optimizer"]
        if (
            not isinstance(optimizer, dict)
            or set(optimizer) != {"step", "first_moment", "second_moment"}
            or type(optimizer["step"]) is not int
            or not 0 <= optimizer["step"] <= MAX_STEPS
            or any(
                not isinstance(optimizer[key], dict)
                or set(optimizer[key]) != set(PARAMETERS)
                for key in ("first_moment", "second_moment")
            )
        ):
            raise ValueError("Digit checkpoint optimizer is malformed")
        weights = {
            key: _array(state["weights"][key], shape, WEIGHT_BOUND)
            for key, shape in self._shapes.items()
        }
        first = {
            key: _array(optimizer["first_moment"][key], shape, 1.000000001)
            for key, shape in self._shapes.items()
        }
        second = {
            key: _array(optimizer["second_moment"][key], shape, 1.000000001)
            for key, shape in self._shapes.items()
        }
        if any(np.any(value < 0) for value in second.values()):
            raise ValueError("Digit checkpoint second moment cannot be negative")
        if optimizer["step"] == 0 and any(
            np.any(value) for bank in (first, second) for value in bank.values()
        ):
            raise ValueError("Untrained digit optimizer has nonzero moments")
        for key, value in weights.items():
            setattr(self, key, value)
        self._first, self._second = first, second
        self.step = optimizer["step"]

    @staticmethod
    def _batch(value):
        try:
            result = np.asarray(value)
        except (TypeError, ValueError):
            raise ValueError("Digit batch is malformed") from None
        if (
            result.ndim != 2
            or result.shape[1] != 64
            or not 1 <= len(result) <= MAX_BATCH
            or result.dtype.kind not in "fiu"
            or not np.isfinite(result).all()
            or np.any(result < 0)
            or np.any(result > 1)
        ):
            raise ValueError("Digit batch must contain normalized bounded images")
        return result.astype(float, copy=False)

    def forward(self, batch):
        """Return logits, hidden activations, surrogate derivative and spikes."""
        x = self._batch(batch)
        current = x @ self.W + self.H
        if self.kind == "continuous":
            hidden = np.clip((current - 0.1) / 0.8, 0, 1)
            derivative = ((current > 0.1) & (current < 0.9)) / 0.8
            return hidden @ self.V + self.C, hidden, derivative, 0
        membrane = np.zeros_like(current)
        counts = np.zeros_like(current)
        derivative = np.zeros_like(current)
        eligibility = np.zeros_like(current)
        for _ in range(self.ticks):
            membrane = 0.8 * membrane + current
            eligibility = 0.8 * eligibility + 1
            spikes = (membrane >= 1).astype(float)
            counts += spikes
            derivative += (
                np.minimum(1.0, 1 / (1 + 4 * np.abs(membrane - 1)) ** 2) * eligibility
            )
            membrane -= spikes
        hidden = counts / self.ticks
        # A clipped, declared straight-through surrogate, not an exact gradient
        # of the discontinuous threshold or a biological learning claim.
        derivative = np.clip(derivative / self.ticks, 0, 1.5)
        return (
            hidden @ self.V + self.C,
            hidden,
            derivative,
            int(counts.sum()),
        )

    def model_weights(self):
        return {key: getattr(self, key).tolist() for key in PARAMETERS}

    def state(self):
        return {
            "schema": VERSION,
            "configuration": dict(self.configuration),
            "weights": self.model_weights(),
            "optimizer": {
                "step": self.step,
                "first_moment": {k: v.tolist() for k, v in self._first.items()},
                "second_moment": {k: v.tolist() for k, v in self._second.items()},
            },
        }

    def model_hash(self):
        return _hash(
            {
                "schema": VERSION,
                "configuration": self.configuration,
                "weights": self.model_weights(),
            }
        )

    def state_hash(self):
        return _hash(self.state())

    def predict(self, pixels):
        if (
            not isinstance(pixels, list)
            or len(pixels) != 64
            or any(type(v) is not int or not 0 <= v <= 16 for v in pixels)
        ):
            raise ValueError("A digit requires exactly 64 integer pixels in 0..16")
        logits, hidden, _, spikes = self.forward([np.asarray(pixels) / 16])
        probabilities = np.exp(logits[0] - logits[0].max())
        probabilities /= probabilities.sum()
        counts = (
            np.rint(hidden[0] * self.ticks).astype(int).tolist()
            if self.kind == "snn"
            else [0] * self.width
        )
        return {
            "schema": VERSION,
            "label": int(logits[0].argmax()),
            "logits": logits[0].tolist(),
            "probabilities": probabilities.tolist(),
            "spike_counts": counts,
            "total_spikes": spikes,
            "model_sha256": self.model_hash(),
            "state_sha256": self.state_hash(),
        }

    def train_batch(
        self,
        batch,
        labels,
        *,
        verified=False,
        learning_rate=0.003,
        l2=1e-5,
        record_hashes=True,
    ):
        """Learn from authenticated, already delivered label outcomes.

        This is supervised cross-entropy training, not selected-action-only
        bandit feedback. The surrounding service must commit a prediction before
        accepting its buyer label and prevent duplicate receipt application.
        """
        if (
            verified is not True
            or not _finite(learning_rate)
            or not 0 < learning_rate <= 0.1
            or not _finite(l2)
            or not 0 <= l2 <= 1
            or type(record_hashes) is not bool
            or self.step >= MAX_STEPS
        ):
            raise ValueError("Verified labels and bounded learning terms required")
        x = self._batch(batch)
        try:
            y = np.asarray(labels)
        except (TypeError, ValueError):
            raise ValueError("Digit outcome labels are malformed") from None
        if (
            y.shape != (len(x),)
            or y.dtype.kind not in "iu"
            or np.any(y < 0)
            or np.any(y > 9)
        ):
            raise ValueError("Digit outcomes require integer labels in 0..9")
        logits, hidden, derivative, _ = self.forward(x)
        shifted = logits - logits.max(1, keepdims=True)
        probabilities = np.exp(shifted)
        probabilities /= probabilities.sum(1, keepdims=True)
        loss = float(
            np.mean(np.log(np.exp(shifted).sum(1)) - shifted[np.arange(len(y)), y])
        )
        error = probabilities.copy()
        error[np.arange(len(y)), y] -= 1
        error /= len(y)
        gradients = {
            "V": hidden.T @ error + l2 * self.V,
            "C": error.sum(0),
        }
        if not self.frozen_input:
            hidden_error = (error @ self.V.T) * derivative
            gradients["W"] = x.T @ hidden_error + l2 * self.W
            gradients["H"] = hidden_error.sum(0)
        before = self.model_hash() if record_hashes else None
        step = self.step + 1
        updates, first, second = {}, {}, {}
        for key, gradient in gradients.items():
            gradient = np.clip(gradient, -1, 1)
            first[key] = 0.9 * self._first[key] + 0.1 * gradient
            second[key] = 0.999 * self._second[key] + 0.001 * gradient**2
            update = learning_rate * (first[key] / (1 - 0.9**step))
            update /= np.sqrt(second[key] / (1 - 0.999**step)) + 1e-8
            updates[key] = getattr(self, key) - update
            _array(updates[key], self._shapes[key], WEIGHT_BOUND)
        changed = {
            key: int(np.count_nonzero(updates[key] != getattr(self, key)))
            for key in updates
        }
        # Apply only after every proposed array passes, keeping rejection atomic.
        for key, value in updates.items():
            setattr(self, key, value)
            self._first[key], self._second[key] = first[key], second[key]
        self.step = step
        return {
            "verified_labels": len(y),
            "cross_entropy_before": loss,
            "optimizer_step": step,
            "changed_weights": changed,
            "before_model_sha256": before,
            "after_model_sha256": self.model_hash() if record_hashes else None,
        }
