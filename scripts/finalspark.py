"""Explicit one-shot biological-data tools; no daemon or production activation."""

import argparse
import json
from pathlib import Path

import numpy as np

from cognition.biology import (
    MAX_BYTES,
    bridge,
    digest,
    read_recording,
    validate_recording,
    waveform_features,
    write_new,
)


def capture_frames(directory):
    directory = Path(directory)
    receipt_path = directory / "receipt.json"
    if receipt_path.stat().st_size > MAX_BYTES:
        raise ValueError("Capture receipt byte limit")
    receipt = json.loads(receipt_path.read_bytes())
    if (
        not receipt.get("completed")
        or not receipt.get("disconnected")
        or not 1 <= len(receipt["frames"]) <= 32
    ):
        raise ValueError(
            "Only a completed, explicitly disconnected capture can be replayed"
        )
    frames = []
    for index, item in enumerate(receipt["frames"], 1):
        if item["file"] != f"frame-{index:03}.json":
            raise ValueError("Unexpected capture member path")
        frame = read_recording(directory / item["file"])
        if digest(frame) != item["sha256"] or frame["mode"] != "public_observed":
            raise ValueError("Capture frame identity changed")
        frames.append(frame)
    return frames


def import_recording(samples_path, metadata_path, output, dataset=None):
    """Import explicitly described numeric data; never infer biological provenance."""
    samples_path, metadata_path = Path(samples_path), Path(metadata_path)
    if (
        samples_path.stat().st_size > MAX_BYTES
        or metadata_path.stat().st_size > 64 * 1024
    ):
        raise ValueError("Input byte limit")
    metadata = json.loads(metadata_path.read_bytes())
    suffix = samples_path.suffix.lower()
    if suffix == ".json":
        matrix = np.asarray(json.loads(samples_path.read_bytes()))
    elif suffix == ".npy":
        matrix = np.load(samples_path, allow_pickle=False, mmap_mode="r")
    elif suffix in (".h5", ".hdf5"):
        if not dataset:
            raise ValueError("Explicit HDF5 dataset key required")
        try:
            import h5py
        except ImportError as error:
            raise RuntimeError(
                "HDF5 import needs optional h5py in the data environment"
            ) from error
        with h5py.File(samples_path, "r") as source:
            # Do not follow an external link supplied by a recording file.
            if any(
                not isinstance(
                    source.get(
                        "/".join(dataset.strip("/").split("/")[:i]), getlink=True
                    ),
                    h5py.HardLink,
                )
                for i in range(1, len(dataset.strip("/").split("/")) + 1)
            ):
                raise ValueError("External/soft HDF5 links are not recording arrays")
            array = source[dataset]
            if (
                array.ndim != 2
                or array.size > 32 * 16384
                or array.is_virtual
                or array.dtype.kind not in "fiu"
            ):
                raise ValueError("Unsupported HDF5 array dimensions or virtual dataset")
            if array.external:
                raise ValueError("External HDF5 storage refused")
            matrix = array[:]
    else:
        raise ValueError("Use numeric JSON, NPY or an explicit HDF5 dataset")
    if matrix.ndim != 2 or matrix.size > 32 * 16384 or matrix.dtype.kind not in "fiu":
        raise ValueError("Bounded channel-major numeric matrix required")
    if metadata.get("mode") not in (
        "recorded_biological",
        "controlled_biological",
        "synthetic_fixture",
    ):
        raise ValueError(
            "Explicit recorded/controlled provider claim or synthetic mode required"
        )
    metadata["samples"] = matrix.tolist()
    validate_recording(metadata)
    write_new(output, metadata)
    return {
        "sha256": digest(metadata),
        "mode": metadata["mode"],
        "features": waveform_features(metadata),
        "biological_origin_independently_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("output", type=Path)
    capture.add_argument("--frames", type=int, default=3)
    capture.add_argument("--seconds", type=float, default=25)
    replay = commands.add_parser("bridge")
    replay.add_argument("capture", type=Path)
    replay.add_argument("output", type=Path)
    imported = commands.add_parser("import")
    imported.add_argument("samples", type=Path)
    imported.add_argument("metadata", type=Path)
    imported.add_argument("output", type=Path)
    imported.add_argument("--dataset")
    compare = commands.add_parser("compare")
    compare.add_argument("course", type=Path)
    compare.add_argument("output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("result", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        from cognition.finalspark import PublicReader

        result = PublicReader(max_frames=args.frames, seconds=args.seconds).capture(
            args.output
        )
    elif args.command == "bridge":
        result = bridge(capture_frames(args.capture), args.output)
    elif args.command == "import":
        result = import_recording(
            args.samples, args.metadata, args.output, args.dataset
        )
    elif args.command == "compare":
        from cognition.biology_comparison import run_comparison

        result = run_comparison(args.course, args.output)
    else:
        from cognition.biology_comparison import verify_comparison

        result = verify_comparison(args.result)
    print(json.dumps(result, allow_nan=False))
    if result.get("completed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
