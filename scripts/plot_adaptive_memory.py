"""Render paired forecast gains and their predefined thresholds."""

import argparse
import json
from pathlib import Path


def render(directory: Path, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    result = json.loads((directory / "result.json").read_text())
    matplotlib.rcParams.update(
        {"font.size": 9, "svg.hashsalt": "nuria-adaptive-memory-v1"}
    )
    tasks = list(result["summary"])
    labels = [task.replace("_", " ").capitalize() for task in tasks]
    fig, ax = plt.subplots(figsize=(9.4, 5.6))
    for index, task in enumerate(tasks):
        row = result["summary"][task]["brier_gains_vs_current"]["adaptive"]
        gain = row["mean"]
        color = "#267260" if gain > 0 else "#a65d37"
        ax.errorbar(
            gain,
            index,
            xerr=[[gain - row["lower_95"]], [row["upper_95"] - gain]],
            fmt="o",
            capsize=4,
            color=color,
            markersize=6,
        )
    ax.axvline(0, color="#65746c", linewidth=0.9)
    ax.axvline(0.01, color="#a4b1a9", linewidth=0.8, linestyle="--")
    ax.axvspan(-0.005, 0, color="#ebeeec", zorder=-1)
    ax.set_yticks(range(len(tasks)), labels)
    ax.invert_yaxis()
    ax.set_xlim(-0.009, 0.17)
    ax.set_xlabel("Current − adaptive Brier loss (positive favors adaptive memory)")
    ax.set_title(
        "Ordered memory and adaptation: held-out comparison",
        loc="left",
        pad=18,
        fontsize=14,
    )
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#bdc8c1")
    ax.tick_params(axis="y", length=0, pad=12)
    ax.grid(axis="x", alpha=0.15)
    fig.text(
        0.02,
        0.035,
        "Twelve paired environment seeds · 95% Student t intervals · 1,792 scored outcomes per task/seed\nDashed: 0.01 required gain on the first four tasks. Shaded: allowed control regression. Synthetic forecasts only.",
        fontsize=8,
        color="#47534d",
    )
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, metadata={"Date": None, "Creator": "Nuria research"})
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
        raise ValueError("Refusing to overwrite an existing figure")
    render(args.directory, args.output)


if __name__ == "__main__":
    main()
