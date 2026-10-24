"""Check an explicit recurrence plan using bounded, local corpus metadata.

Recipe and source-row identities are planner evidence only. They must never be
added to a contractor brief. This guard proves declared recipe equality and
fresh inputs; it does not prove a transform is correct or a skill was learned.
Only hashes, source positions and counts are returned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
MAX_TASKS = 256
MAX_PAIRS = 128
MAX_ROWS = 128
MAX_INPUT_BYTES = 2048
MAX_CHECKS = 65536
HASH = re.compile(r"[0-9a-f]{64}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Corpus metadata is not bounded JSON") from error


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON contains a duplicate object key")
        result[key] = value
    return result


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(
            "Corpus or visible input is not valid unambiguous JSON"
        ) from error


def load_corpus(path, expected_sha256=None):
    if expected_sha256 is not None:
        require(
            type(expected_sha256) is str and HASH.fullmatch(expected_sha256),
            "Pinned corpus SHA256 is malformed",
        )
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(0 < len(raw) <= MAX_BYTES, "Corpus exceeds its byte budget")
    actual = hashlib.sha256(raw).hexdigest()
    require(
        expected_sha256 is None or actual == expected_sha256,
        "Corpus differs from its pinned raw-file SHA256",
    )
    return decode(raw), actual


def check_recurrences(corpus, pairs, *, maximum_checks=MAX_CHECKS):
    """Validate explicit zero-based acquisition:recurrence pairs, without routing.

    Disjointness applies across every selected entry with the same recipe, even
    when its format number differs. Different recipes may share observations.
    ``maximum_checks`` caps logical metadata checks, not CPU or runtime work.
    """
    require(
        type(maximum_checks) is int and 1 <= maximum_checks <= MAX_CHECKS,
        "Metadata-check budget must be finite",
    )
    checks = 0

    def charge(amount=1):
        nonlocal checks
        require(checks + amount <= maximum_checks, "Metadata-check budget exhausted")
        checks += amount

    require(
        type(corpus) is dict
        and type(corpus.get("source")) is dict
        and bool(corpus["source"])
        and type(corpus.get("tasks")) is list
        and 1 <= len(corpus["tasks"]) <= MAX_TASKS,
        "A finite corpus with source provenance is required",
    )
    require(len(canonical(corpus)) <= MAX_BYTES, "Corpus exceeds its byte budget")
    require(
        type(pairs) is list and 1 <= len(pairs) <= MAX_PAIRS,
        "Supply a finite nonempty recurrence plan",
    )
    inspected = {}

    def inspect(position):
        if position in inspected:
            return inspected[position]
        charge()
        entry = corpus["tasks"][position]
        require(type(entry) is dict, "Selected corpus entry is malformed")
        recipe = entry.get("recipe")
        require(type(recipe) is dict and recipe, "Selected entry needs a recipe")
        require(len(canonical(recipe)) <= 4096, "Recipe metadata exceeds its bound")
        phase = entry.get("phase")
        require(type(phase) is str and len(phase) <= 32, "Entry phase is malformed")
        ids, inputs = entry.get("row_ids"), entry.get("inputs")
        require(
            type(ids) is list
            and type(inputs) is list
            and 1 <= len(ids) == len(inputs) <= MAX_ROWS
            and all(type(row) is int and 1 <= row <= 2**53 - 1 for row in ids)
            and len(set(ids)) == len(ids),
            "Source row identities and visible inputs must be aligned and unique",
        )
        visible = []
        for value in inputs:
            charge()
            require(
                type(value) is str and len(value.encode("utf-8")) <= MAX_INPUT_BYTES,
                "Visible input exceeds its text bound",
            )
            fields = decode(value)
            require(
                type(fields) is list
                and len(fields) == 4
                and all(type(field) is str for field in fields),
                "Visible numeric input needs four string fields",
            )
            visible.append(tuple(fields))
        require(
            len(set(visible)) == len(visible), "Visible inputs repeat within one entry"
        )
        result = {
            "phase": phase,
            "recipe_sha256": digest(recipe),
            "row_ids": set(ids),
            "visible_inputs": set(visible),
            "source_rows_sha256": digest(ids),
            "visible_inputs_sha256": digest(visible),
            "rows": len(ids),
        }
        inspected[position] = result
        return result

    recurrences, bindings = set(), []
    for pair in pairs:
        charge()
        require(
            type(pair) in (list, tuple)
            and len(pair) == 2
            and all(
                type(position) is int and 0 <= position < len(corpus["tasks"])
                for position in pair
            ),
            "Recurrence positions are invalid",
        )
        acquired, repeated = pair
        require(
            acquired < repeated and repeated not in recurrences,
            "A recurrence must follow its acquisition and cannot be repeated",
        )
        recurrences.add(repeated)
        origin, recurrence = inspect(acquired), inspect(repeated)
        require(
            origin["phase"] in ("acquisition", "unseen", "transfer")
            and recurrence["phase"] == "serve",
            "Declared acquisition and serving phases do not establish a recurrence",
        )
        require(
            origin["recipe_sha256"] == recurrence["recipe_sha256"],
            "Serving phase or format identity alone is insufficient: recipes differ",
        )
        bindings.append(
            {
                "acquisition_position": acquired,
                "recurrence_position": repeated,
                "recipe_sha256": origin["recipe_sha256"],
                "acquisition_rows": origin["rows"],
                "recurrence_rows": recurrence["rows"],
                "acquisition_source_rows_sha256": origin["source_rows_sha256"],
                "recurrence_source_rows_sha256": recurrence["source_rows_sha256"],
                "acquisition_visible_inputs_sha256": origin["visible_inputs_sha256"],
                "recurrence_visible_inputs_sha256": recurrence["visible_inputs_sha256"],
            }
        )
    groups = {}
    for position, entry in inspected.items():
        prior = groups.setdefault(entry["recipe_sha256"], [])
        for _, other in prior:
            charge()
            require(
                entry["row_ids"].isdisjoint(other["row_ids"]),
                "Matched recipe entries overlap source row identities",
            )
            require(
                entry["visible_inputs"].isdisjoint(other["visible_inputs"]),
                "Matched recipe entries overlap visible inputs",
            )
        prior.append((position, entry))
    return {
        "schema": "nuria-recurrence-plan-check-v1",
        "passed": True,
        "corpus_canonical_sha256": digest(corpus),
        "source_provenance_sha256": digest(corpus["source"]),
        "pairs": bindings,
        "counts": {
            "pairs": len(bindings),
            "acquisition_entries": len({pair[0] for pair in pairs}),
            "recurrence_entries": len(recurrences),
            "recipe_groups": len(groups),
            "selected_entries": len(inspected),
            "selected_rows": sum(entry["rows"] for entry in inspected.values()),
            "metadata_checks": checks,
            "maximum_metadata_checks": maximum_checks,
        },
        "scope": "Planner-only declared recipe equality and fresh-row/input checks. No learning, execution, source-truth or customer-demand claim; no routing metadata is authorized for contractor briefs.",
    }


def parse_pairs(value):
    require(type(value) is str and len(value) <= 4096, "Pair argument is too large")
    items = value.split(",")
    require(1 <= len(items) <= MAX_PAIRS, "Pair count exceeds its bound")
    require(
        all(re.fullmatch(r"[0-9]{1,3}:[0-9]{1,3}", item) for item in items),
        "Pairs must be explicit zero-based acquisition:recurrence positions",
    )
    return [tuple(map(int, item.split(":"))) for item in items]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--corpus-sha256", help="Expected raw-file SHA256, if pinned")
    parser.add_argument("--maximum-checks", type=int, default=MAX_CHECKS)
    args = parser.parse_args()
    try:
        corpus, raw_sha = load_corpus(args.corpus, args.corpus_sha256)
        result = check_recurrences(
            corpus, parse_pairs(args.pairs), maximum_checks=args.maximum_checks
        )
        result["corpus_raw_sha256"] = raw_sha
    except (OSError, ValueError) as error:
        parser.exit(2, json.dumps({"passed": False, "error": str(error)}) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
