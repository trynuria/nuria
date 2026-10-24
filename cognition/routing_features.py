"""Source-reviewed observations for a bounded program-strategy decision.

Only already disclosed diagnostic examples, admitted library size and the exact
recorded neural vector enter the encoder. Program recipes, task identifiers and
buyer-held scoring rows are not arguments. Simple controls receive these same
observations. Feature extraction is a conventional engineering representation;
its usefulness and the neural policy's contribution require separate evaluation.
"""

from __future__ import annotations

import math
import re

from cognition.text_skills import fields, normalize_config, text

VERSION = "audited-routing-observations-v1"
HASH = re.compile(r"[a-f0-9]{64}\Z")
DIMENSION = 128
TASK_DIMENSION = 64
OBSERVABLES = (
    "exact_max",
    "exact_mean",
    "strip_max",
    "strip_mean",
    "upper_max",
    "upper_mean",
    "lower_max",
    "lower_mean",
    "substring_max",
    "substring_mean",
    "folded_substring_max",
    "folded_substring_mean",
    "output_characters",
    "input_characters",
    "output_digit_fraction",
    "output_alpha_fraction",
    "output_upper_fraction",
    "output_space_fraction",
    "output_punctuation_fraction",
    "distinct_output_fraction",
    "admitted_library_fraction",
)


def _library_size(state, config):
    if (
        type(state) is not dict
        or type(state.get("library")) is not dict
        or type(state["library"].get("programs")) is not dict
        or len(state["library"]["programs"]) > config["max_programs"]
    ):
        raise ValueError(
            "Routing features require the reviewed admitted library envelope"
        )
    return len(state["library"]["programs"])


def encode(audits, library_state, config, neural_features64, profile_caps):
    """Return 64 declared task coordinates followed by 64 actual neural ones.

    The first21 coordinates are conventional summaries. Remaining task
    coordinates are reserved zeros, not invented neural measurements. The
    caller binds each receipt and the neural-state hash before requesting a
    choice. This function validates data shape but does not authenticate them.
    """
    config = normalize_config(config)
    if (
        type(audits) is not list
        or not 1 <= len(audits) <= 4
        or type(neural_features64) is not list
        or len(neural_features64) != 64
        or any(
            type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1
            for x in neural_features64
        )
        or type(profile_caps) is not dict
        or not 2 <= len(profile_caps) <= 16
        or any(
            not isinstance(name, str)
            or type(cap) is not int
            or not 1 <= cap <= 10_000_000
            for name, cap in profile_caps.items()
        )
    ):
        raise ValueError(
            "Routing observation, neural vector or profile limits are invalid"
        )
    rows, outputs, receipts = [], [], set()
    for audit in audits:
        if (
            type(audit) is not dict
            or set(audit) != {"input", "output", "receipt_sha256"}
            or not isinstance(audit["receipt_sha256"], str)
            or not HASH.fullmatch(audit["receipt_sha256"])
            or audit["receipt_sha256"] in receipts
        ):
            raise ValueError(
                "Only distinct disclosed diagnostic receipts may enter routing"
            )
        receipts.add(audit["receipt_sha256"])
        rows.append(fields(audit["input"], config))
        outputs.append(text(audit["output"], config["max_input_bytes"]))
    width = min(map(len, rows))
    relations = [[] for _ in range(6)]
    for column in range(width):
        matches = [[] for _ in range(6)]
        for row, output in zip(rows, outputs, strict=True):
            value = row[column]
            comparisons = (
                value == output,
                value.strip() == output,
                value.upper() == output,
                value.lower() == output,
                output in value,
                output.lower() in value.lower(),
            )
            for bank, match in zip(matches, comparisons, strict=True):
                bank.append(int(match))
        for bank, observations in zip(relations, matches, strict=True):
            bank.append(sum(observations) / len(observations))
    result = []
    for bank in relations:
        result.extend((max(bank), sum(bank) / len(bank)))
    output_text = "".join(outputs)
    output_length = max(1, len(output_text))
    result.extend(
        (
            min(1, len(output_text) / 256),
            min(1, sum(len(value) for row in rows for value in row) / 4096),
            sum(c.isdigit() for c in output_text) / output_length,
            sum(c.isalpha() for c in output_text) / output_length,
            sum(c.isupper() for c in output_text) / output_length,
            sum(c.isspace() for c in output_text) / output_length,
            sum(not c.isalnum() and not c.isspace() for c in output_text)
            / output_length,
            len(set(outputs)) / len(outputs),
            min(1, _library_size(library_state, config) / 64),
        )
    )
    return (
        [float(x) for x in result]
        + [0.0] * (TASK_DIMENSION - len(result))
        + [float(x) for x in neural_features64]
    )
