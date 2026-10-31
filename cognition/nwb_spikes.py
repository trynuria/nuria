"""Pinned DANDI acquisition and PyNWB import of curated human-organoid spikes."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

import numpy as np

from cognition.biology import digest, write_new

SOURCE = {
    "archive": "DANDI",
    "dandiset": "001603",
    "version": "0.260923.0623",
    "asset_id": "356d4cb3-c817-419b-bf7d-3345931f41be",
    "path": "sub-HO3/sub-HO3_ses-20250916T190937.nwb",
    "subject_id": "HO3",
    "species": "Homo sapiens",
    "bytes": 9066104,
    "sha256": "d5e5f72db06de01db28c7fbbe5ef2166b8e739a8810e1b548463edf02555c5b1",
    "license": "CC-BY-4.0",
    "dataset_doi": "10.48324/dandi.001603/0.260923.0623",
    "publication_doi": "10.1038/s41593-025-02111-0",
    "attribution": "van der Molen et al., Preconfigured neuronal firing sequences in human brain organoids, DANDI 001603 (2026)",
    "url": "https://dandiarchive.org/dandiset/001603/0.260923.0623",
}


def _metadata(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Nuria-DANDI-replay/1"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("Archive metadata byte limit")
    return json.loads(raw)


def download(directory):
    """One pinned 9 MB asset, with archive license, species and digest checks."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    base = "https://api.dandiarchive.org/api/"
    metadata = _metadata(base + "assets/" + SOURCE["asset_id"] + "/")
    dataset = _metadata(
        base + f"dandisets/{SOURCE['dandiset']}/versions/{SOURCE['version']}/"
    )
    members = _metadata(
        base
        + f"dandisets/{SOURCE['dandiset']}/versions/{SOURCE['version']}/assets/?page_size=200"
    )
    participants = metadata.get("wasAttributedTo", [])
    if (
        dataset.get("license") != ["spdx:CC-BY-4.0"]
        or members.get("next") is not None
        or not any(
            item.get("asset_id") == SOURCE["asset_id"]
            and item.get("path") == SOURCE["path"]
            for item in members.get("results", [])
        )
        or metadata.get("contentSize") != SOURCE["bytes"]
        or metadata.get("path") != SOURCE["path"]
        or metadata.get("digest", {}).get("dandi:sha2-256") != SOURCE["sha256"]
        or not any(
            p.get("identifier") == SOURCE["subject_id"]
            and p.get("species", {}).get("name") == "Homo sapiens - Human"
            for p in participants
        )
        or not any(
            x.get("status") == "dandi:OpenAccess" for x in metadata.get("access", [])
        )
    ):
        raise ValueError("Pinned archive identity, species, access or license changed")
    request = urllib.request.Request(
        base + "assets/" + SOURCE["asset_id"] + "/download/",
        headers={"User-Agent": "Nuria-DANDI-replay/1"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(SOURCE["bytes"] + 1)
    if (
        len(raw) != SOURCE["bytes"]
        or hashlib.sha256(raw).hexdigest() != SOURCE["sha256"]
    ):
        raise ValueError("Recording size or SHA256 mismatch; no recording accepted")
    path = directory / "human-HO3.nwb"
    with path.open("xb") as output:
        path.chmod(0o600)
        output.write(raw)
    write_new(directory / "source.json", SOURCE)
    receipt = {
        "source": SOURCE,
        "verified_sha256": hashlib.sha256(raw).hexdigest(),
        "wall_seconds": time.monotonic() - started,
    }
    write_new(directory / "download.json", receipt)
    return receipt


def validate_binned(value):
    if (
        value.get("schema") != 1
        or value.get("representation") != "sorted_unit_spike_counts"
        or value.get("mode") != "recorded_biological"
    ):
        raise ValueError("Expected recorded sorted-unit spike counts")
    if value.get("source") != SOURCE:
        raise ValueError("Binned source identity changed")
    ids = value.get("unit_ids", [])
    if (
        not 1 <= len(ids) <= 128
        or len(set(ids)) != len(ids)
        or any(type(i) is not int for i in ids)
    ):
        raise ValueError("Distinct bounded sorted-unit identities required")
    raw = value.get("counts", [])
    if (
        not isinstance(raw, list)
        or not 32 <= len(raw) <= 1000
        or any(
            not isinstance(row, list)
            or len(row) != len(ids)
            or any(type(v) is not int or not 0 <= v <= 100000 for v in row)
            for row in raw
        )
    ):
        raise ValueError("Bounded nonnegative integer count matrix required")
    counts = np.asarray(raw, dtype=np.int64)
    width = value.get("bin_seconds")
    if (
        type(width) not in (int, float)
        or not np.isfinite(width)
        or not 0.05 <= width <= 1
    ):
        raise ValueError("Declared bin duration required")
    if (
        counts.sum() != value.get("included_spikes")
        or value.get("source_total_spikes", -1) < counts.sum()
    ):
        raise ValueError("Spike conservation mismatch")
    if value.get("stop_seconds") != value.get("start_seconds", 0) + len(counts) * width:
        raise ValueError("Window duration and bin count disagree")
    if not value.get("timing_reference") or value.get("live") is not False:
        raise ValueError("Recorded timing reference required; never live")
    return counts


def import_nwb(path, output, *, seconds=120, bin_seconds=0.25):
    """Use PyNWB's Units table; sorted unit IDs are not electrode/neuron claims."""
    path = Path(path)
    if (
        path.stat().st_size != SOURCE["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE["sha256"]
    ):
        raise ValueError("Only the pinned original recording is accepted")
    from pynwb import NWBHDF5IO

    if (
        type(seconds) not in (int, float)
        or not 8 <= seconds <= 180
        or type(bin_seconds) not in (int, float)
        or not 0.05 <= bin_seconds <= 1
        or not float(seconds / bin_seconds).is_integer()
    ):
        raise ValueError("Use an 8–180 second interval and exact 50–1000 ms bins")
    if not 32 <= seconds / bin_seconds <= 1000:
        raise ValueError("Use 32–1000 bins")
    with NWBHDF5IO(str(path), "r", load_namespaces=True) as reader:
        nwb = reader.read()
        if (
            nwb.subject is None
            or nwb.subject.species != SOURCE["species"]
            or nwb.subject.subject_id != SOURCE["subject_id"]
        ):
            raise ValueError(
                "NWB human/subject identity disagrees with archive metadata"
            )
        units = nwb.units
        if units is None or not 1 <= len(units) <= 128:
            raise ValueError("Missing or oversized curated Units table")
        grid = nwb.processing["curated_binning"]["t_spk_mat"]
        if (
            grid.starting_time != 0
            or grid.rate != 1000
            or grid.data.shape[1] != len(units)
            or grid.data.shape[0] / grid.rate < seconds
        ):
            raise ValueError(
                "Publisher time-grid coverage does not establish the requested interval"
            )
        ids = units.id[:].tolist()
        edges = np.arange(round(seconds / bin_seconds) + 1) * bin_seconds
        columns, included, total = [], 0, 0
        for index in range(len(ids)):
            times = np.asarray(units["spike_times"][index], dtype=float)
            if (
                not np.isfinite(times).all()
                or np.any(times < 0)
                or np.any(np.diff(times) < 0)
            ):
                raise ValueError("Invalid or unordered curated spike times")
            selected = times[(times >= 0) & (times < seconds)]
            column = np.histogram(selected, bins=edges)[0]
            columns.append(column)
            included += len(selected)
            total += len(times)
        counts = np.column_stack(columns)
        # Retain an independent publisher-grid cross-check, without changing
        # canonical half-open event-time bins to improve agreement or scores.
        samples_per_bin = bin_seconds * grid.rate
        cross_check = {"available": False}
        if float(samples_per_bin).is_integer():
            reference = np.asarray(grid.data[: int(seconds * grid.rate)])
            if (
                reference.dtype.kind not in "biuf"
                or not np.isfinite(reference).all()
                or np.any(reference < 0)
                or np.any(reference != np.floor(reference))
            ):
                raise ValueError("Invalid publisher count grid")
            reference = reference.reshape(
                len(counts), int(samples_per_bin), len(ids)
            ).sum(axis=1)
            cross_check = {
                "available": True,
                "exactly_equal": bool(np.array_equal(counts, reference)),
                "differing_unit_bins": int(np.count_nonzero(counts != reference)),
                "publisher_spikes": int(reference.sum()),
                "event_time_spikes": included,
            }
        result = {
            "schema": 1,
            "representation": "sorted_unit_spike_counts",
            "mode": "recorded_biological",
            "live": False,
            "source": SOURCE,
            "unit_ids": ids,
            "bin_seconds": bin_seconds,
            "start_seconds": 0,
            "stop_seconds": seconds,
            "timing_reference": "NWB spike_times seconds relative to session_start_time; bins [start, stop)",
            "session_start_time_metadata": nwb.session_start_time.isoformat(),
            "nwb_identifier": nwb.identifier,
            "counts": counts.tolist(),
            "included_spikes": included,
            "source_total_spikes": total,
            "excluded_outside_interval_spikes": total - included,
            "publisher_grid_check": cross_check,
            "scope": "Curated sorted units; no independent identification of individual neurons or live tissue access",
        }
    validate_binned(result)
    write_new(output, result)
    return {
        "binned_sha256": digest(result),
        "units": len(ids),
        "bins": len(counts),
        "included_spikes": included,
        "excluded_outside_interval_spikes": total - included,
        "publisher_grid_check": cross_check,
    }
