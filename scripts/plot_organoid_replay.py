"""Plot retained recorded-organoid inputs and held-out comparisons."""

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
    data = json.loads((package / "binned.json").read_bytes())
    result = json.loads((package / "result.json").read_bytes())
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.hashsalt": "nuria-dandi-001603",
        }
    )
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), layout="constrained")
    fig.suptitle(
        "Recorded human-organoid activity · HO3", x=0.06, ha="left", fontsize=17
    )
    time = np.arange(len(data["counts"])) * data["bin_seconds"]
    axes[0].step(
        time,
        np.asarray(data["counts"]).sum(axis=1),
        where="post",
        color="#344e41",
        linewidth=1,
    )
    axes[0].axvline(60, color="#b0b0b0", linestyle="--", linewidth=1)
    axes[0].axvline(90, color="#b0b0b0", linestyle="--", linewidth=1)
    axes[0].set(
        xlabel="Recorded seconds · train 0–60 / validation 60–90 / test 90–120",
        ylabel="All-unit spikes / 250 ms",
    )
    x = np.asarray(result["target_bin_indices"]) * data["bin_seconds"]
    axes[1].plot(
        x,
        result["test_targets"],
        label="Recorded next-bin target",
        color="#444444",
        linewidth=1,
    )
    for key, label, color in (
        ("ridge_history", "History-only ridge", "#1f77b4"),
        ("history_plus_recording_drive", "History + Brian2", "#ad6543"),
    ):
        axes[1].plot(
            x,
            result["methods"][key]["test_predictions"],
            label=label,
            color=color,
            linewidth=1,
        )
    axes[1].set(xlabel="Held-out recorded seconds", ylabel="log(1 + spike count)")
    axes[1].legend(frameon=False, ncol=3, fontsize=8)
    names = ("ridge_history", "history_plus_zero_drive", "history_plus_recording_drive")
    axes[2].barh(
        [
            "History-only ridge",
            "History + zero-drive circuit",
            "History + recording-driven circuit",
        ],
        [result["methods"][k]["test_mse"] for k in names],
        color=["#1f77b4", "#888888", "#ad6543"],
        height=0.5,
    )
    axes[2].set(xlabel="Held-out mean squared error · lower is better", xlim=(0, 0.5))
    for index, key in enumerate(names):
        value = result["methods"][key]["test_mse"]
        axes[2].text(value + 0.006, index, f"{value:.4f}", va="center", fontsize=9)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.12)
        ax.set_axisbelow(True)
    fig.supxlabel(
        "One recorded organoid; 49 curated units. Source: DANDI 001603, van der Molen et al., CC BY 4.0.\nNeural augmentation did not improve this task. No live-tissue or biological-learning claim.",
        fontsize=9,
    )
    fig.savefig(output, metadata={"Date": None})
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.package, args.output)
