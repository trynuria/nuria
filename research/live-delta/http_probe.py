"""Compare full and incremental HTTP responses on actual live state changes."""

import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/nuria/research-deps")
import jsonpatch


def fetch(url, headers=None):
    started = time.perf_counter_ns()
    request = urllib.request.Request(
        url, headers={"Accept-Encoding": "gzip", **(headers or {})}
    )
    try:
        response = urllib.request.urlopen(request, timeout=12)
    except urllib.error.HTTPError as exc:
        if exc.code != 304:
            raise
        response = exc
    with response:
        raw = response.read()
        received = time.perf_counter_ns()
        body = (
            gzip.decompress(raw)
            if response.headers.get("Content-Encoding") == "gzip"
            else raw
        )
        data = json.loads(body) if body else None
        return {
            "status": response.code,
            "headers": dict(response.headers),
            "wire_bytes": len(raw),
            "http_ms": (received - started) / 1e6,
            "parse_ms": (time.perf_counter_ns() - received) / 1e6,
        }, data


def run(url, output):
    initial, previous = fetch(url)
    version = initial["headers"]["X-Studio-Version"]
    etag, _ = fetch(url, {"If-None-Match": initial["headers"]["ETag"]})
    assert etag["status"] == 304
    rows = []
    deadline = time.monotonic() + 80
    while len(rows) < 4 and time.monotonic() < deadline:
        time.sleep(1)
        delta, payload = fetch(url + "?since=" + version)
        if delta["status"] == 204:
            continue
        next_version = delta["headers"]["X-Studio-Version"]
        start = time.perf_counter_ns()
        if delta["headers"]["X-Studio-Update"] == "patch-v1":
            assert delta["headers"]["X-Studio-Base"] == version
            ops = [
                dict(
                    op=("add", "remove", "replace")[o[0]],
                    path=o[1],
                    **({"value": o[2]} if o[0] != 1 else {}),
                )
                for o in payload
            ]
            current = jsonpatch.apply_patch(previous, ops, in_place=False)
        else:
            current = payload
        delta["apply_ms"] = (time.perf_counter_ns() - start) / 1e6
        full, complete = fetch(url)
        if full["headers"]["X-Studio-Version"] != next_version:
            # A new writer revision raced the check. Keep the valid state and
            # verify the next transition; never compare different versions.
            previous, version = complete, full["headers"]["X-Studio-Version"]
            continue
        assert json.dumps(current, sort_keys=True) == json.dumps(
            complete, sort_keys=True
        )
        rows.append(
            {
                "base": version,
                "version": next_version,
                "exact": True,
                "delta": delta,
                "cached_full": full,
            }
        )
        previous, version = current, next_version
    fallback, complete = fetch(url + "?since=" + "0" * 64)
    assert fallback["headers"]["X-Studio-Update"] == "full"
    assert complete["projects"]
    result = {
        "scope": "Actual changing public state over loopback HTTP, equal server cache and gzip-3 for both paths. Timing includes receive, decompression, parsing and patch application, excludes rendering and internet latency. Every compared response has the same target version. Initial parsing/compression is paid by the first request and included separately.",
        "initial": initial,
        "conditional_status": etag["status"],
        "stale_base_recovery": True,
        "rows": rows,
    }
    Path(output).write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "changes": len(rows),
                "exact": all(r["exact"] for r in rows),
                "full_bytes": [r["cached_full"]["wire_bytes"] for r in rows],
                "delta_bytes": [r["delta"]["wire_bytes"] for r in rows],
                "conditional_status": etag["status"],
                "stale_base_recovery": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3043/api/studio")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.url, args.output)
