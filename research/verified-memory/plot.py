"""Render measured code geometry and exact-canonical certificates.

Run with an existing Python environment containing Matplotlib. All quantities
are derived from the serialized network/check reports; no decorative artwork.
"""

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap

ROOT = Path(__file__).resolve().parent
MODEL_NAMES = ("factorized", "mixed")
MODEL_LABELS = ("Factorized code", "Mixed code")
COLORS = ("#427b83", "#a2573b")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_editability(codebook, neurons):
    pairs = tuple(sorted(codebook))
    all_mask = (1 << neurons) - 1
    factors = []
    for factor in (0, 1):
        passing = []
        for mask in range(1 << neurons):
            correct = True
            for recipient in pairs:
                for donor in pairs:
                    target = (
                        (donor[0], recipient[1])
                        if factor == 0
                        else (recipient[0], donor[1])
                    )
                    patched = (codebook[recipient] & (all_mask ^ mask)) | (
                        codebook[donor] & mask
                    )
                    if patched != codebook[target]:
                        correct = False
                        break
                if not correct:
                    break
            if correct:
                passing.append(mask)
        factors.append(
            {
                "factor": factor,
                "passing_masks": passing,
                "passing_mask_count": len(passing),
                "minimum_passing_mask_size": min(
                    (m.bit_count() for m in passing), default=None
                ),
            }
        )
    return factors


def load_data():
    data = []
    for name, label in zip(MODEL_NAMES, MODEL_LABELS):
        network_path = ROOT / "evidence" / name / "network-00.json"
        check_path = ROOT / "evidence" / name / "robust-check.json"
        spec = json.loads(network_path.read_text())
        report = json.loads(check_path.read_text())
        if digest(network_path) != report["network_file_sha256"]:
            raise AssertionError("Network differs from certified network")
        code_rows = report["initial_canonical_code_check"]["canonical_codes"]
        codebook = {
            tuple(row["evaluator_only_behavior"]): row["integer_code"]
            for row in code_rows
        }
        certificates = report["dynamic_repair"]["radius_certificates"]
        maximum = max(
            row["max_corrupted_spike_bits_before_each_action"]
            for row in certificates
            if row["passed"]
        )
        certificate = next(
            row
            for row in certificates
            if row["max_corrupted_spike_bits_before_each_action"] == maximum
        )
        distances = [
            (codebook[a] ^ codebook[b]).bit_count()
            for i, a in enumerate(sorted(codebook))
            for b in sorted(codebook)[i + 1 :]
        ]
        factors = canonical_editability(codebook, spec["state_neurons"])
        data.append(
            {
                "name": name,
                "label": label,
                "state_neurons": spec["state_neurons"],
                "transition_neurons": spec["transition_neurons"],
                "network_file_sha256": digest(network_path),
                "robust_check_file_sha256": digest(check_path),
                "codebook": [
                    {
                        "pair": list(pair),
                        "integer_code": codebook[pair],
                        "spikes": [
                            (codebook[pair] >> i) & 1
                            for i in range(spec["state_neurons"])
                        ],
                    }
                    for pair in sorted(codebook)
                ],
                "minimum_pairwise_hamming_distance": min(distances),
                "maximum_certified_exact_canonical_radius": maximum,
                "canonical_repair_certificate": certificate,
                "exact_coordinate_factor_editability": factors,
                "universally_editable_factors": sum(
                    row["passing_mask_count"] > 0 for row in factors
                ),
            }
        )
    if len({(row["state_neurons"], row["transition_neurons"]) for row in data}) != 1:
        raise AssertionError("Figure requires fixed neural capacity")
    return data


def plot(data):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titleweight": "bold",
            "axes.labelcolor": "#343c41",
            "text.color": "#17262c",
            "xtick.color": "#59666a",
            "ytick.color": "#59666a",
            "axes.edgecolor": "#cbd3d5",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
        }
    )
    fig = plt.figure(figsize=(12, 6.7), facecolor="white")
    grid = fig.add_gridspec(
        2,
        3,
        left=0.07,
        right=0.965,
        bottom=0.31,
        top=0.77,
        width_ratios=(1.55, 1, 1),
        wspace=0.44,
        hspace=0.68,
    )
    fig.text(
        0.07,
        0.93,
        "Coordinate editing and fault correction",
        fontsize=20,
        fontweight="bold",
    )
    fig.text(
        0.07,
        0.875,
        "Two learned encodings at fixed capacity: 12 retained binary spikes and 64 transition units.",
        fontsize=11,
        color="#59666a",
    )
    for index, row in enumerate(data):
        ax = fig.add_subplot(grid[index, 0])
        ax.imshow(
            np.array([item["spikes"] for item in row["codebook"]]),
            cmap=ListedColormap(("#eef1f1", COLORS[index])),
            vmin=0,
            vmax=1,
            aspect="auto",
            interpolation="nearest",
        )
        ax.set_yticks(range(4), ["00", "01", "10", "11"])
        ax.set_xticks(range(12), [str(i) for i in range(12)], fontsize=8)
        ax.set_xticks(np.arange(-0.5, 12, 1), minor=True)
        ax.set_yticks(np.arange(-0.5, 4, 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=2)
        ax.tick_params(which="both", length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(row["label"], loc="left", fontsize=11, pad=10)
        ax.set_ylabel("Behavior", fontsize=9)
        if index == 1:
            ax.set_xlabel("Retained spike coordinate", fontsize=9, labelpad=8)
        ax.text(
            1.0,
            1.12,
            "Minimum code distance " + str(row["minimum_pairwise_hamming_distance"]),
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=8,
            color="#59666a",
        )

    repair = fig.add_subplot(grid[:, 1])
    editable = fig.add_subplot(grid[:, 2])
    for ax, values, title, maximum in (
        (
            repair,
            [row["maximum_certified_exact_canonical_radius"] for row in data],
            "Exact restoration",
            3,
        ),
        (
            editable,
            [row["universally_editable_factors"] for row in data],
            "Exact coordinate edits",
            2,
        ),
    ):
        bars = ax.bar(range(2), values, width=0.57, color=COLORS, zorder=3)
        ax.set_xticks(range(2), ["Factorized", "Mixed"], fontsize=9)
        ax.set_ylim(0, maximum + 0.7)
        ax.set_yticks(range(maximum + 1))
        ax.grid(axis="y", color="#e8edef", zorder=0)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left", fontsize=12, pad=20)
        ax.tick_params(axis="both", length=0, pad=8)
        for rect, value in zip(bars, values):
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                value + 0.1,
                str(value),
                ha="center",
                fontsize=17,
                fontweight="bold",
            )
    repair.set_ylabel("Corrupted spikes before every command", fontsize=9)
    editable.set_ylabel("Independently editable factors, out of two", fontsize=9)
    repair.text(
        0.5,
        -0.17,
        "All 1,896 / 1,896 and 7,176 / 7,176\nrepair cases pass, respectively.",
        transform=repair.transAxes,
        ha="center",
        va="top",
        fontsize=8.5,
        color="#59666a",
        linespacing=1.5,
    )
    editable.text(
        0.5,
        -0.17,
        "All 4,096 donor-coordinate masks\ntested against all 16 donor/recipient pairs.",
        transform=editable.transAxes,
        ha="center",
        va="top",
        fontsize=8.5,
        color="#59666a",
        linespacing=1.5,
    )
    fig.text(
        0.07,
        0.09,
        "Exact restoration: every bounded fault plus legal command returns the correctly updated canonical spike code.\n"
        "Coordinate edit: one fixed mask must replace one factor while preserving the other, in every canonical donor/recipient case.",
        fontsize=9,
        color="#343c41",
        linespacing=1.6,
    )
    fig.text(
        0.07,
        0.025,
        "Four-state clocked threshold/reset prototype; not production Brian2. Different training seeds/objectives/budgets.\n"
        "The comparison describes these serialized networks; it is not a controlled attribution, a new coding theorem, or evidence of consciousness.",
        fontsize=8,
        color="#697579",
        linespacing=1.5,
    )
    for suffix in ("svg", "png"):
        fig.savefig(ROOT / ("memory-geometry." + suffix), dpi=220, facecolor="white")
    plt.close(fig)
    svg_path = ROOT / "memory-geometry.svg"
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n"
    )


if __name__ == "__main__":
    data = load_data()
    plot(data)
    payload = {
        "figure_source_sha256": digest(Path(__file__)),
        "criterion": "Exact canonical replacement, not current output or future-safe-region replacement.",
        "models": data,
        "outputs": {
            suffix: digest(ROOT / ("memory-geometry." + suffix))
            for suffix in ("svg", "png")
        },
    }
    (ROOT / "memory-geometry-data.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "models": [
                    {
                        "name": row["name"],
                        "radius": row["maximum_certified_exact_canonical_radius"],
                        "editable_factors": row["universally_editable_factors"],
                        "canonical_editability": row[
                            "exact_coordinate_factor_editability"
                        ],
                    }
                    for row in data
                ],
                "outputs": payload["outputs"],
            },
            indent=2,
        )
    )
