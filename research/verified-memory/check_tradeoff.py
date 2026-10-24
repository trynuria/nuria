"""Independent finite checks for exact coordinate-factor transplants and coding distance.

Finite reference analysis. Only Python standard library; no trainer imports.
This audits immediate equality to a canonical code, not behavioral correctness.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def states(k):
    return tuple(itertools.product((0, 1), repeat=k))


def column_patch_holds(table, k, memberships):
    ss = states(k)
    index = {s: i for i, s in enumerate(ss)}
    for factor in range(k):
        in_mask = (memberships >> factor) & 1
        for recipient in ss:
            for donor in ss:
                hybrid = list(recipient)
                hybrid[factor] = donor[factor]
                received = table[index[donor]] if in_mask else table[index[recipient]]
                if received != table[index[tuple(hybrid)]]:
                    return False
    return True


def expected_memberships(table, k):
    ss = states(k)
    if len(set(table)) == 1:
        return set(range(1 << k))
    allowed = set()
    for factor in range(k):
        for negate in (0, 1):
            if tuple(s[factor] ^ negate for s in ss) == table:
                allowed.add(1 << factor)
    return allowed


def verify_column_lemma(k):
    tested, mismatch = 0, []
    for table in itertools.product((0, 1), repeat=1 << k):
        actual = {m for m in range(1 << k) if column_patch_holds(table, k, m)}
        expected = expected_memberships(table, k)
        tested += 1 << k
        if actual != expected:
            mismatch.append(
                {"table": table, "actual": sorted(actual), "expected": sorted(expected)}
            )
    return {
        "k": k,
        "boolean_functions": 1 << (1 << k),
        "function_membership_cases": tested,
        "mismatch_count": len(mismatch),
        "mismatches": mismatch,
    }


def canonical_patch_masks(codes, n, factor):
    limit = (1 << n) - 1
    passing = []
    for mask in range(1 << n):
        good = True
        for recipient, recipient_code in codes.items():
            for donor, donor_code in codes.items():
                hybrid = list(recipient)
                hybrid[factor] = donor[factor]
                patched = (donor_code & mask) | (recipient_code & (limit ^ mask))
                if patched != codes[tuple(hybrid)]:
                    good = False
                    break
            if not good:
                break
        if good:
            passing.append(mask)
    return passing


def pairwise_distances(codes):
    return [
        {"a": list(a), "b": list(b), "distance": (ca ^ cb).bit_count()}
        for (a, ca), (b, cb) in itertools.combinations(sorted(codes.items()), 2)
    ]


def balls(codes, n, radius):
    owners = {}
    for state, code in sorted(codes.items()):
        for weight in range(radius + 1):
            for indices in itertools.combinations(range(n), weight):
                received = code
                for index in indices:
                    received ^= 1 << index
                if received in owners and owners[received] != state:
                    return {
                        "disjoint": False,
                        "first_overlap": {
                            "received_code": received,
                            "states": [owners[received], state],
                            "distances": [
                                (received ^ codes[owners[received]]).bit_count(),
                                (received ^ code).bit_count(),
                            ],
                        },
                    }
                owners[received] = state
    return {"disjoint": True, "distinct_received_codes": len(owners)}


def classify_codes(codes, n):
    ss = sorted(codes)
    classification, counts = [], {"constant": 0, "X": 0, "Y": 0, "XOR": 0, "mixed": 0}
    for j in range(n):
        table = tuple((codes[s] >> j) & 1 for s in ss)
        if len(set(table)) == 1:
            label = "constant"
        elif table in ((0, 0, 1, 1), (1, 1, 0, 0)):
            label = "X"
        elif table in ((0, 1, 0, 1), (1, 0, 1, 0)):
            label = "Y"
        elif table in ((0, 1, 1, 0), (1, 0, 0, 1)):
            label = "XOR"
        else:
            label = "mixed"
        counts[label] += 1
        classification.append(
            {"neuron": j, "truth_table_00_01_10_11": table, "factor": label}
        )
    distances = pairwise_distances(codes)
    return {
        "codes": [{"state": list(s), "integer_code": codes[s]} for s in ss],
        "n": n,
        "columns": classification,
        "column_counts": counts,
        "pairwise_distances": distances,
        "minimum_distance": min(x["distance"] for x in distances),
        "canonical_masks_by_factor": {
            str(i): canonical_patch_masks(codes, n, i) for i in range(2)
        },
        "unique_correction_balls": {str(t): balls(codes, n, t) for t in range(4)},
    }


def audit_learned(rel, root):
    path = root / rel
    d = json.loads(path.read_text())
    if "initial_canonical_code_check" in d:
        rows = d["initial_canonical_code_check"]["canonical_codes"]
        codes = {
            tuple(row["evaluator_only_behavior"]): row["integer_code"] for row in rows
        }
    else:
        rows = d["reachable_neural_codes"]
        codes = {
            tuple(row["evaluator_only_behaviors"][0]): row["integer_code"]
            for row in rows
        }
        assert len(codes) == len(rows) == 4, "Unique canonical code per state required"
    result = classify_codes(codes, 12)
    result["evidence_path"] = rel
    result["evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def encode_columns(columns):
    return {s: sum(fn(s) << j for j, fn in enumerate(columns)) for s in states(2)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    evidence_root = args.root
    factor12 = encode_columns([lambda s: s[0]] * 6 + [lambda s: s[1]] * 6)
    factor14 = encode_columns([lambda s: s[0]] * 7 + [lambda s: s[1]] * 7)
    unrestricted12 = encode_columns(
        ([lambda s: s[0], lambda s: s[1], lambda s: s[0] ^ s[1]]) * 4
    )
    # Every four-word binary column contributes a*(4-a) <= 4 to total
    # unordered pair distance. Six pairs therefore give 6*d <= 4*n.
    per_column_pair_sums = [
        sum(a != b for a, b in itertools.combinations(table, 2))
        for table in itertools.product((0, 1), repeat=4)
    ]
    assert max(per_column_pair_sums) == 4
    examples = {
        "factorized_12_balanced": classify_codes(factor12, 12),
        "factorized_14_balanced": classify_codes(factor14, 14),
        "unrestricted_12_parity_repetition": classify_codes(unrestricted12, 12),
    }
    assert examples["factorized_12_balanced"]["minimum_distance"] == 6
    assert examples["factorized_14_balanced"]["minimum_distance"] == 7
    assert examples["unrestricted_12_parity_repetition"]["minimum_distance"] == 8
    assert not examples["factorized_12_balanced"]["unique_correction_balls"]["3"][
        "disjoint"
    ]
    assert examples["factorized_14_balanced"]["unique_correction_balls"]["3"][
        "disjoint"
    ]
    assert examples["unrestricted_12_parity_repetition"]["unique_correction_balls"][
        "3"
    ]["disjoint"]
    assert not any(
        examples["unrestricted_12_parity_repetition"][
            "canonical_masks_by_factor"
        ].values()
    )
    result = {
        "format": "exact-coordinate-transplant-distance-check-v1",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Canonical binary code equality and Hamming-ball uniqueness; not runtime learning, new biological science, global novelty, or behavioral off-manifold transplants.",
        "column_lemma_enumerations": [verify_column_lemma(k) for k in (1, 2, 3)],
        "four_word_plotkin_column_totals": per_column_pair_sums,
        "factorized_length_12_allocations": [
            {"n_X": x, "n_Y": 12 - x, "d_min": min(x, 12 - x)} for x in range(13)
        ],
        "reference_constructions": examples,
        "learned_codebooks": [
            audit_learned(rel, evidence_root)
            for rel in (
                "evidence/factorized/robust-check.json",
                "evidence/initial-9102/transplant-check.json",
                "evidence/initial-9103/transplant-check.json",
                "evidence/initial-9104/transplant-check.json",
                "evidence/mixed/robust-check.json",
            )
        ],
    }
    assert all(x["mismatch_count"] == 0 for x in result["column_lemma_enumerations"])
    out = args.out
    assert not out.exists(), (
        "Preserve previous evidence; choose a new filename to rerun"
    )
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(out),
                "lemma_enumerations": result["column_lemma_enumerations"],
                "learned": [
                    {
                        "path": x["evidence_path"],
                        "counts": x["column_counts"],
                        "d_min": x["minimum_distance"],
                        "masks": x["canonical_masks_by_factor"],
                    }
                    for x in result["learned_codebooks"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
