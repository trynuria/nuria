"""Read-only shared-cache isolation with a synthetic index, without model calls."""

import importlib.util
import json
import unittest
import uuid
from pathlib import Path


class CachedReplayTests(unittest.TestCase):
    def test_shared_index_is_readable_and_mutation_is_denied(self):
        source = Path(__file__).resolve().parents[1]
        path = (
            source
            / "research/outcome-learning/reasoning-continuity-09/replay-cached.py"
        )
        spec = importlib.util.spec_from_file_location("cached_replay_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = source / ".test-state" / ("cache-control-" + uuid.uuid4().hex)
        cache = root / "cache"
        cache.mkdir(parents=True, mode=0o700)
        target = cache / "fontlist-vfixture.json"
        original = json.dumps({"ttflist": [], "_version": "fixture"}).encode()
        target.write_bytes(original)
        Executor, _ = module.cached_executor(cache)
        result = Executor(root / "executions").run(
            "import errno\nfrom pathlib import Path\nindex=Path('mplconfig/fontlist-vfixture.json')",
            "assert index.read_bytes()==" + repr(original) + "\n"
            "try:\n index.write_bytes(b'forbidden')\n"
            "except OSError as error:\n assert error.errno in (errno.EACCES,errno.EPERM,errno.EROFS)\n"
            "else:\n raise AssertionError('Shared cache was writable')\n",
            label="readonly",
        )
        self.assertTrue(result["passed"], result["stderr"])
        self.assertEqual(target.read_bytes(), original)
