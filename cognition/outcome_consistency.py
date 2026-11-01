"""Withhold contradictory host observations from future automatic experience.

Original job receipts and their accepted labels remain immutable. Observations
are trusted host inputs, not model judgments or independent attestations. This
uses the existing outcome journal; no second database or background work runs.
"""

from __future__ import annotations

import hashlib
import json
import re

from cognition.outcome_worker import OutcomeStore, identity

EVENT = "learning-observation"


class ConsistentOutcomeStore(OutcomeStore):
    """An outcome store whose automatic experience excludes conflicting jobs."""

    def raw_experiences(self):
        return super().experiences()

    @staticmethod
    def _bound(record, checker):
        original = record.get("checker") or {}
        if (
            type(checker) is not dict
            or type(record.get("accepted")) is not bool
            or original.get("passed") is not record["accepted"]
            or type(checker.get("passed")) is not bool
            or not re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"])
            or checker.get("code_sha256")
            != hashlib.sha256(record["code"].encode()).hexdigest()
            or checker.get("code_sha256") != original.get("code_sha256")
            or not re.fullmatch(r"[0-9a-f]{64}", checker.get("checks_sha256", ""))
            or checker["checks_sha256"] != original.get("checks_sha256")
        ):
            raise ValueError(
                "Observation differs from its checked source/program/contract"
            )

    def observe(self, job, observation_id, checker, *, result_sha256):
        """Append one host-supplied check; identical receipt IDs do not add votes.

        The host must have executed the original enrolled inputs and checker.
        Hash binding catches changed code/checkers, not a dishonest host. A
        single contradictory verdict withholds credit; a majority cannot erase it.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", observation_id):
            raise ValueError("Observation needs a bounded receipt identity")
        if len(json.dumps(checker, allow_nan=False).encode()) > 32768:
            raise ValueError("Observation exceeds its receipt bound")
        self.verify()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT contract,state,body FROM jobs WHERE id=?", (job,)
            ).fetchone()
            if row is None or row[1] != "finished":
                raise ValueError("Observation requires a finished job")
            record = json.loads(row[2])
            if identity(record) != result_sha256:
                raise ValueError("Observation identifies a different original receipt")
            self._bound(record, checker)
            body = {
                "event": EVENT,
                "job": job,
                "observation_id": observation_id,
                "contract_sha256": row[0],
                "result_sha256": result_sha256,
                "checker": checker,
            }
            for (text,) in db.execute("SELECT body FROM events ORDER BY seq"):
                prior = json.loads(text)
                if (
                    prior.get("event") == EVENT
                    and prior["observation_id"] == observation_id
                ):
                    if prior != body:
                        raise ValueError("Observation ID cannot be rebound")
                    return {**prior, "replayed": True}
            self._event(db, body)
        return {**body, "replayed": False}

    def learning_report(self):
        """Read raw history and observations in one consistent journal snapshot."""
        self.verify()
        with self.connect() as db:
            db.execute("BEGIN")
            records = {
                job: (contract, json.loads(body))
                for job, contract, body in db.execute(
                    "SELECT id,contract,body FROM jobs WHERE state='finished' ORDER BY id"
                )
            }
            chain = db.execute(
                "SELECT previous,sha256,body FROM events ORDER BY seq"
            ).fetchall()
        previous, expected, contracts, events = "0" * 64, {}, {}, []
        for prior, sha, text in chain:
            event = json.loads(text)
            if prior != previous or sha != identity(
                {"previous": prior, "event": event}
            ):
                raise ValueError("Outcome journal changed")
            if event["event"] == "begin":
                contracts[event["job"]] = event["contract_sha256"]
            if event["event"] in ("outcome", "acceptance"):
                expected[event["job"]] = event["result_sha256"]
            previous = sha
            events.append(event)
        if any(
            expected.get(job) != identity(record) or contracts.get(job) != contract
            for job, (contract, record) in records.items()
        ):
            raise ValueError("Stored outcome differs from its journal snapshot")
        verdicts = {
            job: {record["accepted"]}
            for job, (_, record) in records.items()
            if type(record.get("accepted")) is bool
        }
        seen = set()
        for event in events:
            if event.get("event") != EVENT:
                continue
            contract, record = records[event["job"]]
            if (
                event["observation_id"] in seen
                or event["contract_sha256"] != contract
                or event["result_sha256"] != identity(record)
            ):
                raise ValueError("Observation provenance differs from its original job")
            seen.add(event["observation_id"])
            self._bound(record, event["checker"])
            verdicts[event["job"]].add(event["checker"]["passed"])
        withheld = [job for job, values in verdicts.items() if len(values) != 1]
        return {
            "version": "nuria.consistent-experience.v1",
            "records": [
                record
                for job, (_, record) in records.items()
                if job in verdicts and job not in withheld
            ],
            "withheld_job_ids": withheld,
            "unknown_job_ids": [job for job in records if job not in verdicts],
            "raw_jobs": len(records),
            "additional_observations": len(seen),
            "original_receipts_changed": False,
            "scope": "Per-job host observation consistency, not proof of correctness or model superiority",
        }

    def experiences(self):
        return self.learning_report()["records"]
