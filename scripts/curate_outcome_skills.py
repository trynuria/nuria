"""Create one conditional skill playbook from checked training experience.

This uses the structured-memory pattern described by ACE, not its framework or
reported results. Notes remain transferable hypotheses linked to actual source
episodes; a model's prose cannot establish a new accepted execution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.outcome_worker import BASE_POLICY, identity
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient, save


def curate(root, config):
    root = Path(root)
    output = root / "outcome-playbook.json"
    if output.exists():
        raise ValueError("The one-candidate playbook already exists")
    common = json.loads((root / "common-experience.json").read_text())
    client = BoundedClient(load_reasoner(config), root)
    evidence = [
        {
            "job_id": x["job_id"],
            "specification": x["prompt"][:2200],
            "program": x["code"][:3000],
            "accepted": x["accepted"],
            "observed_failure": x["checker"]["stderr"][-1200:],
        }
        for x in common
    ]
    response = client.complete(
        [
            {
                "role": "system",
                "content": "Build reusable procedural memory from actual checked programming episodes. Return ONLY JSON with a skills array, at most twelve entries. Each entry has exactly name, applies_when, procedure, source_jobs. source_jobs must cite one or more supplied job_id strings. Preserve concrete conditional techniques and failure modes, not generic instructions to follow specifications or write good code. Explain useful library idioms, parsing/representation choices, ordering, numeric conventions and operational checks when evidenced. A success establishes only its checked scope; a failure establishes the observed mismatch, not an invented correct implementation. Do not convert an unspecified behavior into a universal default. Do not memorize task identifiers, hidden answers or fixed example values. Each procedure is at most 500 characters. No evaluation or validation examples are supplied.",
            },
            {"role": "user", "content": json.dumps(evidence)},
        ],
        max_tokens=2400,
        seed=0,
    )
    text = response["content"].strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    proposal = json.loads(text)
    if set(proposal) != {"skills"} or not 1 <= len(proposal["skills"]) <= 12:
        raise ValueError("Malformed skill playbook")
    sources = {x["job_id"]: x for x in common}
    for skill in proposal["skills"]:
        if set(skill) != {"name", "applies_when", "procedure", "source_jobs"}:
            raise ValueError("Malformed conditional skill")
        if not skill["source_jobs"] or not set(skill["source_jobs"]) <= set(sources):
            raise ValueError("Skill cites an absent outcome")
        if any(
            type(skill[k]) is not str or len(skill[k]) > n
            for k, n in [("name", 100), ("applies_when", 400), ("procedure", 500)]
        ):
            raise ValueError("Skill note exceeds its bound")
        skill["observed_origins"] = [
            {
                "job_id": key,
                "accepted": sources[key]["accepted"],
                "program_sha256": sources[key]["proposed_code_sha256"],
            }
            for key in skill["source_jobs"]
        ]
    policy = (
        BASE_POLICY
        + "\nConditional outcome-derived memory follows. Apply a note only when its preconditions and the current specification agree. These are proposed transferable lessons from the cited executions, not permission to invent behavior.\n"
        + json.dumps(proposal, sort_keys=True)
    )
    if len(policy.encode()) > 12000:
        raise ValueError("Compiled skill memory exceeds the common policy bound")
    artifact = {
        "version": "nuria.conditional-skills.v1",
        "policy": policy,
        "policy_sha256": identity(policy),
        "skills": proposal["skills"],
        "training_experience_sha256": identity(common),
        "curation_calls": 1,
        "effectiveness": "unqualified",
        "model": client.configuration,
    }
    artifact["artifact_sha256"] = identity(artifact)
    save(output, artifact)
    return {
        "skills": len(proposal["skills"]),
        "policy_sha256": artifact["policy_sha256"],
        "effectiveness": "unqualified",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(curate(args.root, args.config), sort_keys=True))


if __name__ == "__main__":
    main()
