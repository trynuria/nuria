"""Reproduce an isolated task round trip through real Brian2 state and journals."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from cognition.worker import Organism
from commerce.action_loop import ActionLoop
from commerce.ledger import Ledger, canonical
from commerce.tasks import contractor_brief, digest


def check(directory):
    if directory.exists():
        raise ValueError("Use a new isolated directory; preserve prior evidence")
    directory.mkdir(parents=True)
    organism = Organism(directory / "cognition", "isolated-task-check")
    ledger = Ledger(directory / "commerce.sqlite3")
    loop = ActionLoop(ledger)
    task = {
        "id": "reference-classifier",
        "title": "Evaluate an isolated classifier",
        "purpose": "Check exact neural task, delivery and delayed outcome continuity",
        "brief": json.dumps(
            {
                "features": [[0], [1]] * 4,
                "method": "Return 0.1 for zero and 0.9 for one",
            }
        ),
        "kind": "agent",
        "provider": "local-reference",
        "action": "experiment",
        "maximum_micro_usd": 1_000_000,
        "delivery_seconds": 3600,
        "evaluation_nonce": "0123456789abcdef" * 4,
        "acceptance": {
            "type": "predictions",
            "dataset_sha256": digest([[0], [1]] * 4),
            "targets": [0, 1] * 4,
            "baseline": [0.5] * 8,
            "minimum_gain": 0.01,
        },
    }
    try:
        organism.agency_tasks = [task]
        for _ in range(5):
            organism.cycle([])
        decision = organism.journal.recent("decision")[0]
        intent = decision["intent"]
        pending = organism.agency.pending[intent["id"]]
        assert digest(pending["synaptic_trace"]) == intent["eligibility_sha256"]
        now = time.time()
        ident = loop.propose(task, decision, now)
        quote = {
            "provider": "local-reference",
            "account": "included-capacity",
            "offer_id": "included-server-capacity",
            "total_micro_usd": 0,
            "expires_at": now + 60,
            "checked_at": now,
            "destination": {"basis": "included_capacity"},
            "evidence_sha256": digest({"billing": "included_capacity"}),
            "payment_basis": "included_capacity",
        }
        policy = SimpleNamespace(enabled=False)
        loop.reserve(
            ident,
            quote,
            {
                "account": "included-capacity",
                "available_micro_usd": 0,
                "checked_at": now,
                "verified": True,
            },
            policy,
            {"enabled": True, "providers": ["local-reference"]},
            now,
        )
        external = "local-" + ident[:24]
        loop.operate(
            ident,
            "dispatch",
            contractor_brief(task, ident),
            lambda *_: {"job_id": ident, "external_id": external},
            time.time(),
        )
        public = contractor_brief(task, ident)
        features = json.loads(public["brief"])["features"]
        cpu = time.process_time()
        predictions = [0.1 if row[0] == 0 else 0.9 for row in features]
        cpu = time.process_time() - cpu
        raw = canonical(
            {
                "schema": "nuria.task-result.v1",
                "job_id": ident,
                "contract_sha256": public["contract_sha256"],
                "result": predictions,
            }
        ).encode()
        acceptance = loop.receive(ident, raw, time.time())
        loop.settle(
            ident,
            {
                "job_id": ident,
                "account": "included-capacity",
                "external_id": external,
                "verified": True,
                "basis": "included_capacity",
                "total_micro_usd": 0,
                "checked_at": time.time(),
                "evidence_sha256": digest(
                    {
                        "cpu_seconds": cpu,
                        "artifact_sha256": acceptance["artifact_sha256"],
                    }
                ),
            },
            time.time(),
        )
        outcome = loop.evaluate(ident, time.time())
        before = organism.brain.state_hash()
        organism.task_feedback(loop.feedback())
        credited = organism.brain.state_hash()
        event = organism.journal.recent("task_outcome")[0]
        assert before != credited and event["neural_credit"]["changed_synapses"] > 0
        organism.task_feedback(loop.feedback())
        assert organism.brain.state_hash() == credited
        organism.save()
        source_cursor, genesis = organism.cursor, organism.genesis
        organism.journal.db.close()
        organism = Organism(directory / "cognition", "isolated-task-check")
        assert (
            organism.brain.state_hash() == credited and organism.agency.completed == 1
        )
        assert organism.cursor == source_cursor and organism.genesis == genesis
        return {
            "schema": "nuria.action-loop-check.v1",
            "verified": True,
            "scope": "Isolated contract fixture with actual Brian2 state. The local reference classifier uses included capacity; no external agent was hired, no market input was inserted, no payment or vendor integration is implied.",
            "neural_intent_bound": True,
            "originating_eligibility_bound": True,
            "decision_before_delivery": True,
            "private_targets_excluded_from_brief": '"targets"' not in canonical(public),
            "acceptance": acceptance,
            "outcome": outcome,
            "neural_credit": event["neural_credit"],
            "duplicate_feedback_no_effect": True,
            "joint_recovery_verified": True,
            "source_cursor_preserved": True,
            "cognitive_integrity": organism.journal.verify(full=True),
            "commerce_integrity": ledger.verify(),
            "financial_transactions": 0,
            "paid_micro_usd": 0,
            "local_reference_cpu_seconds": cpu,
            "runtime_source_sha256": hashlib.sha256(
                b"".join(
                    p.read_bytes()
                    for p in sorted(
                        (Path(__file__).resolve().parents[1] / "cognition").glob("*.py")
                    )
                )
            ).hexdigest(),
        }
    finally:
        organism.journal.db.close()
        ledger.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve the prior check report")
    value = check(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value))
