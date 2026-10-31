"""Plot actual recorded outcomes from the fixed acquisition comparison."""

import argparse
import json
from pathlib import Path

import numpy as np


def plot(package, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    package, output = Path(package), Path(output)
    if output.exists():
        raise FileExistsError(output)
    result = json.loads((package / "result.json").read_bytes())
    binned = json.loads((package / "binned.json").read_bytes())
    selected = result["selected_method"]
    methods = result["methods"]
    counts = np.asarray(binned["counts"])[480:]
    recorded = counts.sum(axis=1)
    time = np.arange(len(counts)) * 0.25 + 120
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.hashsalt": "nuria-acquisition-01",
        }
    )
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(11, 9),
        layout="constrained",
        gridspec_kw={"height_ratios": [1.0, 1.0, 1.25]},
    )
    fig.suptitle(
        "Learned attention · recorded human-organoid activity",
        x=0.06,
        ha="left",
        fontsize=17,
    )
    axes[0].fill_between(
        time, recorded, step="post", color="#e1e5e2", label="All recorded entries"
    )
    axes[0].step(
        time,
        methods[selected]["per_window_acquired"],
        where="post",
        color="#24784e",
        linewidth=1,
        label="Acquired by the frozen memory policy",
    )
    axes[0].set(
        xlabel="Previously unused recorded interval · seconds",
        ylabel="Spike entries / 250 ms",
        xlim=(120, 180),
    )
    axes[0].legend(frameon=False, ncol=2, fontsize=8)
    for method, label, color in (
        ("rotating", "Rotating selection", "#9b9b9b"),
        ("static_rates", "Static training rates", "#697dab"),
        (selected, "Validation-selected memory", "#24784e"),
    ):
        axes[1].plot(
            time,
            np.cumsum(methods[method]["per_window_acquired"]),
            label=label,
            color=color,
            linewidth=1.6,
        )
    axes[1].set(
        xlabel="Recorded seconds", ylabel="Cumulative acquired entries", xlim=(120, 180)
    )
    axes[1].legend(frameon=False, ncol=3, fontsize=8)
    ordered = [
        "rotating",
        "static_rates",
        "sentinel_knn",
        "linear_history",
        "quadratic_history",
        "history_plus_spiking",
    ]
    labels = [
        "Rotating",
        "Static rates",
        "Memory · selected before test",
        "Linear history",
        "Quadratic history",
        "History + Brian2",
    ]
    values = [methods[name]["fraction"] * 100 for name in ordered]
    colors = ["#aaaaaa", "#697dab", "#24784e", "#708d81", "#8c9b8d", "#b17c62"]
    axes[2].barh(labels, values, height=0.55, color=colors)
    axes[2].invert_yaxis()
    axes[2].axvline(100 * 16 / 49, color="#777777", linestyle=":", linewidth=1)
    axes[2].text(
        100 * 16 / 49 + 1,
        -0.65,
        "32.7% of unit records selected by every method",
        color="#666666",
        fontsize=8,
    )
    for index, (value, name) in enumerate(zip(values, ordered)):
        axes[2].text(
            value + 1,
            index,
            f"{value:.1f}% · {methods[name]['acquired_spikes']:,}",
            va="center",
            fontsize=9,
        )
    axes[2].set(xlabel="Actual spike entries acquired · higher is better", xlim=(0, 82))
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.12)
        ax.set_axisbelow(True)
    fig.supxlabel(
        "One recording, 240 test windows, 1,632 spike entries. Four sentinel reads + twelve selected records per window.\nMemory wins validation; linear history does best on test and is not substituted. No neural-superiority or physical bandwidth claim.\nSource: DANDI 001603 · HO3 · van der Molen et al. · CC BY 4.0.",
        fontsize=8,
    )
    fig.savefig(output, metadata={"Date": None})
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.package, args.output)
