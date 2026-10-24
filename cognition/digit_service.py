"""Private, durable digit decisions and source-bound checked outcome learning.

No network, wallet, filesystem input paths or executable model artifacts enter
this API. Model state is reviewed JSON. A training-label commitment belongs to
the trusted local dataset registrar; it establishes immutable prior authority,
not independent evidence that its label is true. Qualification requests cannot
train. Every prediction is committed before any buyer comparison is accepted.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import inspect
import json
import math
import os
import re
import sqlite3
from pathlib import Path

import numpy as np

VERSION = "nuria.digit-service.v1"
TRAINING_RECEIPT = "nuria.digit-training-receipt.v1"
LABEL_COMMITMENT = "nuria.digit-label-commitment.v1"
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_JSON_BYTES = 64 * 1024 * 1024


def canonical(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > MAX_JSON_BYTES:
        raise ValueError("Digit service JSON exceeds its bounded state size")
    return raw


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def model_digest(state):
    return digest({key: state[key] for key in ("schema", "configuration", "weights")})


def pixels(value):
    if (
        type(value) is not list
        or len(value) != 64
        or any(type(item) is not int or not 0 <= item <= 16 for item in value)
    ):
        raise ValueError("A digit requires exactly 64 integer pixels in 0..16")
    return list(value)


def _identity(value, name):
    if not isinstance(value, str) or not SOURCE.fullmatch(value):
        raise ValueError(f"Digit {name} identity is invalid")
    return value


def _hash(value, name):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError(f"Digit {name} commitment is invalid")
    return value


def _label(value):
    if type(value) is not int or not 0 <= value <= 9:
        raise ValueError("Digit labels must be integers in 0..9")
    return value


def label_commitment(source_id, request_id, input_sha256, label, nonce):
    """Register source/request/input-bound label authority without exposing it."""
    return digest(
        {
            "schema": LABEL_COMMITMENT,
            "source_id": _identity(source_id, "source"),
            "request_id": _identity(request_id, "request"),
            "input_sha256": _hash(input_sha256, "input"),
            "label": _label(label),
            "nonce": _hash(nonce, "label nonce"),
        }
    )


def _model_type():
    from cognition.spiking_digits import SpikingDigits

    return SpikingDigits


class DigitService:
    """One private source, dataset partition, model identity and SQLite writer.

    Existing state requires the original reviewed model, exact source/dataset,
    partition, training authority and implementation. Interrupted computations
    may repeat, but a committed decision and training receipt never change.
    Model weights, optimizer, receipt and journal update in one transaction.
    """

    def __init__(
        self,
        directory,
        source_id,
        reviewed_model,
        *,
        dataset_sha256,
        partition="qualification",
        training_enabled=False,
    ):
        self.source_id = _identity(source_id, "source")
        self.dataset_sha256 = _hash(dataset_sha256, "dataset")
        if partition not in ("training", "qualification"):
            raise ValueError("Digit dataset partition is unsupported")
        if (
            type(training_enabled) is not bool
            or training_enabled
            and partition != "training"
        ):
            raise ValueError("Qualification services cannot enable training")
        if (
            type(reviewed_model) is not dict
            or type(reviewed_model.get("configuration")) is not dict
        ):
            raise ValueError(
                "A reviewed JSON model with explicit configuration is required"
            )
        self.partition = partition
        self.training_enabled = training_enabled
        self._factory = _model_type()
        initial = json.loads(canonical(reviewed_model))
        self.model = self._new_model(initial)
        self.directory = Path(directory)
        self.closed = False
        self.db = None
        self.lock = None
        self.terms = {
            "schema": VERSION,
            "source_id": self.source_id,
            "dataset_sha256": self.dataset_sha256,
            "partition": partition,
            "training_enabled": training_enabled,
            "reviewed_state_sha256": digest(initial),
            "reviewed_model_sha256": model_digest(initial),
            "configuration_sha256": digest(initial["configuration"]),
            "initial_optimizer_step": initial["optimizer"]["step"],
            "implementation_sha256": hashlib.sha256(
                Path(inspect.getfile(self._factory)).read_bytes()
            ).hexdigest(),
            "service_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "numpy_version": np.__version__,
            "financial_transactions": 0,
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker = self.directory / "DIGIT-SERVICE.json"
            if marker.exists():
                if json.loads(marker.read_text()) != self.terms:
                    raise ValueError(
                        "Existing digit history has another source or model identity"
                    )
            else:
                if any(self.directory.iterdir()):
                    raise ValueError(
                        "An unmarked directory cannot become a digit history"
                    )
                with marker.open("x") as stream:
                    os.chmod(marker, 0o600)
                    stream.write(canonical(self.terms))
                    stream.flush()
                    os.fsync(stream.fileno())
            self.lock = (self.directory / "writer.lock").open("a")
            os.chmod(self.directory / "writer.lock", 0o600)
            try:
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another writer owns this digit history") from None
            previous_umask = os.umask(0o077)
            try:
                self.db = sqlite3.connect(
                    self.directory / "digits.sqlite3", isolation_level=None
                )
            finally:
                os.umask(previous_umask)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), terms TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, state TEXT NOT NULL, state_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, pixels TEXT NOT NULL, purpose TEXT NOT NULL, label_commitment TEXT, decision TEXT NOT NULL, decision_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluations (request_id TEXT PRIMARY KEY REFERENCES requests(id), label INTEGER NOT NULL, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS training (request_id TEXT PRIMARY KEY REFERENCES requests(id), input_receipt_sha256 TEXT NOT NULL, input_receipt TEXT NOT NULL, output_receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS journal (sequence INTEGER PRIMARY KEY, previous_sha256 TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE);
                """
            )
            existing = self.db.execute(
                "SELECT terms FROM identity WHERE id=1"
            ).fetchone()
            if existing and json.loads(existing[0]) != self.terms:
                raise ValueError(
                    "Digit database identity differs from its authority marker"
                )
            if not existing:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    "INSERT INTO identity VALUES(1,?)", (canonical(self.terms),)
                )
                self.db.execute(
                    "INSERT INTO checkpoint VALUES(1,0,?,?)",
                    (canonical(initial), digest(initial)),
                )
                self._append(
                    "initialized",
                    {
                        "terms_sha256": digest(self.terms),
                        "state_sha256": digest(initial),
                    },
                )
                self._commit()
            self._reload()
            self.verify()
        except BaseException:
            self.close()
            raise

    def _new_model(self, state):
        return self._factory(**state["configuration"], state=copy.deepcopy(state))

    def _open(self):
        if self.closed or self.db is None:
            raise RuntimeError("The digit service is closed")

    def _commit(self):
        self.db.commit()

    def _append(self, kind, payload):
        previous = self.db.execute(
            "SELECT sequence,sha256 FROM journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence, head = (previous[0] + 1, previous[1]) if previous else (1, "0" * 64)
        event = {
            "sequence": sequence,
            "previous_sha256": head,
            "kind": kind,
            "payload": payload,
        }
        self.db.execute(
            "INSERT INTO journal VALUES(?,?,?,?,?)",
            (sequence, head, kind, canonical(payload), digest(event)),
        )

    def _reload(self):
        current = self.db.execute(
            "SELECT revision,state,state_sha256 FROM checkpoint WHERE id=1"
        ).fetchone()
        if (
            not current
            or digest(json.loads(current["state"])) != current["state_sha256"]
        ):
            raise ValueError("Digit checkpoint integrity failed")
        state = json.loads(current["state"])
        if digest(state["configuration"]) != self.terms["configuration_sha256"]:
            raise ValueError("Digit checkpoint configuration changed")
        self.model = self._new_model(state)
        self.revision = current["revision"]
        if (
            type(self.revision) is not int
            or self.revision < 0
            or state["optimizer"]["step"]
            != self.terms["initial_optimizer_step"] + self.revision
        ):
            raise ValueError("Digit optimizer and committed revision disagree")
        self.state_sha256 = current["state_sha256"]
        self.model_sha256 = model_digest(state)

    def _request(self, request_id):
        self._open()
        request_id = _identity(request_id, "request")
        row = self.db.execute(
            "SELECT * FROM requests WHERE id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise ValueError("A committed digit decision must precede buyer evaluation")
        return row

    def submit(self, request_id, values, *, purpose="inference", committed_label=None):
        """Commit a prediction without receiving its buyer label or nonce."""
        self._open()
        request_id = _identity(request_id, "request")
        values = pixels(values)
        if purpose not in ("inference", "training"):
            raise ValueError("Digit request purpose is unsupported")
        if purpose == "training":
            if not self.training_enabled or self.partition != "training":
                raise ValueError("Digit training authority is disabled")
            _hash(committed_label, "precommitted label")
        elif committed_label is not None:
            raise ValueError("Inference requests cannot register training authority")
        input_sha256 = digest(values)
        previous = self.db.execute(
            "SELECT * FROM requests WHERE id=?", (request_id,)
        ).fetchone()
        if previous:
            if (
                previous["input_sha256"] != input_sha256
                or previous["purpose"] != purpose
                or previous["label_commitment"] != committed_label
            ):
                raise ValueError(
                    "A committed digit request cannot change its inputs or authority"
                )
            return json.loads(previous["decision"])
        before = digest(self.model.state())
        prediction = self.model.predict(values)
        if digest(self.model.state()) != before or before != self.state_sha256:
            self._reload()
            raise ValueError("Digit inference changed the committed model state")
        _label(prediction.get("label"))
        for name in ("logits", "probabilities"):
            vector = prediction.get(name)
            if (
                type(vector) is not list
                or len(vector) != 10
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in vector
                )
            ):
                raise ValueError("Digit prediction is malformed")
        if any(
            not 0 <= value <= 1 for value in prediction["probabilities"]
        ) or not math.isclose(sum(prediction["probabilities"]), 1, abs_tol=1e-6):
            raise ValueError("Digit probabilities are not normalized")
        configuration = self.model.state()["configuration"]
        counts = prediction.get("spike_counts")
        if (
            type(counts) is not list
            or len(counts) != configuration["width"]
            or any(
                type(value) is not int or not 0 <= value <= configuration["ticks"]
                for value in counts
            )
            or type(prediction.get("total_spikes")) is not int
            or prediction["total_spikes"] != sum(counts)
            or prediction["label"]
            != max(range(10), key=lambda index: prediction["logits"][index])
        ):
            raise ValueError("Digit answer and spike counts are inconsistent")
        _hash(prediction.get("model_sha256"), "prediction model")
        if (
            prediction.get("state_sha256") != before
            or prediction["model_sha256"] != self.model_sha256
        ):
            raise ValueError("Digit prediction does not bind the current full state")
        decision = {
            "schema": "nuria.digit-decision.v1",
            "source_id": self.source_id,
            "dataset_sha256": self.dataset_sha256,
            "partition": self.partition,
            "request_id": request_id,
            "input_sha256": input_sha256,
            "purpose": purpose,
            "label_commitment": committed_label,
            "revision": self.revision,
            "prediction": prediction,
            "financial_transactions": 0,
        }
        decision["sha256"] = digest(decision)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?)",
                (
                    request_id,
                    input_sha256,
                    canonical(values),
                    purpose,
                    committed_label,
                    canonical(decision),
                    decision["sha256"],
                ),
            )
            self._append("decision", decision)
            self._commit()
        except BaseException:
            self.db.rollback()
            self._reload()
            raise
        return copy.deepcopy(decision)

    def _comparison(self, row, label, nonce=None):
        _label(label)
        if row["purpose"] == "training":
            if (
                label_commitment(
                    self.source_id, row["id"], row["input_sha256"], label, nonce
                )
                != row["label_commitment"]
            ):
                raise ValueError(
                    "Buyer label does not match its prior source-bound commitment"
                )
        elif nonce is not None:
            raise ValueError("Inference requests have no training-label nonce")
        decision = json.loads(row["decision"])
        return {
            "schema": "nuria.digit-comparison.v1",
            "source_id": self.source_id,
            "dataset_sha256": self.dataset_sha256,
            "partition": self.partition,
            "request_id": row["id"],
            "input_sha256": row["input_sha256"],
            "decision_sha256": row["decision_sha256"],
            "label": label,
            "predicted_label": decision["prediction"]["label"],
            "correct": label == decision["prediction"]["label"],
            "model_sha256": decision["prediction"]["model_sha256"],
            "state_sha256": decision["prediction"]["state_sha256"],
            "financial_transactions": 0,
        }

    def evaluate(self, request_id, buyer_label, *, nonce=None):
        """Compare only; neither weights nor optimizer can change here."""
        row = self._request(request_id)
        result = self._comparison(row, buyer_label, nonce)
        previous = self.db.execute(
            "SELECT receipt FROM evaluations WHERE request_id=?", (request_id,)
        ).fetchone()
        if previous:
            if json.loads(previous[0]) != result:
                raise ValueError("A committed buyer comparison cannot change")
            return json.loads(previous[0])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO evaluations VALUES(?,?,?)",
                (request_id, buyer_label, canonical(result)),
            )
            self._append("comparison", result)
            self._commit()
        except BaseException:
            self.db.rollback()
            self._reload()
            raise
        return copy.deepcopy(result)

    def learn(self, receipt):
        """Apply one revealed, origin-bound training label exactly once."""
        self._open()
        required = {
            "schema",
            "source_id",
            "request_id",
            "input_sha256",
            "decision_sha256",
            "label",
            "nonce",
            "verified",
        }
        if (
            type(receipt) is not dict
            or set(receipt) != required
            or receipt["schema"] != TRAINING_RECEIPT
            or receipt["source_id"] != self.source_id
            or receipt["verified"] is not True
        ):
            raise ValueError(
                "A complete source-bound verified training receipt is required"
            )
        row = self._request(receipt["request_id"])
        if (
            row["purpose"] != "training"
            or not self.training_enabled
            or self.partition != "training"
        ):
            raise ValueError("Inference-only digit decisions cannot train")
        if (
            receipt["input_sha256"] != row["input_sha256"]
            or receipt["decision_sha256"] != row["decision_sha256"]
        ):
            raise ValueError(
                "Training receipt does not bind the exact originating decision"
            )
        comparison = self._comparison(row, receipt["label"], receipt["nonce"])
        receipt_sha256 = digest(receipt)
        previous = self.db.execute(
            "SELECT * FROM training WHERE request_id=?", (row["id"],)
        ).fetchone()
        if previous:
            if previous["input_receipt_sha256"] != receipt_sha256:
                raise ValueError("An already learned digit receipt cannot change")
            return json.loads(previous["output_receipt"])
        evaluated = self.db.execute(
            "SELECT receipt FROM evaluations WHERE request_id=?", (row["id"],)
        ).fetchone()
        if evaluated and json.loads(evaluated[0]) != comparison:
            raise ValueError("Training label differs from the committed comparison")
        before = self.state_sha256
        before_model = self.model_sha256
        self.db.execute("BEGIN IMMEDIATE")
        try:
            statistics = self.model.train_batch(
                np.asarray([json.loads(row["pixels"])], dtype=float) / 16,
                [receipt["label"]],
                verified=True,
            )
            state = self.model.state()
            after = digest(state)
            after_model = model_digest(state)
            if (
                statistics.get("before_model_sha256") != before_model
                or statistics.get("after_model_sha256") != after_model
                or statistics.get("verified_labels") != 1
                or statistics.get("optimizer_step")
                != self.terms["initial_optimizer_step"] + self.revision + 1
            ):
                raise ValueError(
                    "Digit training statistics do not bind the actual model update"
                )
            result = {
                "schema": "nuria.digit-learning.v1",
                "source_id": self.source_id,
                "dataset_sha256": self.dataset_sha256,
                "request_id": row["id"],
                "input_sha256": row["input_sha256"],
                "decision_sha256": row["decision_sha256"],
                "training_receipt_sha256": receipt_sha256,
                "comparison": comparison,
                "before_state_sha256": before,
                "after_state_sha256": after,
                "before_model_sha256": before_model,
                "after_model_sha256": after_model,
                "revision": self.revision + 1,
                "statistics": statistics,
                "financial_transactions": 0,
            }
            self.db.execute(
                "INSERT INTO training VALUES(?,?,?,?)",
                (row["id"], receipt_sha256, canonical(receipt), canonical(result)),
            )
            if not evaluated:
                self.db.execute(
                    "INSERT INTO evaluations VALUES(?,?,?)",
                    (row["id"], receipt["label"], canonical(comparison)),
                )
            self.db.execute(
                "UPDATE checkpoint SET revision=?,state=?,state_sha256=? WHERE id=1",
                (self.revision + 1, canonical(state), after),
            )
            self._append("learning", result)
            self._commit()
            self._reload()
        except BaseException:
            self.db.rollback()
            self._reload()
            raise
        return copy.deepcopy(result)

    def verify(self):
        """Verify retained event order, decisions, outcomes and model continuity."""
        self._open()
        identity = self.db.execute("SELECT terms FROM identity WHERE id=1").fetchone()
        if identity is None or json.loads(identity[0]) != self.terms:
            raise ValueError("Digit database authority identity changed")
        current = self.db.execute(
            "SELECT revision,state,state_sha256 FROM checkpoint WHERE id=1"
        ).fetchone()
        if (
            current is None
            or current["revision"] != self.revision
            or current["state_sha256"] != self.state_sha256
            or digest(json.loads(current["state"])) != self.state_sha256
            or digest(self.model.state()) != self.state_sha256
        ):
            raise ValueError(
                "Digit persisted checkpoint differs from the running model"
            )
        head = "0" * 64
        sequence = 0
        state = self.terms["reviewed_state_sha256"]
        model = self.terms["reviewed_model_sha256"]
        learned = 0
        decisions, evaluations, training = {}, {}, {}
        for row in self.db.execute("SELECT * FROM journal ORDER BY sequence"):
            sequence += 1
            payload = json.loads(row["payload"])
            event = {
                "sequence": sequence,
                "previous_sha256": head,
                "kind": row["kind"],
                "payload": payload,
            }
            if (
                row["sequence"] != sequence
                or row["previous_sha256"] != head
                or digest(event) != row["sha256"]
            ):
                raise ValueError("Digit journal integrity failed")
            if row["kind"] == "initialized":
                if sequence != 1 or payload != {
                    "terms_sha256": digest(self.terms),
                    "state_sha256": state,
                }:
                    raise ValueError(
                        "Digit journal initialization differs from its authority"
                    )
            elif row["kind"] == "decision":
                request_id = payload.get("request_id")
                if (
                    request_id in decisions
                    or payload.get("source_id") != self.source_id
                    or payload.get("dataset_sha256") != self.dataset_sha256
                    or payload.get("partition") != self.partition
                    or payload.get("revision") != learned
                    or payload.get("prediction", {}).get("state_sha256") != state
                    or payload["prediction"].get("model_sha256") != model
                ):
                    raise ValueError("Digit journal decision origin is inconsistent")
                decisions[request_id] = payload
            elif row["kind"] == "comparison":
                request_id = payload.get("request_id")
                if (
                    request_id not in decisions
                    or request_id in evaluations
                    or payload.get("decision_sha256") != decisions[request_id]["sha256"]
                ):
                    raise ValueError(
                        "Digit journal comparison has no unique originating decision"
                    )
                evaluations[request_id] = payload
            elif row["kind"] == "learning":
                learned += 1
                request_id = payload.get("request_id")
                if (
                    payload["before_state_sha256"] != state
                    or payload["before_model_sha256"] != model
                    or payload["revision"] != learned
                    or request_id not in decisions
                    or request_id in training
                    or payload["decision_sha256"] != decisions[request_id]["sha256"]
                    or decisions[request_id]["purpose"] != "training"
                    or not self.training_enabled
                ):
                    raise ValueError("Digit learning checkpoint continuity failed")
                if (
                    request_id in evaluations
                    and evaluations[request_id] != payload["comparison"]
                ):
                    raise ValueError(
                        "Digit learning differs from its prior buyer comparison"
                    )
                evaluations[request_id] = payload["comparison"]
                training[request_id] = payload
                state = payload["after_state_sha256"]
                model = payload["after_model_sha256"]
            else:
                raise ValueError("Digit journal event kind is unsupported")
            head = row["sha256"]
        if (
            sequence < 1
            or state != self.state_sha256
            or model != self.model_sha256
            or learned != self.revision
        ):
            raise ValueError("Digit journal and model checkpoint disagree")
        stored_requests = {}
        for row in self.db.execute("SELECT * FROM requests"):
            decision = json.loads(row["decision"])
            if (
                digest(pixels(json.loads(row["pixels"]))) != row["input_sha256"]
                or decision["input_sha256"] != row["input_sha256"]
                or decision["request_id"] != row["id"]
                or decision["purpose"] != row["purpose"]
                or decision["label_commitment"] != row["label_commitment"]
                or row["purpose"] not in ("inference", "training")
                or row["purpose"] == "inference"
                and row["label_commitment"] is not None
                or row["purpose"] == "training"
                and (
                    not self.training_enabled
                    or not isinstance(row["label_commitment"], str)
                    or not HASH.fullmatch(row["label_commitment"])
                )
                or digest(
                    {key: value for key, value in decision.items() if key != "sha256"}
                )
                != row["decision_sha256"]
                or decision["sha256"] != row["decision_sha256"]
                or decisions.get(row["id"]) != decision
            ):
                raise ValueError("Digit decision integrity failed")
            stored_requests[row["id"]] = row
        if set(stored_requests) != set(decisions):
            raise ValueError("Digit journal and request membership differ")
        stored_evaluations = {}
        for row in self.db.execute("SELECT * FROM evaluations"):
            comparison = json.loads(row["receipt"])
            request = stored_requests[row["request_id"]]
            origin = decisions[row["request_id"]]
            if (
                comparison != evaluations.get(row["request_id"])
                or comparison["source_id"] != self.source_id
                or comparison["dataset_sha256"] != self.dataset_sha256
                or comparison["partition"] != self.partition
                or comparison["request_id"] != row["request_id"]
                or comparison["input_sha256"] != request["input_sha256"]
                or comparison["decision_sha256"] != origin["sha256"]
                or comparison["model_sha256"] != origin["prediction"]["model_sha256"]
                or comparison["state_sha256"] != origin["prediction"]["state_sha256"]
                or comparison["label"] != _label(row["label"])
                or comparison["predicted_label"] != origin["prediction"]["label"]
                or comparison["correct"]
                is not (row["label"] == origin["prediction"]["label"])
            ):
                raise ValueError("Digit buyer comparison integrity failed")
            stored_evaluations[row["request_id"]] = comparison
        if set(stored_evaluations) != set(evaluations):
            raise ValueError("Digit journal and comparison membership differ")
        stored_training = {}
        for row in self.db.execute("SELECT * FROM training"):
            receipt = json.loads(row["input_receipt"])
            result = json.loads(row["output_receipt"])
            request = stored_requests[row["request_id"]]
            comparison = self._comparison(request, receipt["label"], receipt["nonce"])
            if (
                receipt["schema"] != TRAINING_RECEIPT
                or receipt["source_id"] != self.source_id
                or receipt["request_id"] != row["request_id"]
                or receipt["verified"] is not True
                or receipt["input_sha256"] != request["input_sha256"]
                or receipt["decision_sha256"] != request["decision_sha256"]
                or digest(receipt) != row["input_receipt_sha256"]
                or result != training.get(row["request_id"])
                or result["training_receipt_sha256"] != row["input_receipt_sha256"]
                or result["comparison"] != comparison
            ):
                raise ValueError("Digit training receipt integrity failed")
            stored_training[row["request_id"]] = result
        if set(stored_training) != set(training):
            raise ValueError("Digit journal and training membership differ")
        return {
            "valid": True,
            "events": sequence,
            "head_sha256": head,
            "revision": self.revision,
            "state_sha256": self.state_sha256,
            "financial_transactions": 0,
        }

    def summary(self):
        self._open()
        counts = {
            table: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("requests", "evaluations", "training")
        }
        return {
            "schema": VERSION,
            "source_id": self.source_id,
            "dataset_sha256": self.dataset_sha256,
            "partition": self.partition,
            "training_enabled": self.training_enabled,
            **counts,
            **self.verify(),
            "scope": "Private local model inference and source-registered label learning; no payment, external customer or independent label-truth attestation.",
        }

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
