"""Render Nuria's vector launch artwork and an explanatory motion sequence.

The animation illustrates architecture. It does not depict recorded neural
activity, token transactions or successful payments. CairoSVG and ffmpeg are
optional artwork tools, not application dependencies.
"""

import argparse
import html
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BG, INK, GREEN, MUTED, LINE = "#0d1112", "#eef0e8", "#b8d4a7", "#909b99", "#2b3535"


def text(x, y, value, size=24, color=INK, weight=400):
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
        f'font-weight="{weight}" font-family="Arial, sans-serif">'
        f"{html.escape(value)}</text>"
    )


def mark(x, y, size):
    svg = (ROOT / "brand/nuria-mark.svg").read_text()
    body = svg[svg.index(">") + 1 : svg.rindex("</svg>")]
    return f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="300 260 650 675">{body}</svg>'


def wordmark(x, y, width=255):
    source = (ROOT / "observatory.html").read_text()
    found = re.search(r'<svg\s+class="wordmark-svg".*?</svg>', source, re.S)
    if not found:
        raise ValueError("The approved wordmark is missing")
    body = found.group()[found.group().index(">") + 1 : found.group().rindex("</svg>")]
    return f'<svg x="{x}" y="{y}" width="{width}" height="{width * 150 / 430}" viewBox="0 0 430 150" fill="none">{body}</svg>'


def document(body, width=1536, height=864, title="Nuria"):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{html.escape(title)}"><title>{html.escape(title)}</title>'
        f'<rect width="{width}" height="{height}" fill="{BG}"/>{body}</svg>'
    )


def trajectory(x=980, y=225, scale=1, progress=None):
    parts = []
    rows = ((0, 0), (140, 60), (55, 178), (218, 236), (129, 361), (265, 436))
    labels = ("INPUT", "STATE", "MEMORY", "CHOICE", "OUTCOME", "NEXT CHOICE")
    for index, ((px, py), label) in enumerate(zip(rows, labels)):
        px, py = x + px * scale, y + py * scale
        if index:
            ax, ay = rows[index - 1]
            ax, ay = x + ax * scale, y + ay * scale
            parts.append(
                f'<path d="M{ax} {ay} C{ax - 85 * scale} {ay},{px - 65 * scale} {py},{px} {py}" fill="none" stroke="{LINE}" stroke-width="2"/>'
            )
            if progress is not None and index - 1 <= progress < index:
                t = progress - index + 1
                qx = (
                    (1 - t) ** 3 * ax
                    + 3 * (1 - t) ** 2 * t * (ax - 85 * scale)
                    + 3 * (1 - t) * t * t * (px - 65 * scale)
                    + t**3 * px
                )
                qy = (
                    (1 - t) ** 3 * ay
                    + 3 * (1 - t) ** 2 * t * ay
                    + 3 * (1 - t) * t * t * py
                    + t**3 * py
                )
                parts.append(f'<circle cx="{qx}" cy="{qy}" r="5" fill="{GREEN}"/>')
        active = progress is None or abs(progress - index) < 0.6
        color = GREEN if active else MUTED
        parts.append(
            f'<circle cx="{px}" cy="{py}" r="6" fill="{BG}" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(text(px + 17, py + 5, label, 13, color))
    return "".join(parts)


def cover(banner=False):
    height = 512 if banner else 864
    left = 310 if banner else 96
    body = mark(left, 85 if banner else 105, 62) + wordmark(
        left + 90, 92 if banner else 112, 236
    )
    body += text(left, 247 if banner else 365, "An onchain", 46 if banner else 64)
    body += text(
        left, 305 if banner else 442, "consciousness experiment.", 46 if banner else 64
    )
    body += text(
        left,
        375 if banner else 560,
        "One entity. A history that carries forward.",
        20 if banner else 26,
        MUTED,
    )
    body += text(left, 443 if banner else 739, "nuria.network", 17, GREEN)
    body += trajectory(
        1090 if banner else 1060, 70 if banner else 180, 0.76 if banner else 1.1
    )
    return document(
        body, height=height, title="Nuria — An onchain consciousness experiment"
    )


def flow(kind, progress=None):
    body = mark(80, 62, 48) + wordmark(148, 68, 172)
    body += text(
        80,
        232,
        "The same entity, after every outcome."
        if kind == "history"
        else "From creator fees to a measured outcome.",
        48,
    )
    body += text(
        80,
        292,
        "Persistent state, recorded choices and feedback that survives a restart."
        if kind == "history"
        else "Prepared financial path. Live token claims and paid delivery still require verification.",
        22,
        MUTED,
    )
    labels = (
        (
            ("Experience", "Finalized token inputs"),
            ("State", "Neurons and memory"),
            ("Choice", "Recorded scores"),
            ("Outcome", "Measured feedback"),
        )
        if kind == "history"
        else (
            ("Claim", "Verified creator wallet"),
            ("Inventory", "SOL / approved USDC"),
            ("Purchase", "Bounded provider job"),
            ("Evidence", "Payment + delivery"),
        )
    )
    for index, (label, detail) in enumerate(labels):
        x = 80 + index * 360
        active = progress is None or index <= progress
        color = GREEN if active else LINE
        body += f'<rect x="{x}" y="390" width="296" height="205" rx="14" fill="#12191a" stroke="{color}"/>'
        body += text(x + 26, 460, label, 32)
        body += text(x + 26, 520, detail, 17, MUTED)
        if index < 3:
            body += f'<path d="M{x + 296} 492h64" stroke="{LINE}" fill="none"/>'
            if progress is not None and index <= progress < index + 1:
                body += f'<circle cx="{x + 296 + 64 * (progress - index)}" cy="492" r="5" fill="{GREEN}"/>'
    body += f'<path d="M1370 595v70H228v-70" stroke="{LINE}" fill="none"/>'
    body += text(545, 704, "A later outcome changes the next choice.", 21, GREEN)
    body += text(
        80,
        800,
        "ARCHITECTURE ILLUSTRATION" if progress is not None else "NURIA / SYSTEM NOTES",
        13,
        MUTED,
    )
    body += text(1280, 800, "nuria.network", 17, MUTED)
    return document(
        body,
        title="Nuria persistent learning loop"
        if kind == "history"
        else "Nuria financial and evidence path",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--video", action="store_true")
    args = parser.parse_args()
    import cairosvg

    args.output.mkdir(parents=True, exist_ok=True)
    assets = {
        "nuria-cover": cover(),
        "nuria-twitter-banner": cover(True),
        "persistent-entity": flow("history"),
        "fee-to-outcome": flow("fees"),
    }
    for name, svg in assets.items():
        (args.output / (name + ".svg")).write_text(svg + "\n")
        cairosvg.svg2png(
            bytestring=svg.encode(),
            write_to=str(args.output / (name + ".png")),
            scale=2,
        )
    if args.video:
        frames = args.output / "motion-frames"
        frames.mkdir(exist_ok=False)
        fps, duration = 24, 12
        for frame in range(fps * duration):
            phase = frame / (fps * duration)
            progress = min(3.999, 4 * phase)
            svg = flow("history", progress)
            # Illustrative progression; there are no fabricated telemetry values.
            cairosvg.svg2png(
                bytestring=svg.encode(),
                write_to=str(frames / f"{frame:04}.png"),
                output_width=1920,
                output_height=1080,
            )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(fps),
                "-i",
                str(frames / "%04d.png"),
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(args.output / "nuria-persistent-entity.mp4"),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
