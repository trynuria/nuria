"""Replay unchanged programs with a shared, read-only Matplotlib font index.

Only the cache JSON is shared. Each job retains its own writable configuration
folder, program, inputs and observations. The existing OS boundary protects the
external cache target; the original replayer and all checkers stay unchanged.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

script = Path(__file__).resolve().with_name("replay.py")
spec = importlib.util.spec_from_file_location("continuity_replay", script)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
OriginalExecutor = replay.IsolatedPython


def cached_executor(cache_directory):
    cache_directory = Path(cache_directory).resolve()
    candidates = sorted(cache_directory.glob("fontlist-v*.json"))
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise ValueError("Supply one regular, existing Matplotlib font index")
    cache_file = candidates[0]
    raw = cache_file.read_bytes()
    if not 1 <= len(raw) <= 1048576:
        raise ValueError("Font index exceeds its finite cache contract")
    value = json.loads(raw)
    if type(value) is not dict or type(value.get("ttflist")) is not list:
        raise ValueError("The input is not a Matplotlib font index")

    class CachedExecutor(OriginalExecutor):
        def __init__(self, root, *, dependency_roots=(), **kwargs):
            super().__init__(
                root,
                dependency_roots=(*dependency_roots, cache_directory),
                **kwargs,
            )

        def _command(self, work, driver):
            config = work / "mplconfig"
            config.mkdir(mode=0o700)
            (config / cache_file.name).symlink_to(cache_file)
            return super()._command(work, driver)

    return CachedExecutor, hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--font-cache", required=True, type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    executor, fingerprint = cached_executor(args.font_cache)
    replay.IsolatedPython = executor
    report = replay.verify(script.parent, args.workspace, args.dependency_root)
    report["read_only_font_cache_sha256"] = fingerprint
    report["original_replay_source_sha256"] = hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    report["programs_and_checkers_changed"] = False
    print(json.dumps(report, sort_keys=True))
    if report["mismatches"]:
        raise SystemExit("Independent replay differs; original grades remain unchanged")
