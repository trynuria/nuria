"""Render every acquisition branch from recorded results; plotting is optional."""

import argparse
import json
import re
from pathlib import Path
from statistics import mean, stdev


def main():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.results.read_text())
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "svg.fonttype": "none",
            "svg.hashsalt": "nuria-acquisition-v1",
            "font.size": 11,
        }
    )
    labels = [
        "Adaptive",
        "Contextless",
        "No forgetting",
        "Frozen",
        "Free",
        "Always buy A",
    ]
    branches = data["protocol"]["branches"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.4), facecolor="#090e0c")
    colors = ["#b6e4a9", "#728678", "#d9dfd8", "#728678", "#c2ccc0", "#728678"]
    for ax, summary in zip(axes.flat, data["summary"], strict=True):
        means, errors = [], []
        for branch in branches:
            values = [
                t["net_gain"]
                for t in data["trials"]
                if t["task"] == summary["task"] and t["branch"] == branch
            ]
            means.append(mean(values))
            errors.append(2 * stdev(values) / len(values) ** 0.5)
        ax.set_facecolor("#090e0c")
        ax.barh(
            range(6),
            means,
            color=colors,
            height=0.52,
            xerr=errors,
            error_kw={"ecolor": "#dde6da", "linewidth": 0.8, "capsize": 2},
        )
        ax.invert_yaxis()
        ax.set_yticks(range(6), labels, color="#c2ccc0")
        ax.set_xlim(-0.043, 0.08)
        ax.set_xticks(
            [-0.03, 0, 0.03, 0.06], ["−0.03", "0", "+0.03", "+0.06"], color="#94a591"
        )
        ax.axvline(0, color="#53604f", linewidth=0.8)
        ax.tick_params(axis="both", length=0, pad=8)
        ax.set_title(
            summary["task"].capitalize(),
            loc="left",
            color="#e2e9de",
            pad=18,
            fontsize=13,
        )
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(axis="x", color="#1d281f", linewidth=0.5)
        ax.set_axisbelow(True)
    fig.subplots_adjust(
        left=0.13, right=0.96, top=0.81, bottom=0.13, hspace=0.52, wspace=0.55
    )
    fig.text(
        0.06, 0.93, "When is information worth buying?", color="#e2e9de", fontsize=23
    )
    fig.text(
        0.06,
        0.883,
        "Incremental prediction value after cost · 12 matched seeds · all branches retained",
        color="#94a591",
        fontsize=11,
    )
    fig.text(
        0.06,
        0.062,
        "Synthetic virtual utility. No live payments or neural features. Error bars: mean ± 2 standard errors.",
        color="#94a591",
        fontsize=10,
    )
    fig.text(
        0.06,
        0.033,
        "Positive values improve on the same free forecast. Forgetting helps reversal, but loses on stable tasks.",
        color="#94a591",
        fontsize=10,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output.with_suffix(".svg"), metadata={"Date": None})
    svg_path = args.output.with_suffix(".svg")
    svg_path.write_text(
        re.sub(
            r'd="[^"]*"',
            lambda match: re.sub(r"\s+", " ", match.group()).rstrip(),
            svg_path.read_text(),
        )
    )
    fig.savefig(
        args.output.with_suffix(".png"),
        dpi=220,
        metadata={"Software": "Nuria research figure"},
    )
    plt.close(fig)


if __name__ == "__main__":
    main()
