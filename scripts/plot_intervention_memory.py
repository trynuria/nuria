"""Plot every registered branch and the paired credit-attribution contrasts."""

import argparse
import json
from pathlib import Path


def render(directory, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    result = json.loads((directory / "result.json").read_text())
    tasks = list(result["summary"])
    methods = result["methods"]
    labels = {
        "neural_origin": "Neural + origin credit",
        "raw_origin": "Raw + origin credit",
        "neural_receipt_time": "Neural + wrong-time credit",
        "raw_receipt_time": "Raw + wrong-time credit",
        "random_features": "Random features + origin",
        "frozen": "Frozen policy",
        "frequency": "Workload heuristic",
        "no_index": "No index",
        "static_training": "Training-calibrated static",
        "all_indexes_reference": "All three indexes*",
    }
    matplotlib.rcParams.update(
        {"font.size": 10, "svg.hashsalt": "nuria-verified-interventions-v2"}
    )
    fig, (ax, contrast) = plt.subplots(
        1, 2, figsize=(12.8, 7), gridspec_kw={"width_ratios": [1.65, 1]}
    )
    ratios = np.asarray(
        [
            [
                result["summary"][task]["means"][method]
                / result["summary"][task]["means"]["no_index"]
                for task in tasks
            ]
            for method in methods
        ]
    )
    ax.imshow(ratios, cmap="RdYlGn_r", vmin=0, vmax=2, aspect="auto")
    for row in range(len(methods)):
        for column in range(len(tasks)):
            ax.text(column, row, f"{ratios[row, column]:.2f}", ha="center", va="center")
    ax.set_xticks(range(len(tasks)), [task.capitalize() for task in tasks])
    ax.set_yticks(range(len(methods)), [labels[m] for m in methods])
    ax.tick_params(length=0, pad=10)
    ax.set_title(
        "Native VM work / no-index work\nLower is better; 1.00 = no index", pad=17
    )
    ax.spines[:].set_visible(False)
    for index, task in enumerate(tasks):
        item = result["summary"][task]["contrasts"][
            "neural_origin__neural_receipt_time"
        ]
        mean, low, high = (
            100 * item[key] for key in ("relative_reduction", "lower_95", "upper_95")
        )
        contrast.errorbar(
            mean,
            index,
            xerr=[[mean - low], [high - mean]],
            fmt="o",
            color="#267260" if low > 0 else "#a65d37",
            capsize=4,
        )
    contrast.axvline(0, color="#68756e", linewidth=0.8)
    contrast.set_yticks(range(len(tasks)), [task.capitalize() for task in tasks])
    contrast.invert_yaxis()
    contrast.spines[["top", "right", "left"]].set_visible(False)
    contrast.tick_params(axis="y", length=0, pad=9)
    contrast.set_title(
        "Origin vs wrong-time credit\nPaired 95% Student t intervals", pad=17
    )
    contrast.set_xlabel("VM work reduction (%)")
    contrast.grid(axis="x", alpha=0.18)
    fig.suptitle(
        "Verified software interventions — reserved workloads",
        x=0.02,
        ha="left",
        fontsize=17,
    )
    fig.text(
        0.02,
        0.025,
        "24 paired seeds per task · 48 decisions per seed · Real UCI records; generated query schedules\n"
        "Construction and calibration charged. VM counts exclude inference, cloning and answer checking.\n"
        "Wrong-time credit is an intentional defect control. *Three indexes exceed the one-index action constraint.\n"
        "The heuristic is stronger than the learned policy; the neural-specificity gate fails. No overall efficiency or neural-superiority claim.",
        fontsize=8.5,
        color="#47534d",
    )
    fig.tight_layout(rect=(0, 0.15, 1, 0.94), w_pad=3)
    fig.savefig(output, metadata={"Date": None, "Creator": "Nuria research"}, dpi=180)
    plt.close(fig)
    if output.suffix == ".svg":
        output.write_text(
            "\n".join(line.rstrip() for line in output.read_text().splitlines()) + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new figure path")
    render(args.directory, args.output)


if __name__ == "__main__":
    main()
