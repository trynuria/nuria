"""Read-only verification of four-audit composed text episodes and neural feedback.

Only evidence helpers and the independent text interpreter are imported.
No controller, provider, learner, simulator, signer or application constructor
participates in verification. Local commitments are not an external witness.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.verify_active_skill_results import outcome as text_outcome
from scripts.verify_live_skills import (
    HASH,
    cache,
    canonical,
    close,
    commerce_integrity,
    configure_paths,
    digest,
    financial_counts,
    read_json,
    record_hash,
    require,
    source_identity,
    sqlite_view,
    timestamp,
    write_new,
)

PROVIDER = "local-composed-records-v1"
SOURCE = "nuria:composed-record-repair-v1"
POLICY = "composed_fixed4"
SOLVER_ID = "bounded-text-composition-v1:four-audits"


def outcome(program, inputs, expected, config):
    checked = text_outcome(program, inputs, expected, config)
    return {
        **checked,
        "tool_units": checked["execution_work"],
        "actual_sha256": digest(checked["outputs"]),
    }


COSTS = ("portfolio_work", "routing_work", "probe_work", "synthesis_work")
MODULES = (
    "cognition.active_skills",
    "cognition.skill_language",
    "cognition.skills",
    "cognition.skill_expressions",
    "cognition.skill_factored",
    "cognition.text_skills",
    "commerce.local_skills",
    "commerce.tasks",
)


def parsed(raw):
    require(
        type(raw) is str and len(raw.encode()) <= 8 * 1024 * 1024,
        "Evidence size is invalid",
    )
    return json.loads(raw)


def units(left, right):
    return 1 + (len(left.encode()) + len(right.encode()) + 31) // 32


def course_evidence(path, authority):
    course, checksum = read_json(path)
    require(
        type(course) is dict
        and set(course)
        == {
            "schema",
            "provider",
            "source_id",
            "config_sha256",
            "tasks",
            "expected_count",
        }
        and course["schema"] == "nuria.composed-skill-course.v1"
        and course["provider"] == authority["id"] == PROVIDER
        and course["source_id"] == authority["source_id"] == SOURCE
        and course["config_sha256"] == digest(authority["config"])
        and type(course["expected_count"]) is int
        and 1 <= course["expected_count"] <= 512
        and type(course["tasks"]) is list
        and len(course["tasks"]) == course["expected_count"]
        and all(
            type(item) is dict
            and set(item) == {"contract_sha256"}
            and type(item["contract_sha256"]) is str
            and HASH.fullmatch(item["contract_sha256"])
            for item in course["tasks"]
        )
        and len({item["contract_sha256"] for item in course["tasks"]})
        == len(course["tasks"]),
        "Course manifest identity, denominator or contract commitments changed",
    )
    return course, checksum


def event(db, job, key, value):
    rows = db.execute(
        "SELECT seq,hash,payload FROM events WHERE json_extract(payload,'$.active_job_id')=? AND json_extract(payload,?)=? ORDER BY seq",
        (job, "$." + key, value),
    ).fetchall()
    require(len(rows) == 1, "Active operation lacks one unique journal event")
    return rows[0], parsed(rows[0]["payload"])


def check_library(state, source, config_hash):
    require(
        state["source_id"] == source
        and state["config_sha256"] == config_hash
        and state["version"] == "verified-text-library-v1"
        and type(state["sequence"]) is int
        and type(state["total_work"]) is int
        and 0 <= state["sequence"] <= 2**53 - 1
        and 0 <= state["total_work"] <= 2**53 - 1,
        "Library source or language identity changed",
    )
    require(
        not state["macros"] and len(state["programs"]) <= 128,
        "Library exceeds the inspected no-macro scope",
    )
    for ident, record in state["programs"].items():
        require(
            HASH.fullmatch(ident)
            and digest(record["program"]) == ident
            and HASH.fullmatch(record["evidence_sha256"])
            and type(record["uses"]) is int
            and record["uses"] > 0
            and any(
                receipt.get("program_sha256") == ident
                and receipt.get("accepted") is True
                and receipt.get("evidence_sha256") == record["evidence_sha256"]
                for receipt in state["receipts"].values()
            ),
            "Retained program identity or buyer provenance is invalid",
        )


def inspect_episode(db, task, local, authority, root):
    contract, intent = parsed(task["contract"]), parsed(task["intent"])
    rule, source = contract["acceptance"], authority["source_id"]
    require(
        set(rule)
        == {
            "type",
            "config",
            "diagnostic_inputs",
            "diagnostic_outputs",
            "heldout_inputs",
            "heldout_outputs",
            "maximum_work",
            "maximum_probes",
            "synthesis_work",
            "minimum_evidence",
            "policy",
            "source_id",
        }
        and rule["type"] == "transform_active"
        and rule["config"] == authority["config"]
        and rule["config"]["version"] == "safe-text-v1"
        and rule["policy"] == POLICY
        and rule["source_id"] == SOURCE
        and rule["maximum_probes"] == rule["minimum_evidence"] == 4
        and rule["config"]["max_macros"] == 0,
        "Unqualified or hinted active data-transform contract",
    )
    config = rule["config"]
    require(
        contract["maximum_micro_usd"] == task["amount"] == 0
        and contract["kind"] == "compute"
        and not contract.get("outcome")
        and digest(contract) == intent["contract_sha256"]
        and digest({k: v for k, v in intent.items() if k != "id"})
        == task["intent_id"]
        == intent["id"],
        "Episode is not one bound zero-payment neural intent",
    )
    require(
        4 <= len(rule["diagnostic_inputs"]) <= config["max_rows"]
        and len(rule["diagnostic_inputs"]) == len(rule["diagnostic_outputs"])
        and 8
        <= len(rule["heldout_inputs"])
        == len(rule["heldout_outputs"])
        <= config["max_rows"]
        and len(set(rule["diagnostic_inputs"])) == len(rule["diagnostic_inputs"])
        and not set(rule["diagnostic_inputs"]) & set(rule["heldout_inputs"])
        and all(
            type(v) is str and len(v.encode()) <= config["max_input_bytes"]
            for name in (
                "diagnostic_inputs",
                "diagnostic_outputs",
                "heldout_inputs",
                "heldout_outputs",
            )
            for v in rule[name]
        ),
        "Diagnostic and final partitions are malformed or overlap",
    )
    episode = db.execute(
        "SELECT * FROM active_skill_episodes WHERE job_id=?", (task["id"],)
    ).fetchone()
    require(
        episode is not None
        and episode["state"] == "evaluated"
        and episode["provider"] == local["provider"] == authority["id"],
        "Active episode is not completely checked",
    )
    state, initial = parsed(episode["session"]), parsed(episode["initial_library"])
    state_body = {k: v for k, v in state.items() if k != "state_sha256"}
    require(
        digest(state_body) == state["state_sha256"]
        and digest(state) == episode["session_sha256"]
        and digest(initial) == episode["initial_library_sha256"],
        "Active checkpoint or original library commitment changed",
    )
    policy = "audited_retention"
    implementation = {
        name: hashlib.sha256(
            (root / (name.replace(".", "/") + ".py")).read_bytes()
        ).hexdigest()
        for name in MODULES
    }
    implementation["runtime"] = hashlib.sha256(
        (root / "commerce/active_skill_runtime.py").read_bytes()
    ).hexdigest()
    base_terms = digest(
        {
            "schema": "nuria.active-skill-runtime.v1",
            "provider": task["provider"],
            "source_id": source,
            "policy": policy,
            "contract_sha256": digest(contract),
            "implementation": implementation,
        }
    )
    composed_implementation = {
        name: hashlib.sha256(
            (root / (name.replace(".", "/") + ".py")).read_bytes()
        ).hexdigest()
        for name in (
            "cognition.composed_skills",
            "cognition.text_composition",
            "commerce.composed_skill_runtime",
        )
    }
    require(
        episode["terms_sha256"]
        == digest(
            {
                "schema": "nuria.composed-skill-runtime.v1",
                "base_terms": base_terms,
                "provider": task["provider"],
                "source_id": source,
                "mode": "active_composed",
                "policy": POLICY,
                "solver_id": SOLVER_ID,
                "implementation": composed_implementation,
            }
        ),
        "Active episode underlying implementation identity changed",
    )
    limits = state["terms"]["limits"]
    text_blocks = (config["max_input_bytes"] + 31) // 32
    final_reserve = config["max_rows"] * (
        text_blocks
        + config["max_ops"] * (2 + text_blocks + 16 * text_blocks)
        + 2 * (1 + 2 * text_blocks)
    )
    require(
        state["terms"]["source_id"] == source
        and state["terms"]["config_sha256"] == digest(config)
        and state["terms"]["inputs_sha256"] == digest(rule["diagnostic_inputs"])
        and state["terms"]["initial_library_sha256"] == digest(initial)
        and state["terms"].get("policy") == policy
        and state["session_id"] == digest(state["terms"])
        and state["inputs"] == rule["diagnostic_inputs"]
        and all(
            limits[key] == rule[key]
            for key in ("maximum_probes", "synthesis_work", "minimum_evidence")
        )
        and limits["maximum_work"] == rule["maximum_work"] - final_reserve
        and state["accounting_uncertain"] is False
        and state["uncertain_reservation"] == 0
        and state["pending"] is None
        and episode["next_action"] is None,
        "Routing checkpoint has changed terms, hidden rows or uncertain execution",
    )
    check_library(initial, source, digest(config))
    work = state["work"]
    require(
        all(type(v) is int and v >= 0 for v in work.values())
        and sum(work[k] for k in COSTS) == work["total"] == episode["work"]
        and work["total"]
        <= limits["maximum_work"]
        < rule["maximum_work"]
        <= authority["maximum_work_per_job"],
        "Active work components or authority bounds disagree",
    )
    _, initialized = event(db, task["id"], "active_phase", "initialized")
    previous = initialized["active_session_sha256"]
    actions = db.execute(
        "SELECT * FROM active_skill_actions WHERE job_id=? ORDER BY ordinal",
        (task["id"],),
    ).fetchall()
    require(0 < len(actions) <= 256, "Episode operation coverage is invalid")
    labels, probe_records, pending, observed_work = {}, [], None, 0
    portfolio, portfolio_work = {}, 0
    if policy == "disagreement":
        for ident, record in sorted(initial["programs"].items()):
            outputs, charged = [], 1
            for value in rule["diagnostic_inputs"]:
                checked = text_outcome(record["program"], [value], [""], config)
                output, cost = checked["outputs"][0], checked["execution_work"]
                outputs.append(output)
                charged += cost
            portfolio[ident] = outputs
            portfolio_work += charged
    require(
        work["portfolio_work"] == portfolio_work,
        "Independent portfolio execution cost differs",
    )
    for ordinal, action in enumerate(actions):
        request, response = parsed(action["request"]), parsed(action["response"])
        reservations = db.execute(
            "SELECT payload FROM events WHERE json_extract(payload,'$.active_action_id')=? AND json_extract(payload,'$.active_phase')='operation_reserved'",
            (action["id"],),
        ).fetchall()
        completions = db.execute(
            "SELECT payload FROM events WHERE json_extract(payload,'$.active_action_id')=? AND json_extract(payload,'$.active_phase') IN ('operation_completed','selected')",
            (action["id"],),
        ).fetchall()
        require(
            len(reservations) == len(completions) == 1,
            "Operation lacks exactly one reservation and completion",
        )
        reserved, completed = parsed(reservations[0][0]), parsed(completions[0][0])
        require(
            action["ordinal"] == ordinal
            and action["state"] == "completed"
            and digest(request)
            == action["request_sha256"]
            == reserved["request_sha256"]
            and reserved["active_session_sha256"] == previous
            and action["id"]
            == digest(
                {
                    "job_id": task["id"],
                    "ordinal": ordinal,
                    "kind": action["kind"],
                    "request": request,
                    "session_sha256": previous,
                }
            )
            and digest(response) == completed["response_sha256"]
            and action["work"] == completed["work"]
            and 0
            <= action["work"]
            <= action["reserved_work"]
            == reserved["maximum_work"],
            "Active operation intent, response or charged work changed",
        )
        previous = completed["active_session_sha256"]
        observed_work += action["work"]
        if action["kind"] == "decide":
            require(
                pending is None
                and request == {"session_sha256": reserved["active_session_sha256"]},
                "Decision does not continue the preceding checkpoint",
            )
            require(
                response["id"]
                == digest({k: v for k, v in response.items() if k != "id"})
                and response["session_id"] == state["session_id"],
                "Router action identity changed",
            )
            pending = response if response["kind"] in ("probe", "synthesize") else None
            if response["kind"] == "probe":
                index = response["input_index"]
                require(
                    type(index) is int
                    and 0 <= index < len(rule["diagnostic_inputs"])
                    and index not in labels,
                    "Router repeated or invented a diagnostic row",
                )
                if policy == "audited_retention":
                    require(
                        len(labels) < max(2, rule["minimum_evidence"])
                        and index
                        == next(
                            i
                            for i in range(len(rule["diagnostic_inputs"]))
                            if i not in labels
                        ),
                        "Audited policy did not request the next distinct diagnostic row",
                    )
                else:
                    candidates = [
                        values
                        for values in portfolio.values()
                        if None not in values
                        and all(values[i] == label for i, label in labels.items())
                    ]
                    scores = {}
                    for i in range(len(rule["diagnostic_inputs"])):
                        if i in labels:
                            continue
                        groups = {}
                        for values in candidates:
                            groups[values[i]] = groups.get(values[i], 0) + 1
                        scores[i] = len(candidates) ** 2 - sum(
                            count * count for count in groups.values()
                        )
                    require(
                        index == max(scores, key=lambda i: (scores[i], -i)),
                        "Disagreement policy did not select the independently reconstructed probe",
                    )
            require(
                response["kind"] in ("probe", "synthesize", "propose", "abstain"),
                "Unsupported router action",
            )
        elif action["kind"] == "probe":
            require(
                request == pending and pending["kind"] == "probe",
                "Probe did not follow its selected request",
            )
            index = request["input_index"]
            value, target = (
                rule["diagnostic_inputs"][index],
                rule["diagnostic_outputs"][index],
            )
            cost = units(value, target)
            require(
                request["input"] == value
                and request["input_sha256"] == digest(value)
                and response
                == {
                    "source_id": source,
                    "input_index": index,
                    "input_sha256": digest(value),
                    "output": target,
                    "work": cost,
                    "receipt_id": digest(
                        {
                            "job_id": task["id"],
                            "contract_sha256": digest(contract),
                            "action_id": request["id"],
                            "input_index": index,
                            "input_sha256": digest(value),
                            "output": target,
                            "work": cost,
                            "nonce": contract["evaluation_nonce"],
                        }
                    ),
                }
                and action["work"] == cost,
                "Buyer probe disclosed another label or changed its actual cost",
            )
            labels[index] = target
            probe_records.append({"action": request, "receipt": response})
            pending = None
        else:
            require(
                action["kind"] == "synthesize"
                and request == pending
                and pending["kind"] == "synthesize",
                "Search did not follow its selected request",
            )
            require(
                len(labels) == rule["minimum_evidence"] == 4
                and action["work"]
                == response["work"]["total"]
                <= request["maximum_work"]
                <= rule["synthesis_work"]
                and response.get("search_version")
                == SOLVER_ID.removesuffix(":four-audits")
                and response.get("search_mode") == "deduplicated",
                "Search evidence or work allowance changed",
            )
            if response["program"] is not None:
                proposal = state["library"]["pending"].get(response["proposal_id"])
                ordered = sorted(labels)
                require(
                    proposal is not None
                    and proposal["training_sha256"]
                    == digest(
                        [
                            [rule["diagnostic_inputs"][i] for i in ordered],
                            [labels[i] for i in ordered],
                        ]
                    ),
                    "Search used labels outside the requested diagnostic receipts",
                )
                require(
                    proposal["program"] == response["program"]
                    and proposal["program_sha256"]
                    == response["program_sha256"]
                    == digest(response["program"])
                    and proposal["work"] == action["work"]
                    and response["proposal_id"]
                    == digest(
                        {
                            "source_id": source,
                            "config_sha256": digest(config),
                            **proposal,
                        }
                    )
                    and state["synthesis"] == response
                    and state["synthesis_action"] == request,
                    "Recorded search proposal lost its source or program binding",
                )
                require(
                    outcome(
                        response["program"],
                        [rule["diagnostic_inputs"][i] for i in ordered],
                        [labels[i] for i in ordered],
                        config,
                    )["passed"],
                    "Proposed program does not fit its visible diagnostic evidence",
                )
            pending = None
    require(
        previous == episode["session_sha256"] and observed_work == work["total"],
        "Final checkpoint or total operation work changed",
    )
    require(
        state["library"]["programs"] == initial["programs"]
        and state["library"]["receipts"] == initial["receipts"]
        and sum(action["kind"] == "synthesize" for action in actions) == 1
        and state["library"]["sequence"] == initial["sequence"] + 1
        and state["library"]["total_work"]
        == initial["total_work"] + work["synthesis_work"],
        "An episode acquired unchecked programs or omitted search work before final acceptance",
    )
    require(
        len(labels) == rule["maximum_probes"] == 4
        and work["probe_work"]
        == sum(record["receipt"]["work"] for record in probe_records),
        "Probe coverage or disclosure work changed",
    )
    require(
        state["labels"] == {str(i): value for i, value in labels.items()}
        and state["probe_receipts"]
        == {
            record["action"]["id"]: {**record, "sha256": digest(record["receipt"])}
            for record in probe_records
        },
        "Checkpoint labels differ from actual buyer exchanges",
    )
    selected = parsed(local["delivery"])
    require(
        selected["job_id"] == task["id"]
        and selected["contract_sha256"] == digest(contract),
        "Delivery is bound to another episode",
    )
    result = selected["result"]
    chosen = state["selected"] or {
        "program": None,
        "program_sha256": None,
        "proposal_id": None,
        "selection": None,
    }
    expected_result = {
        "status": "proposed" if state["stage"] == "proposed" else "abstained",
        **chosen,
        "reason": state["reason"],
        "source_id": source,
        "session_id": state["session_id"],
        "work": work,
        "probes_used": len(labels),
        "probe_receipt_sha256s": [
            digest(record["receipt"]) for record in probe_records
        ],
        "accounting_uncertain": False,
        "uncertain_reservation": 0,
        "session_sha256": episode["session_sha256"],
        "probe_receipts": probe_records,
    }
    require(
        result == expected_result and parsed(local["proposal"]) == result,
        "Actual delivery differs from its fixed, diagnostic-only selection",
    )
    program = result["program"]
    if result["selection"] == "retained":
        require(
            result["program_sha256"] in initial["programs"]
            and program == initial["programs"][result["program_sha256"]]["program"],
            "Retained selection had no original verified program",
        )
        if result["proposal_id"] is None:
            compatible = sorted(
                ident
                for ident, values in portfolio.items()
                if None not in values
                and all(values[i] == label for i, label in labels.items())
            )
            require(
                len(labels) >= rule["minimum_evidence"]
                and compatible
                and len({tuple(portfolio[ident]) for ident in compatible}) == 1
                and result["program_sha256"] == compatible[0],
                "Portfolio reuse was not identified by actual diagnostic outcomes",
            )
    independent = outcome(
        program, rule["heldout_inputs"], rule["heldout_outputs"], config
    )
    comparison = sum(
        units(a, b)
        for a, b in zip(rule["heldout_inputs"], rule["heldout_outputs"], strict=True)
    )
    if program is not None:
        comparison += sum(
            units(a or "", b)
            for a, b in zip(
                independent["outputs"], rule["heldout_outputs"], strict=True
            )
        )
    final_work = independent["tool_units"] + comparison
    acceptance = parsed(task["acceptance"])
    baseline = sum(
        a == b
        for a, b in zip(rule["heldout_inputs"], rule["heldout_outputs"], strict=True)
    ) / len(rule["heldout_inputs"])
    utility = max(
        -1,
        independent["correct"] / independent["rows"]
        - baseline
        - 0.01 * (work["total"] + final_work) / rule["maximum_work"],
    )
    require(
        acceptance["artifact_sha256"]
        == hashlib.sha256(local["delivery"].encode()).hexdigest()
        and acceptance["exact_rows"] == independent["correct"]
        and acceptance["evaluation_rows"] == independent["rows"]
        and acceptance["passed"]
        == (independent["passed"] and not acceptance.get("late"))
        and acceptance["program_sha256"] == result["program_sha256"]
        and acceptance["proposal_id"] == result["proposal_id"]
        and acceptance["selection"] == result["selection"]
        and acceptance["source_id"] == source
        and acceptance["execution_status"] == result["status"]
        and acceptance["probes_used"] == 4
        and acceptance["probe_work"] == work["probe_work"]
        and acceptance["probe_receipt_sha256s"] == result["probe_receipt_sha256s"]
        and acceptance["evaluation_sha256"]
        == digest(
            {
                "inputs": rule["heldout_inputs"],
                "outputs": rule["heldout_outputs"],
                "nonce": contract["evaluation_nonce"],
            }
        )
        and acceptance["session_sha256"] == episode["session_sha256"]
        and acceptance["solver_work"] == work["total"]
        and acceptance["verifier_work"] == final_work
        and acceptance["total_work"]
        == local["work"]
        == work["total"] + final_work
        <= local["budget"]
        == rule["maximum_work"]
        and acceptance["utility"]
        == (min(-0.1, utility) if acceptance.get("late") else utility),
        "Independent final outputs, costs or usefulness differ from buyer acceptance",
    )
    admission = parsed(local["admission"])
    final_library = copy.deepcopy(state["library"])
    if result["proposal_id"] is not None:
        receipt = admission["admission"]
        require(
            receipt
            == {
                "source_id": source,
                "proposal_id": result["proposal_id"],
                "program_sha256": result["program_sha256"],
                "evaluation_id": digest(
                    {"job": task["id"], "contract": digest(contract)}
                ),
                "accepted": independent["passed"],
                "correct_rows": independent["correct"],
                "total_rows": independent["rows"],
                "evidence_sha256": acceptance["evaluation_sha256"],
                "work": final_work,
            },
            "Library admission does not match the independently checked final outcome",
        )
        proposal = final_library["pending"].pop(result["proposal_id"])
        if receipt["accepted"] and acceptance["passed"]:
            old = final_library["programs"].get(result["program_sha256"])
            if (
                old is not None
                or len(final_library["programs"]) < config["max_programs"]
            ):
                final_library["programs"][result["program_sha256"]] = {
                    "program": proposal["program"],
                    "uses": old["uses"] + 1 if old else 1,
                    "features": old["features"] if old else proposal["features"],
                    "evidence_sha256": receipt["evidence_sha256"],
                }
        final_library["total_work"] += receipt["work"]
        final_library["receipts"][result["proposal_id"]] = receipt
        while len(final_library["receipts"]) > 256:
            final_library["receipts"].pop(next(iter(final_library["receipts"])))
    require(
        admission["accepted"] == acceptance["passed"]
        and admission["skill_id"] == result["program_sha256"],
        "Active admission misattributes the selected program",
    )
    return {
        "initial_library": initial,
        "final_library": final_library,
        "contract": contract,
        "intent": intent,
        "program_sha256": result["program_sha256"],
        "selection": result["selection"],
        "probes_used": len(labels),
        "probe_receipt_sha256s": result["probe_receipt_sha256s"],
        "session_sha256": episode["session_sha256"],
        "provider_work": work["total"],
        "buyer_work": final_work,
        "independent": independent,
        "acceptance": acceptance,
    }


def collect(args):
    root = Path(args.source_root)
    release, release_hash = read_json(args.release_file)
    source = source_identity(
        root,
        release,
        args.source_commit,
        {"cognition": args.cognition_service, "commerce": args.commerce_service},
    )
    controls, controls_hash = read_json(args.controls_file)
    adapters = [v for v in controls.get("adapters", []) if v.get("id") == args.provider]
    require(
        len(adapters) == 1
        and adapters[0].get("mode") == "active_composed"
        and adapters[0].get("type") == "local_skills",
        "Expected explicitly enrolled active provider is missing",
    )
    authority = adapters[0]
    require(
        authority["source_id"] == args.skill_source == SOURCE
        and authority["id"] == args.provider == PROVIDER,
        "Dedicated source identity differs",
    )
    course, course_hash = course_evidence(args.course_manifest, authority)
    require(
        args.expected_episodes in (None, course["expected_count"]),
        "Expected episode count differs from the committed course manifest",
    )
    baseline = read_json(args.baseline_json)[0]
    public_commerce, commerce_cache = cache(args.commerce_cache)
    public_cognition, cognition_cache = cache(args.cognition_cache)
    cdb = ndb = None
    try:
        cdb, ndb = sqlite_view(args.commerce_db), sqlite_view(args.cognition_db)
        integrity = commerce_integrity(cdb)
        metadata = parsed(
            ndb.execute("SELECT metadata FROM checkpoint WHERE id=1").fetchone()[0]
        )
        head = ndb.execute(
            "SELECT seq,hash FROM records ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        require(
            head is not None
            and tuple(head) == (metadata["record_seq"], metadata["record_head"]),
            "Neural checkpoint does not match its committed head",
        )
        old = baseline["snapshots"]["cognition"]["checkpoint"]
        previous = ndb.execute(
            "SELECT hash FROM records WHERE seq=?", (old["record_seq"],)
        ).fetchone()
        continuation = {
            "genesis_preserved": old["genesis_utc"] == metadata["genesis_utc"],
            "source_cursor_not_rewound": metadata["cursor"] >= old["cursor"],
            "record_sequence_not_rewound": metadata["record_seq"] >= old["record_seq"],
            "baseline_record_head_retained": previous is not None
            and previous[0] == old["record_head"]
            or old["record_seq"] == 0
            and old["record_head"] == "0" * 64,
        }
        require(
            all(continuation.values()), "Original neural history or genesis changed"
        )
        old_commerce = baseline["snapshots"]["commerce"]["journal"]
        if old_commerce["sequence"]:
            previous = cdb.execute(
                "SELECT hash FROM events WHERE seq=?", (old_commerce["sequence"],)
            ).fetchone()
            require(
                previous is not None and previous[0] == old_commerce["head"],
                "Original commerce prefix changed",
            )
        else:
            require(
                old_commerce["head"] == "0" * 64, "Empty commerce baseline is malformed"
            )
        state_row = cdb.execute(
            "SELECT * FROM local_skill_state WHERE provider=?", (args.provider,)
        ).fetchone()
        require(
            state_row is not None and state_row["source_id"] == args.skill_source,
            "Dedicated retained library is missing",
        )
        library = parsed(state_row["state"])
        check_library(library, args.skill_source, state_row["config_sha256"])
        tasks = cdb.execute(
            "SELECT * FROM action_tasks WHERE provider=? ORDER BY rowid",
            (args.provider,),
        ).fetchall()
        require(0 < len(tasks) <= 512, "Actual episode coverage is empty or unbounded")
        expected_contracts = [item["contract_sha256"] for item in course["tasks"]]
        actual_contracts = [digest(parsed(task["contract"])) for task in tasks]
        require(
            len(actual_contracts) <= len(expected_contracts)
            and len(set(actual_contracts)) == len(actual_contracts)
            and set(actual_contracts) <= set(expected_contracts),
            "Actual provider episodes repeat or fall outside the committed course",
        )
        # Include every reserved provider job, including unresolved execution.
        for day in cdb.execute(
            "SELECT day,count(*) AS jobs,sum(budget) AS work FROM local_skill_jobs WHERE provider=? GROUP BY day",
            (args.provider,),
        ):
            require(
                day["jobs"] <= authority["maximum_jobs_per_day"]
                and day["work"] <= authority["maximum_work_per_day"],
                "Reserved work exceeds the inspected current daily authority",
            )
        jobs, gaps, prior_library = [], [], None
        known_ids = {task["id"] for task in tasks}
        for (ident,) in cdb.execute(
            "SELECT job_id FROM local_skill_jobs WHERE provider=?", (args.provider,)
        ):
            if ident not in known_ids:
                gaps.append(
                    {
                        "job_id": ident,
                        "finding": "A provider reservation has no originating neural episode",
                    }
                )
        for task in tasks:
            local = cdb.execute(
                "SELECT * FROM local_skill_jobs WHERE job_id=? AND provider=?",
                (task["id"], args.provider),
            ).fetchone()
            if (
                task["state"] != "evaluated"
                or local is None
                or local["state"] != "evaluated"
            ):
                gaps.append(
                    {
                        "job_id": task["id"],
                        "finding": "Episode is unfinished or execution accounting remains uncertain",
                    }
                )
                continue
            checked = inspect_episode(cdb, task, local, authority, root)
            if prior_library is not None:
                require(
                    checked["initial_library"] == prior_library,
                    "An episode replaced earlier retained learning",
                )
            else:
                require(
                    not checked["initial_library"]["programs"]
                    and not checked["initial_library"]["receipts"]
                    and not checked["initial_library"]["pending"]
                    and checked["initial_library"]["sequence"] == 0
                    and checked["initial_library"]["total_work"] == 0,
                    "The separate composed course did not begin with a fresh checked library",
                )
            prior_library = checked["final_library"]
            contract, intent, acceptance = (
                checked["contract"],
                checked["intent"],
                checked["acceptance"],
            )
            proposal = cdb.execute(
                "SELECT payload FROM events WHERE json_extract(payload,'$.action_task_id')=? AND json_extract(payload,'$.task_state')='proposed'",
                (task["id"],),
            ).fetchall()
            require(len(proposal) == 1, "Episode has no unique originating proposal")
            original = parsed(proposal[0][0])
            row = ndb.execute(
                "SELECT seq,previous_hash,hash,payload FROM records WHERE hash=?",
                (original["origin_decision_hash"],),
            ).fetchone()
            require(
                row is not None and record_hash(row).get("intent") == intent,
                "Original neural decision differs from this episode",
            )
            result, settlement = parsed(task["outcome"]), parsed(task["settlement"])
            expected_reward = (
                acceptance["utility"]
                if acceptance["passed"]
                else min(0, acceptance["utility"])
            )
            require(
                settlement["basis"] == "included_capacity"
                and settlement["total_micro_usd"] == 0
                and settlement["job_id"] == task["id"],
                "Episode has an unexpected financial settlement",
            )
            require(
                result["result_sha256"] == acceptance["artifact_sha256"]
                and result["reward"] == expected_reward
                and result["cost_micro_usd"] == 0
                and result["evidence_level"] == "verified_outcome",
                "Outcome is not the checked zero-payment result",
            )
            feedback = cdb.execute(
                "SELECT seq,hash,payload FROM events WHERE json_extract(payload,'$.action_task_id')=? AND json_extract(payload,'$.task_state')='evaluated'",
                (task["id"],),
            ).fetchall()
            require(
                len(feedback) == 1, "Episode has duplicate or missing final feedback"
            )
            neural = ndb.execute(
                "SELECT seq,previous_hash,hash,payload FROM records WHERE kind='task_outcome' AND json_extract(payload,'$.intent_id')=?",
                (task["intent_id"],),
            ).fetchall()
            if (
                not neural
                or metadata.get("agency", {}).get("feedback_cursor", 0)
                < feedback[0]["seq"]
            ):
                gaps.append(
                    {
                        "job_id": task["id"],
                        "finding": "Final outcome is not acknowledged by the committed neural checkpoint",
                    }
                )
                continue
            require(
                len(neural) == 1, "One episode received multiple neural outcome credits"
            )
            credit = record_hash(neural[0])
            require(
                all(
                    credit.get(key) == result[key]
                    for key in (
                        "intent_id",
                        "goal_id",
                        "action",
                        "contract_sha256",
                        "reward",
                        "result_sha256",
                    )
                )
                and credit["seq"] == feedback[0]["seq"],
                "Neural feedback differs from its originating episode result",
            )
            synapses = credit["neural_credit"]
            require(
                0 <= synapses["changed_synapses"] <= synapses["tagged_synapses"] <= 256
                and all(
                    HASH.fullmatch(synapses[key])
                    for key in ("before_state_sha256", "after_state_sha256")
                ),
                "Origin-bound neural credit is malformed",
            )
            jobs.append(
                {
                    "job_id": task["id"],
                    "intent_id": task["intent_id"],
                    "contract_sha256": digest(contract),
                    "origin_decision_sha256": original["origin_decision_hash"],
                    "origin_neural_state_sha256": intent["neural_state_sha256"],
                    "session_sha256": checked["session_sha256"],
                    "program_sha256": checked["program_sha256"],
                    "selection": checked["selection"],
                    "probes_used": checked["probes_used"],
                    "probe_receipt_sha256s": checked["probe_receipt_sha256s"],
                    "passed": acceptance["passed"],
                    "exact_rows": checked["independent"]["correct"],
                    "evaluation_rows": checked["independent"]["rows"],
                    "actual_outputs_sha256": checked["independent"]["actual_sha256"],
                    "artifact_sha256": acceptance["artifact_sha256"],
                    "provider_work": checked["provider_work"],
                    "buyer_final_work": checked["buyer_work"],
                    "total_work": local["work"],
                    "reward": credit["reward"],
                    "commerce_feedback_sequence": feedback[0]["seq"],
                    "commerce_feedback_hash": feedback[0]["hash"],
                    "neural_outcome_sequence": neural[0]["seq"],
                    "neural_outcome_sha256": neural[0]["hash"],
                    "changed_synapses_recorded": synapses["changed_synapses"],
                    "tagged_synapses_recorded": synapses["tagged_synapses"],
                }
            )
        if prior_library is not None and not gaps:
            require(
                library == prior_library,
                "Current library differs from actual checked admissions",
            )
            require(
                not library["pending"],
                "Completed course leaves an unconsumed library proposal",
            )
        financial = financial_counts(cdb)
        no_payment = (
            financial["authorizations_count"]
            == financial["signing_requests_count"]
            == financial["settled_payment_transaction_count"]
            == 0
        )
        policy_disabled = (
            (public_commerce.get("policy") or {}).get("enabled") is False
            and public_commerce.get("financial_execution") is False
            and public_commerce.get("actions", {}).get("execution_enabled") is False
        )
        acquired, reused = (
            sum(v["selection"] == name and v["passed"] for v in jobs)
            for name in ("synthesized", "retained")
        )
        complete = (
            not gaps
            and len(jobs) == len(tasks)
            and policy_disabled
            and no_payment
            and commerce_cache["fresh"]
            and cognition_cache["fresh"]
            and public_cognition.get("phase") == "running"
            and acquired >= args.minimum_acquired
            and reused >= args.minimum_reused
            and len(tasks) == course["expected_count"]
            and set(actual_contracts) == set(expected_contracts)
        )
        certificate = {
            "schema": "nuria.live-composed-skill-certificate.v1",
            "observed_utc": datetime.now(timezone.utc).isoformat(),
            "complete": complete,
            "complete_meaning": "All selected episodes, including rejected outcomes, have checked diagnostic exchanges, fixed final outputs and one acknowledged neural outcome. Completeness does not mean every episode succeeded.",
            "provider": args.provider,
            "source_id": args.skill_source,
            "source": source,
            "verification_sources": {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in (
                    "scripts/verify_live_composed_skills.py",
                    "scripts/verify_live_skills.py",
                    "scripts/verify_active_skill_results.py",
                )
            },
            "release_metadata_sha256": release_hash,
            "authority_sha256": controls_hash,
            "course": {
                "manifest_sha256": course_hash,
                "expected_episodes": course["expected_count"],
                "committed_contract_order_sha256": digest(expected_contracts),
                "actual_episode_order_sha256": digest(actual_contracts),
                "scope": "Finite local generated engineering service. Manifest order is a source commitment; execution can interleave goal lanes. Library continuity follows actual chronological episodes. Included compute, not customer requests, paid hiring or fresh generalization evidence",
            },
            "authority_scope": "Every retained reservation fits the inspected current authority. Historical authority-file identity is not attested.",
            "counts": {
                "actual_episodes": len(tasks),
                "checked_and_neurally_acknowledged": len(jobs),
                "acquired_selections": acquired,
                "retained_selections": reused,
                "failed_episodes_retained": sum(not v["passed"] for v in jobs),
                "diagnostic_labels_disclosed": sum(v["probes_used"] for v in jobs),
                "final_rows": sum(v["evaluation_rows"] for v in jobs),
                "exact_final_rows": sum(v["exact_rows"] for v in jobs),
                "provider_work": sum(v["provider_work"] for v in jobs),
                "buyer_final_work": sum(v["buyer_final_work"] for v in jobs),
            },
            "commerce_integrity": integrity,
            "cognition": {
                "genesis_utc": timestamp(metadata["genesis_utc"]),
                "checkpoint_record_sequence": metadata["record_seq"],
                "checkpoint_record_head": metadata["record_head"],
                "history_continuation": continuation,
            },
            "financial": {
                "policy_disabled_in_fresh_cache": policy_disabled,
                "new_financial_activity": 0 if no_payment else None,
                **financial,
            },
            "cache_evidence": {
                "commerce": commerce_cache,
                "cognition": cognition_cache,
            },
            "jobs": jobs,
            "gaps": gaps,
            "scope": "Separate read-only WAL views, local journal commitments and an independent text interpreter. No final targets enter routing. Search internals and physical CPU are not independently replayed; declared search work and all operation reservations are checked. No external witness, chain-wide wallet audit, synaptic-delta reconstruction, neural advantage or consciousness result is established. Private values, program bodies, nonces and filesystem paths are excluded.",
        }
        return {
            "schema": "nuria.live-composed-skill-private.v1",
            "read_only_sources": {
                "commerce": str(args.commerce_db),
                "cognition": str(args.cognition_db),
            },
            "certificate_sha256": digest(certificate),
            "certificate": certificate,
        }, certificate
    finally:
        close(cdb)
        close(ndb)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "data-root",
        "public-root",
        "release-file",
        "source-root",
        "source-commit",
        "provider",
        "skill-source",
        "controls-file",
        "baseline-json",
        "output-directory",
        "course-manifest",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--cognition-service", default="nuria-cognition.service")
    parser.add_argument("--commerce-service", default="nuria-commerce.service")
    parser.add_argument("--minimum-acquired", type=int, default=1)
    parser.add_argument("--minimum-reused", type=int, default=1)
    parser.add_argument("--expected-episodes", type=int)
    args = configure_paths(parser.parse_args())
    destination = Path(args.output_directory)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        private, public = collect(args)
        write_new(destination / "private-evidence.json", private)
        write_new(destination / "safe-public-certificate.json", public)
        print(
            canonical(
                {
                    "complete": public["complete"],
                    "counts": public["counts"],
                    "certificate_sha256": digest(public),
                }
            )
        )
        return 0 if public["complete"] else 2
    except BaseException as error:
        write_new(
            destination / "collection-failure.json",
            {
                "phase": "evidence_incomplete",
                "exception_type": type(error).__name__,
                "scope": "No successful verification is established. Inspect trusted invocation details privately.",
            },
        )
        print(
            canonical(
                {
                    "complete": False,
                    "phase": "evidence_incomplete",
                    "exception_type": type(error).__name__,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
