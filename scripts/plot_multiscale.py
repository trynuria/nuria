"""Render the retained paired Brier contrasts, including null and worse results."""

import argparse
import json
from pathlib import Path


def render(directory: Path, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    result = json.loads((directory / "result.json").read_text())
    matplotlib.rcParams.update({"font.size": 9, "svg.hashsalt": "nuria-multiscale-v1"})
    branches = ("coupled", "no_return", "no_spike_feedback", "shuffled")
    labels = (
        "Full coupling",
        "No regional return",
        "No spike feedback",
        "Shuffled connectivity",
    )
    tasks = ("delayed_dependency", "context_reversal")
    limits = [
        summary["comparisons_to_current"][branch][edge]
        for summary in result["summary"].values()
        for branch in branches
        for edge in ("lower_95", "upper_95")
    ]
    extent = max(max(abs(value) for value in limits) * 1.3, 0.006)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True)
    for ax, task in zip(axes, tasks):
        for index, branch in enumerate(branches):
            row = result["summary"][task]["comparisons_to_current"][branch]
            gain = row["paired_gain"]
            color = "#267260" if gain >= 0 else "#b35e26"
            ax.errorbar(
                gain,
                index,
                xerr=[[gain - row["lower_95"]], [row["upper_95"] - gain]],
                fmt="o",
                capsize=4,
                markersize=6,
                color=color,
                linewidth=1.4,
            )
        ax.axvline(0, color="#78827e", linewidth=0.8)
        ax.axvline(0.005, color="#b5bdb9", linewidth=0.8, linestyle="--")
        ax.set_xlim(-extent, extent)
        ax.set_yticks(range(len(labels)), labels)
        ax.set_title(task.replace("_", " ").capitalize(), loc="left", pad=15)
        ax.set_xlabel("Current − branch Brier loss")
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#c4ccc8")
        ax.tick_params(axis="y", length=0, pad=12)
        ax.grid(axis="x", alpha=0.12)
        ax.set_axisbelow(True)
    axes[0].invert_yaxis()
    fig.suptitle(
        "Does regional coupling improve the existing forecast path?",
        x=0.02,
        ha="left",
        fontsize=13,
    )
    fig.text(
        0.02,
        0.035,
        "Points: six paired environment seeds · Bars: 95% Student t intervals · One Brian initialization\n"
        "Positive values favor the branch. Dashed line: fixed 0.005 mean-gain threshold. Synthetic tasks only.",
        fontsize=8,
        color="#47534d",
    )
    fig.tight_layout(rect=(0, 0.14, 1, 0.94), w_pad=3)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, metadata={"Date": None, "Creator": "Nuria research"})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing figure")
    render(args.directory, args.output)


if __name__ == "__main__":
    main()
