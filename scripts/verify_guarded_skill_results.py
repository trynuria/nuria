"""Check generated guarded-skill evidence without importing a learner/evaluator.

This checker independently executes data-only ASTs and reconstructs membership,
receipts, resource arithmetic and complete paired coverage. It does not replay
synthesis, independently witness CPU, or attest to the physical completeness of
instrumentation. A public freeze commitment is separate provenance evidence.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

from scripts.verify_active_skill_results import digest, outcome, serial

ROOT = Path(__file__).resolve().parents[1]
HASH = re.compile(r"[a-f0-9]{64}\Z")
METHODS = {
    "old_fixed2",
    "old_fixed4",
    "composed_fixed2",
    "composed_fixed3",
    "composed_fixed4",
    "enumerative_fixed3",
    "enumerative_fixed4",
    "guarded",
    "frozen_guarded",
}
COSTS = {
    "portfolio_work",
    "routing_work",
    "probe_work",
    "synthesis_work",
    "audit_work",
    "checkpoint_work",
}
COUNTERS = {
    "candidate_evaluations",
    "planning_operations",
    "probe_requests",
    "primitive_attempts",
    "candidates",
    "byte_blocks",
    "errors",
    "semantic_duplicates",
    "constraint_operations",
    "parse_operations",
    "checkpoint_operations",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, maximum=2**53 - 1):
    require(type(value) is int and 0 <= value <= maximum, "Invalid finite counter")
    return value


def units(value):
    return 1 + (len(serial(value).encode()) + 31) // 32


def probe_cost(value, answer):
    return 1 + (len(value.encode()) + len(answer.encode()) + 31) // 32


def read(directory, name):
    path = directory / name
    if path.is_file():
        raw = path.read_bytes()
    else:
        # Generated study traces may be distributed losslessly compressed. The
        # same decompressed input bound applies; no learner is needed to check.
        with gzip.open(path.with_name(name + ".gz"), "rb") as stream:
            raw = stream.read(512 * 1024 * 1024 + 1)
    require(len(raw) <= 512 * 1024 * 1024, "Evidence file exceeds its input bound")
    return json.loads(raw)


def library(state, source, config):
    require(
        type(state) is dict
        and set(state)
        == {
            "version",
            "source_id",
            "config_sha256",
            "sequence",
            "total_work",
            "programs",
            "pending",
            "receipts",
            "macros",
        }
        and state["source_id"] == source
        and state["config_sha256"] == digest(config)
        and state["macros"] == {},
        "Library identity or authority changed",
    )
    integer(state["sequence"])
    integer(state["total_work"])
    for name in ("programs", "pending", "receipts"):
        require(type(state[name]) is dict, "Library collection changed")
    require(
        len(state["programs"]) <= config["max_programs"], "Library exceeds capacity"
    )
    for identity, record in state["programs"].items():
        require(
            HASH.fullmatch(identity) and identity == digest(record["program"]),
            "Retained program commitment changed",
        )
        integer(record["uses"])
        require(record["uses"] >= 1, "Retained program has no checked use")
    for identity, pending in state["pending"].items():
        require(
            identity
            == digest(
                {
                    "source_id": source,
                    "config_sha256": digest(config),
                    **pending,
                }
            )
            and pending["program_sha256"] == digest(pending["program"]),
            "Pending proposal commitment changed",
        )
    for identity, receipt in state["receipts"].items():
        require(
            identity == receipt["proposal_id"]
            and receipt["source_id"] == source
            and type(receipt["accepted"]) is bool
            and receipt["accepted"]
            == (receipt["correct_rows"] == receipt["total_rows"]),
            "Library buyer receipt changed",
        )
        require(identity not in state["pending"], "Consumed proposal remains pending")


def check_session(row, job, config, limits):
    session = row["session"]
    require(type(session) is dict, "Guarded episode omits its private checkpoint")
    saved = {key: value for key, value in session.items() if key != "state_sha256"}
    require(session["state_sha256"] == digest(saved), "Session checksum changed")
    terms = session["terms"]
    library(session["library"], "nuria:guarded-study-v1", config)
    require(
        session["session_id"] == digest(terms)
        and session["inputs"] == job["diagnostic_inputs"]
        and terms["inputs_sha256"] == digest(job["diagnostic_inputs"])
        and terms["config_sha256"] == digest(config)
        and terms["initial_library_sha256"] == row["library_before_sha256"]
        and terms["source_id"] == "nuria:guarded-study-v1"
        and all(terms["limits"][key] == value for key, value in limits.items()),
        "Session source, inputs or limits changed",
    )
    require(
        session["pending"] is None
        and not session["accounting_uncertain"]
        and session["uncertain_reservation"] == 0,
        "Guarded accounting or physical tool completion is unresolved",
    )
    work = session["work"]
    require(work == row["controller_work"], "Controller checkpoint work changed")
    require(set(work) == {"total", *COSTS, *COUNTERS}, "Unknown controller work field")
    reconstructed = {key: 0 for key in work}
    for charge in session["charges"]:
        require(
            set(charge) == {"category", "amount", "counters"}
            and charge["category"] in COSTS
            and type(charge["counters"]) is dict
            and set(charge["counters"]) <= COUNTERS,
            "Controller charge authority changed",
        )
        amount = integer(charge["amount"])
        reconstructed[charge["category"]] += amount
        reconstructed["total"] += amount
        for key, value in charge["counters"].items():
            reconstructed[key] += integer(value)
    require(reconstructed == work, "Controller charges do not add up")
    require(
        work["total"] <= limits["maximum_work"]
        and work["synthesis_work"] <= limits["synthesis_work"]
        and work["portfolio_work"] <= terms["limits"]["lookup_work"],
        "Controller exceeds its shared allowance",
    )
    actions, action_ids, probe_indices, synthesis_ids = row["actions"], set(), [], set()
    for action in actions:
        require(
            action["id"]
            == digest({key: value for key, value in action.items() if key != "id"})
            and action["session_id"] == session["session_id"]
            and action["id"] not in action_ids,
            "Action commitment repeats or changed",
        )
        action_ids.add(action["id"])
        if action["kind"] == "probe":
            index = integer(action["input_index"], 5)
            require(
                set(action)
                == {
                    "id",
                    "session_id",
                    "sequence",
                    "kind",
                    "input_index",
                    "input",
                    "input_sha256",
                    "maximum_output_bytes",
                    "maximum_work",
                }
                and action["input"] == job["diagnostic_inputs"][index]
                and action["input_sha256"] == digest(action["input"])
                and action["maximum_output_bytes"] == config["max_input_bytes"]
                and action["maximum_work"]
                == probe_cost(action["input"], "x" * config["max_input_bytes"]),
                "Probe exposes an unintended input or has altered authority",
            )
            probe_indices.append(index)
            record = session["probe_receipts"].get(action["id"])
            require(
                record is not None and record["action"] == action,
                "Probe receipt missing",
            )
            receipt = record["receipt"]
            require(
                set(receipt)
                == {
                    "source_id",
                    "input_index",
                    "input_sha256",
                    "output",
                    "receipt_id",
                    "work",
                }
                and receipt["source_id"] == terms["source_id"]
                and receipt["input_index"] == index
                and receipt["input_sha256"] == action["input_sha256"]
                and receipt["output"] == job["diagnostic_outputs"][index]
                and receipt["work"] == probe_cost(action["input"], receipt["output"])
                and record["sha256"] == digest(receipt),
                "Probe answer, source or cost changed",
            )
        elif action["kind"] == "synthesize":
            require(
                set(action) == {"id", "session_id", "sequence", "kind", "maximum_work"}
                and 1 <= action["maximum_work"] <= limits["synthesis_work"],
                "Synthesis tool authority changed",
            )
            synthesis_ids.add(action["id"])
        else:
            require(
                action["kind"] in ("propose", "abstain")
                and set(action) == {"id", "session_id", "sequence", "kind", "reason"},
                "Unknown terminal action fields",
            )
    require(
        actions
        and actions[-1]["kind"]
        == ("propose" if row["program"] is not None else "abstain")
        and all(action["kind"] in ("probe", "synthesize") for action in actions[:-1]),
        "Terminal tool chronology changed",
    )
    require(probe_indices == row["probe_indices"], "Probe action order changed")
    require(
        len(session["probe_receipts"]) == len(probe_indices)
        and session["labels"]
        == {str(index): job["diagnostic_outputs"][index] for index in probe_indices}
        and len(
            {
                entry["receipt"]["receipt_id"]
                for entry in session["probe_receipts"].values()
            }
        )
        == len(probe_indices),
        "Checkpoint contains unrequested labels or duplicate receipts",
    )
    require(
        work["probe_work"]
        == sum(entry["receipt"]["work"] for entry in session["probe_receipts"].values())
        and work["probe_requests"] == len(probe_indices),
        "Probe charges changed",
    )
    attempts = session.get("synthesis_attempts")
    if attempts is None:
        attempts = (
            []
            if session["synthesis"] is None
            else [
                {
                    "action": session["synthesis_action"],
                    "proposal": session["synthesis"],
                    "training_indices": sorted(
                        int(index) for index in session["labels"]
                    ),
                }
            ]
        )
    require(type(attempts) is list and len(attempts) <= 2, "Unknown synthesis attempts")
    require(
        {attempt["action"]["id"] for attempt in attempts} == synthesis_ids,
        "Attempted synthesis is omitted from checkpoint",
    )
    synthesis_total = 0
    for attempt in attempts:
        proposal, indices = attempt["proposal"], attempt["training_indices"]
        available_indices = sorted(
            action["input_index"]
            for action in actions
            if action["kind"] == "probe"
            and action["sequence"] < attempt["action"]["sequence"]
        )
        require(
            indices == sorted(set(indices))
            and indices == available_indices
            and len(indices) >= limits["minimum_evidence"]
            and all(index in probe_indices for index in indices),
            "Synthesis uses labels outside actual requested evidence",
        )
        if "training_sha256" in attempt:
            require(
                attempt["training_sha256"]
                == digest(
                    [
                        [job["diagnostic_inputs"][index] for index in indices],
                        [job["diagnostic_outputs"][index] for index in indices],
                    ]
                ),
                "Synthesis training commitment changed",
            )
        require(
            proposal["program_sha256"]
            == (None if proposal["program"] is None else digest(proposal["program"])),
            "Synthesis program commitment changed",
        )
        synthesis_total += integer(proposal["work"]["total"])
        require(
            proposal["work"]["total"] <= attempt["action"]["maximum_work"],
            "Synthesis executed beyond reservation",
        )
    require(synthesis_total == work["synthesis_work"], "Failed synthesis cost omitted")
    history = {entry["action_id"]: entry for entry in session["history"]}
    require(
        len(history) == len(session["history"])
        and set(history) == {action["id"] for action in actions[:-1]},
        "Attempt history omits or repeats a tool",
    )
    for action in actions[:-1]:
        if action["kind"] == "probe":
            binding = session["probe_receipts"][action["id"]]["receipt"]
            amount = binding["work"]
        else:
            binding = next(
                attempt["proposal"]
                for attempt in attempts
                if attempt["action"]["id"] == action["id"]
            )
            amount = binding["work"]["total"]
        require(
            history[action["id"]]
            == {
                "action_id": action["id"],
                "kind": action["kind"],
                "binding_sha256": digest(binding),
                "work": amount,
            },
            "Attempt receipt or charged history changed",
        )
    rejections = session.get("diagnostic_rejections", [])
    require(
        type(rejections) is list and len(rejections) <= 2,
        "Unknown diagnostic rejections",
    )
    attempts_by_id = {
        attempt["proposal"]["proposal_id"]: attempt["proposal"]
        for attempt in attempts
        if attempt["proposal"]["proposal_id"] is not None
    }
    rejection_ids = set()
    for rejected in rejections:
        ident = rejected["proposal_id"]
        require(
            ident in attempts_by_id and ident not in rejection_ids,
            "Rejection has no unique attempted program",
        )
        rejection_ids.add(ident)
        index = integer(rejected["input_index"], 5)
        require(index in probe_indices, "Rejection uses an unrequested answer")
        proposal = attempts_by_id[ident]
        checked = outcome(
            proposal["program"],
            [job["diagnostic_inputs"][index]],
            [job["diagnostic_outputs"][index]],
            config,
        )
        expected_evidence = {
            "input": job["diagnostic_inputs"][index],
            "expected": job["diagnostic_outputs"][index],
            "observed": checked["outputs"][0],
            "error": rejected["error"],
        }
        require(
            not checked["passed"]
            and rejected["evidence"] == expected_evidence
            and rejected["program_sha256"] == proposal["program_sha256"]
            and rejected["input_sha256"] == digest(expected_evidence["input"])
            and rejected["expected_sha256"] == digest(expected_evidence["expected"])
            and rejected["observed_sha256"] == digest(expected_evidence["observed"]),
            "Diagnostic rejection is not an independently observed failure",
        )
        receipt = rejected["receipt"]
        require(
            receipt == session["library"]["receipts"].get(ident)
            and receipt == row["library_after"]["receipts"].get(ident)
            and receipt["accepted"] is False
            and receipt["correct_rows"] == 0
            and receipt["total_rows"] == 1
            and receipt["evidence_sha256"] == digest(expected_evidence)
            and receipt["work"] == rejected["work"]["total"]
            and receipt["work"]
            == checked["execution_work"] + int(checked["outputs"][0] is None),
            "Failed diagnostic proposal lacks its charged negative receipt",
        )
    require(
        session.get("diagnostic_rejection") == (rejections[-1] if rejections else None),
        "Latest diagnostic rejection changed",
    )
    allowed_receipts = set(row["library_before"]["receipts"]) | rejection_ids
    require(
        set(session["library"]["receipts"]) == allowed_receipts,
        "Session contains an unobserved buyer receipt",
    )
    require(
        set(session["library"]["pending"])
        == (
            {row["proposal_id"]}
            if row["proposal_id"]
            else {
                entry["proposal_id"] for entry in row.get("abstention_dispositions", [])
            }
        ),
        "Session leaves an unaccounted provisional program",
    )
    require(
        session["library"]["programs"] == row["library_before"]["programs"],
        "Session positively admits before final buyer acceptance",
    )
    require(
        session["library"]["total_work"]
        == row["library_before"]["total_work"]
        + synthesis_total
        + sum(rejected["receipt"]["work"] for rejected in rejections),
        "Library omits failed search or diagnostic rejection work",
    )


def summarize(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[row["method"]].append(row)
    return {
        method: {
            "jobs": len(rows),
            "exact_successes": sum(row["checked"]["passed"] for row in rows),
            "proposed": sum(row["program"] is not None for row in rows),
            "wrong_proposals": sum(
                row["program"] is not None and not row["checked"]["passed"]
                for row in rows
            ),
            "abstentions": sum(row["program"] is None for row in rows),
            "labels": sum(len(row["probe_indices"]) for row in rows),
            "final_rows": sum(row["checked"]["rows"] for row in rows),
            "exact_final_rows": sum(row["checked"]["correct"] for row in rows),
            "total_work": sum(row["total_work"] for row in rows),
            "core_cpu_seconds": sum(row["core_cpu_seconds"] for row in rows),
            "cpu_seconds": sum(row["cpu_seconds"] for row in rows),
            "retained_selections": sum(
                row["selection"] == "retained" and row["checked"]["passed"]
                for row in rows
            ),
        }
        for method, rows in grouped.items()
    }


def matching_totals(expected, actual):
    require(set(expected) == set(actual), "Summary method coverage changed")
    for method, values in expected.items():
        require(set(values) == set(actual[method]), "Summary fields changed")
        for key, value in values.items():
            observed = actual[method][key]
            require(
                math.isclose(value, observed, rel_tol=1e-12, abs_tol=1e-12)
                if key.endswith("cpu_seconds")
                else type(observed) is int and value == observed,
                "Summary total changed: " + method + "/" + key,
            )


def verify(directory, *, source_root=ROOT):
    directory = Path(directory)
    started, corpora, traces, summary = (
        read(directory, name)
        for name in ("started.json", "corpora.json", "traces.json", "summary.json")
    )
    require(
        summary["schema"] == "nuria.guarded-skill-study.v1"
        and summary["phase"] == started["phase"]
        and summary["phase"] in ("development", "reserved")
        and summary["source_hashes"] == started["source_hashes"]
        and summary["corpora_sha256"] == digest(corpora)
        and summary["traces_sha256"] == digest(traces),
        "Evidence manifests or commitments changed",
    )
    require(started["source_hashes"], "Study omits evaluated source identities")
    for name, expected in started["source_hashes"].items():
        relative = Path(name)
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and HASH.fullmatch(expected)
            and hashlib.sha256((Path(source_root) / relative).read_bytes()).hexdigest()
            == expected,
            "Evaluated source snapshot changed",
        )
    seeds, methods, count = started["seeds"], started["methods"], started["jobs"]
    require(
        len(seeds) == len(set(seeds))
        and len(methods) == len(set(methods))
        and 1 <= len(seeds) <= 32
        and set(methods) <= METHODS
        and len(methods) >= 1
        and 1 <= count <= 128,
        "Study coverage authority changed",
    )
    limits = started["limits"]
    require(limits["maximum_probes"] <= 4, "Probe authority exceeds frozen ceiling")
    jobs, configs, per_seed_jobs = {}, {}, {}
    require(type(corpora) is list and len(corpora) == len(seeds), "Missing seed corpus")
    for corpus, seed in zip(corpora, seeds, strict=True):
        require(
            corpus["source"]["seed"] == seed and len(corpus["tasks"]) == count,
            "Corpus seed or denominator changed",
        )
        configs[seed], per_seed_jobs[seed] = corpus["config"], []
        for ordinal, job in enumerate(corpus["tasks"]):
            require(
                len(job["diagnostic_inputs"]) == len(job["diagnostic_outputs"]) == 6
                and len(job["score_inputs"]) == len(job["score_outputs"]) == 24
                and len(set(job["diagnostic_inputs"] + job["score_inputs"])) == 30,
                "Diagnostic/final partition overlaps or changes its denominator",
            )
            require(
                len(set(job["row_ids"]["diagnostic"] + job["row_ids"]["score"])) == 30,
                "Source row identities repeat",
            )
            require(
                len(job["row_ids"]["diagnostic"]) == 6
                and len(job["row_ids"]["score"]) == 24
                and job["id"]
                == digest(
                    {
                        "schema": corpus["schema"],
                        "seed": seed,
                        "ordinal": ordinal,
                        "row_ids": job["row_ids"]["diagnostic"]
                        + job["row_ids"]["score"],
                        "inputs_sha256": digest(
                            job["diagnostic_inputs"] + job["score_inputs"]
                        ),
                    }
                )
                and job["private_recipe"]["program_sha256"]
                == digest(job["private_recipe"]["program"]),
                "Original task or recipe provenance commitment changed",
            )
            oracle = outcome(
                job["private_recipe"]["program"],
                job["diagnostic_inputs"] + job["score_inputs"],
                job["diagnostic_outputs"] + job["score_outputs"],
                corpus["config"],
            )
            require(oracle["passed"], "Independent target/recipe reconstruction failed")
            key = (seed, job["id"])
            require(key not in jobs, "Duplicate original job")
            jobs[key] = (ordinal, job)
            per_seed_jobs[seed].append(job["id"])
    expected = {
        (seed, method, job)
        for seed in seeds
        for method in methods
        for job in per_seed_jobs[seed]
    }
    seen, states, positions, public = set(), {}, defaultdict(int), []
    for row in traces:
        key = (row["seed"], row["method"], row["job_id"])
        require(
            key in expected and key not in seen, "Duplicate or undeclared job result"
        )
        seen.add(key)
        seed, method, identity = key
        ordinal, job = jobs[(seed, identity)]
        branch = (seed, method)
        require(
            row["ordinal"] == ordinal == positions[branch],
            "Within-branch task chronology changed",
        )
        positions[branch] += 1
        config = configs[seed]
        before, after = row["library_before"], row["library_after"]
        for state in (before, after):
            library(state, "nuria:guarded-study-v1", config)
        require(
            not after["pending"] and not before["pending"],
            "Unresolved proposal in completed task",
        )
        require(
            row["library_before_sha256"] == digest(before)
            and row["library_after_sha256"] == digest(after)
            and row["programs_before"] == sorted(before["programs"])
            and row["programs_after"] == sorted(after["programs"]),
            "Library commitments or membership changed",
        )
        if branch in states:
            require(states[branch] == before, "Retained library history discontinuity")
        else:
            require(
                not before["programs"]
                and not before["receipts"]
                and before["sequence"] == 0
                and before["total_work"] == 0,
                "Initial library is not empty",
            )
        states[branch] = after
        program, program_hash = row["program"], row["program_sha256"]
        require(
            program_hash == (None if program is None else digest(program)),
            "Selected program commitment changed",
        )
        probes = row["probe_indices"]
        require(
            type(probes) is list
            and len(probes) == len(set(probes)) <= limits["maximum_probes"]
            and all(type(index) is int and 0 <= index < 6 for index in probes),
            "Diagnostic label index repeats or exceeds its authority",
        )
        require(row["family"] == job["family"], "Task family attribution changed")
        require(
            row["committed_proposal_sha256"]
            == digest({"job": identity, "program": program, "probes": probes}),
            "Committed proposal changed",
        )
        checked = outcome(program, job["score_inputs"], job["score_outputs"], config)
        require(
            row["checked"] == checked, "Independent final outputs or buyer work changed"
        )
        cost = row["controller_work"]
        for value in cost.values():
            integer(value)
        if "fixed" in method:
            required = int(method.rsplit("fixed", 1)[1])
            require(
                probes == list(range(required))
                and row["session"] is None
                and row["actions"] == [],
                "Fixed-example information boundary changed",
            )
            require(
                set(cost) == {"probe_work", "synthesis_work", "routing_work", "total"},
                "Unknown fixed-controller cost",
            )
            require(
                cost["total"]
                == cost["probe_work"] + cost["synthesis_work"] + cost["routing_work"]
                and cost["routing_work"] == 0,
                "Fixed-controller cost arithmetic changed",
            )
        else:
            check_session(row, job, config, limits)
        require(
            cost["probe_work"]
            == sum(
                probe_cost(job["diagnostic_inputs"][i], job["diagnostic_outputs"][i])
                for i in probes
            ),
            "Disclosed label cost changed",
        )
        require(
            cost["total"] <= limits["maximum_work"]
            and cost["synthesis_work"] <= limits["synthesis_work"],
            "Job exceeds its shared allowance",
        )
        if row["selection"] == "retained":
            require(
                program_hash in before["programs"],
                "Retained selection was not previously checked",
            )
        admission = row["admission"]
        if row["proposal_id"] is None:
            require(admission is None, "Unproposed program was admitted")
        else:
            require(
                admission is not None
                and admission == after["receipts"].get(row["proposal_id"])
                and admission["proposal_id"] == row["proposal_id"]
                and admission["program_sha256"] == program_hash
                and admission["accepted"] == checked["passed"]
                and admission["correct_rows"] == checked["correct"]
                and admission["total_rows"] == checked["rows"]
                and admission["evidence_sha256"] == digest(checked)
                and admission["work"] == checked["total_work"],
                "Positive admission lacks exact independent buyer acceptance",
            )
        dispositions = row.get("abstention_dispositions", [])
        require(
            type(dispositions) is list and len(dispositions) <= 2,
            "Unknown abstention dispositions",
        )
        require(
            not dispositions or program is None and row["session"] is not None,
            "A delivered proposal cannot acquire hidden abstention dispositions",
        )
        disposition_ids, disposition_work = set(), 0
        for disposition in dispositions:
            require(
                type(disposition) is dict
                and set(disposition)
                == {"proposal_id", "program_sha256", "program", "checked", "receipt"},
                "Abstention disposition fields changed",
            )
            ident = disposition["proposal_id"]
            require(ident not in disposition_ids, "Abstention disposition repeats")
            disposition_ids.add(ident)
            pending = row["session"]["library"]["pending"].get(ident)
            require(
                pending is not None
                and pending["program"] == disposition["program"]
                and pending["program_sha256"] == disposition["program_sha256"],
                "Abstention disposition does not bind its actual pending artifact",
            )
            disposal_checked = outcome(
                disposition["program"],
                job["score_inputs"],
                job["score_outputs"],
                config,
            )
            require(
                disposal_checked == disposition["checked"],
                "Abstention disposition misreports independent correctness",
            )
            receipt = disposition["receipt"]
            require(
                receipt == after["receipts"].get(ident)
                and receipt["proposal_id"] == ident
                and receipt["program_sha256"] == disposition["program_sha256"]
                and receipt["source_id"] == before["source_id"]
                and type(receipt["accepted"]) is bool
                and receipt["accepted"] == disposal_checked["passed"]
                and receipt["correct_rows"] == disposal_checked["correct"]
                and receipt["total_rows"] == disposal_checked["rows"]
                and receipt["evidence_sha256"] == digest(disposal_checked)
                and receipt["work"] == disposal_checked["total_work"],
                "Abstention disposition receipt misstates final buyer evidence",
            )
            disposition_work += disposal_checked["total_work"]
        expected_programs = set(before["programs"])
        if (
            admission
            and checked["passed"]
            and method != "frozen_guarded"
            and len(expected_programs) < config["max_programs"]
        ):
            expected_programs.add(program_hash)
        require(
            set(after["programs"]) == expected_programs,
            "Program membership changed without exact accepted admission",
        )
        diagnostic_receipts = (
            []
            if row["session"] is None
            else [
                item["receipt"]
                for item in row["session"].get("diagnostic_rejections", [])
            ]
        )
        expected_receipts = {
            **before["receipts"],
            **{item["proposal_id"]: item for item in diagnostic_receipts},
            **{item["proposal_id"]: item["receipt"] for item in dispositions},
        }
        if admission is not None:
            expected_receipts[row["proposal_id"]] = admission
        require(
            after["receipts"] == expected_receipts,
            "Library receipt history changed outside actual checks",
        )
        require(
            after["total_work"]
            == before["total_work"]
            + cost["synthesis_work"]
            + sum(item["work"] for item in diagnostic_receipts)
            + disposition_work
            + (admission["work"] if admission else 0),
            "Library total omits attempted or rejected work",
        )
        require(
            row["administration_work"]
            == units(before) + units(after) + units(row["session"]),
            "State administration work changed",
        )
        require(
            row["total_work"]
            == cost["total"]
            + checked["total_work"]
            + row["administration_work"]
            + disposition_work,
            "Whole-job work arithmetic changed",
        )
        for name in ("cpu_seconds", "core_cpu_seconds"):
            require(
                type(row[name]) in (int, float)
                and math.isfinite(row[name])
                and row[name] >= 0,
                "CPU measurement is not finite",
            )
        require(
            row["cpu_seconds"] + 1e-12 >= row["core_cpu_seconds"],
            "Core CPU exceeds complete pipeline CPU",
        )
        public.append(
            {
                "seed": seed,
                "ordinal": ordinal,
                "method": method,
                "job_id": identity,
                "family": row["family"],
                "program_sha256": program_hash,
                "proposal_sha256": row["committed_proposal_sha256"],
                "selection": row["selection"],
                "labels": len(probes),
                "exact": checked["passed"],
                "correct_rows": checked["correct"],
                "rows": checked["rows"],
                "work": row["total_work"],
                "program_count_before": len(before["programs"]),
                "program_count_after": len(after["programs"]),
                "abstention_disposition_count": len(dispositions),
                "abstention_disposition_work": disposition_work,
            }
        )
    require(seen == expected, "Missing failed or successful paired job result")
    totals = summarize(traces)
    matching_totals(totals, summary["methods"])
    require(len(summary["per_seed"]) == len(seeds), "Per-seed summary omitted")
    for expected_seed, reported in zip(seeds, summary["per_seed"], strict=True):
        require(reported["seed"] == expected_seed, "Per-seed attribution changed")
        matching_totals(
            summarize([r for r in traces if r["seed"] == expected_seed]),
            reported["methods"],
        )
    return {
        "schema": "nuria.guarded-skill-verification.v1",
        "verified": True,
        "records": len(traces),
        "seeds": seeds,
        "source_hashes": started["source_hashes"],
        "summary_sha256": digest(summary),
        "methods": totals,
        "per_seed": summary["per_seed"],
        "public_results": public,
        "limits": "AST execution, requested evidence, receipts, membership, complete coverage and arithmetic checked independently; synthesis is not replayed and CPU/tool instrumentation are not physically attested. Public freeze provenance is separate evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = verify(args.directory)
    if args.output:
        with args.output.open("x") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(serial({key: receipt[key] for key in ("verified", "records", "seeds")}))


if __name__ == "__main__":
    main()
