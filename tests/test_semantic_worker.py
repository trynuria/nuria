"""One-shot transport fixtures, using no ONNX session or pretrained inference."""

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cognition import semantic_worker as worker
from cognition.semantic_encoder import binding_from_manifest, digest
from tests import test_semantic_encoder as encoder_fixtures


class SemanticWorkerTests(unittest.TestCase):
    def setUp(self):
        # Reuse only inert file/manifest construction from the encoder fixture.
        self.fixture = encoder_fixtures.SemanticEncoderTests()
        self.fixture.setUp()
        self.root = self.fixture.root

    def configured(self, mode="normal", *, max_calls=12, timeout=3, symlink=False):
        expected = digest(binding_from_manifest(self.fixture.manifest_path, max_calls))
        script = self.root / (mode + ".py")
        script.write_text(
            "import hashlib,json,os,struct,sys,time\n"
            "def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()\n"
            "data=json.load(sys.stdin);q=data['question']\n"
            "assert sys.flags.isolated and sys.flags.no_user_site\n"
            "assert 'PYTHONPATH' not in os.environ\n"
            "assert q not in sys.argv\n"
            + ("time.sleep(5)\n" if mode == "timeout" else "")
            + (
                "sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(5)\n"
                if mode == "flood"
                else ""
            )
            + (
                "sys.stderr.write('x'*20000);sys.stderr.flush();time.sleep(5)\n"
                if mode == "error-flood"
                else ""
            )
            + "vector=[1.0]+[0.0]*383\n"
            + ("vector=[0.0]*384\n" if mode == "zero-vector" else "")
            + "r={'schema':'nuria.semantic-observation.v1','binding_sha256':"
            + repr(expected)
            + ",'text_sha256':hashlib.sha256(q.encode()).hexdigest(),"
            "'vector_float32_sha256':hashlib.sha256(struct.pack('<384f',*vector)).hexdigest(),"
            "'dimensions':384,'wordpieces_used':8,'wordpieces_before_truncation':8,"
            "'input_bytes':len(q.encode()),'call':1,'wall_seconds':0.01,"
            "'truncated':False,'fixture':True}\n"
            + ("r['text_sha256']='0'*64\n" if mode == "wrong-text" else "")
            + ("r['call']=2\n" if mode == "wrong-call" else "")
            + ("r['input_bytes']=0\n" if mode == "wrong-bytes" else "")
            + ("r['wordpieces_before_truncation']=300\n" if mode == "truncated" else "")
            + ("r['wall_seconds']=-1\n" if mode == "wrong-time" else "")
            + ("vector=['1']+['0']*383\n" if mode == "string-vector" else "")
            + ("vector=[True]+[False]*383\n" if mode == "bool-vector" else "")
            + "r['receipt_sha256']=digest(r)\n"
            "print(json.dumps({'vector':vector,'receipt':r}))\n"
        )
        executable = Path(sys.executable)
        if symlink:
            executable = self.root / "configured-python"
            executable.symlink_to(sys.executable)
        return worker.OneShotSemanticEncoder(
            executable,
            self.root,
            self.fixture.manifest_path,
            max_calls=max_calls,
            timeout_seconds=timeout,
            worker_file=script,
            worker_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
        )

    def test_question_stays_in_stdin_and_parent_binds_the_complete_observation(self):
        encoder = self.configured()
        with patch.dict("os.environ", {"PYTHONPATH": "untrusted-user-path"}):
            packet = encoder.observe("Count unverified deliveries.")
        self.assertEqual(np.linalg.norm(packet["vector"]), 1)
        self.assertEqual(packet["receipt"]["binding_sha256"], encoder.binding_sha256)
        self.assertEqual(
            packet["receipt"]["underlying_observation"]["binding_sha256"],
            encoder.encoder_binding_sha256,
        )
        self.assertEqual(packet["receipt"]["worker_sha256"], encoder.worker_sha256)
        self.assertTrue(packet["receipt"]["underlying_observation"]["fixture"])
        self.assertNotIn(str(self.root), json.dumps(encoder.binding))
        self.assertEqual(encoder.calls, 1)

    def test_configured_venv_entry_path_is_preserved_for_execution(self):
        encoder = self.configured(symlink=True)
        self.assertEqual(encoder.python, self.root / "configured-python")
        self.assertNotEqual(encoder.python, encoder.python.resolve())
        self.assertEqual(len(encoder.observe("Count tasks.")["vector"]), 384)

    def test_timeout_retains_failed_instance_without_automatic_retry(self):
        encoder = self.configured("timeout", timeout=1)
        with self.assertRaises(TimeoutError):
            encoder.observe("Count tasks.")
        self.assertTrue(encoder.failed)
        self.assertEqual(encoder.calls, 1)
        with self.assertRaises(ValueError):
            encoder.observe("Count tasks.")
        self.assertEqual(encoder.calls, 1)

    def test_output_and_error_floods_are_bounded(self):
        for mode in ("flood", "error-flood"):
            with self.subTest(mode=mode):
                encoder = self.configured(mode)
                with self.assertRaisesRegex(ValueError, "byte allowance"):
                    encoder.observe("Count tasks.")
                self.assertTrue(encoder.failed)

    def test_changed_question_or_degenerate_vector_is_rejected(self):
        for mode in (
            "wrong-text",
            "zero-vector",
            "wrong-call",
            "wrong-bytes",
            "truncated",
            "wrong-time",
            "string-vector",
            "bool-vector",
        ):
            with self.subTest(mode=mode):
                encoder = self.configured(mode)
                with self.assertRaises(ValueError):
                    encoder.observe("Count tasks.")
                self.assertTrue(encoder.failed)

    def test_exited_worker_descendant_is_still_stopped_on_timeout(self):
        encoder = self.configured()
        pid_file = self.root / "owned-descendant.pid"
        encoder.worker.write_text(
            "import os,signal,sys,time\n"
            "json_input=sys.stdin.read()\n"
            "if os.fork()==0:\n"
            " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f" open({str(pid_file)!r},'w').write(str(os.getpid()))\n"
            " time.sleep(20)\n"
            "else:\n"
            " os._exit(0)\n"
        )
        encoder = worker.OneShotSemanticEncoder(
            sys.executable,
            self.root,
            self.fixture.manifest_path,
            timeout_seconds=1,
            worker_file=encoder.worker,
        )
        with self.assertRaises(TimeoutError):
            encoder.observe("Count tasks.")
        descendant = int(pid_file.read_text())
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertTrue(result.returncode != 0 or "Z" in result.stdout)

    def test_mutated_worker_allowance_is_refused_before_disclosure(self):
        encoder = self.configured()
        encoder.timeout = 60
        with patch.object(worker, "_exchange") as exchange:
            with self.assertRaises(ValueError):
                encoder.observe("Count tasks.")
            exchange.assert_not_called()

    def test_worker_and_weight_changes_are_refused_before_disclosure(self):
        encoder = self.configured()
        encoder.worker.write_text("raise RuntimeError('changed fixture')")
        with patch.object(worker, "_exchange") as exchange:
            with self.assertRaises(ValueError):
                encoder.observe("Count tasks.")
            exchange.assert_not_called()
        other = self.configured("another")
        (self.root / "weights.onnx").write_bytes(b"changed fixture weights")
        with patch.object(worker, "_exchange") as exchange:
            with self.assertRaises(ValueError):
                other.observe("Count tasks.")
            exchange.assert_not_called()

    def test_process_call_ceiling_is_not_renewed_after_a_success(self):
        encoder = self.configured(max_calls=1)
        encoder.observe("Count tasks.")
        with patch.object(worker, "_exchange") as exchange:
            with self.assertRaises(ValueError):
                encoder.observe("Count tasks again.")
            exchange.assert_not_called()
        self.assertEqual(encoder.calls, 1)

    def test_invalid_or_oversized_question_never_launches_a_worker(self):
        encoder = self.configured()
        for question in ("", 42, "x" * 5001):
            with (
                self.subTest(question_type=type(question).__name__),
                patch.object(worker, "_exchange") as exchange,
            ):
                with self.assertRaises(ValueError):
                    encoder.observe(question)
                exchange.assert_not_called()
        self.assertEqual(encoder.calls, 0)


if __name__ == "__main__":
    unittest.main()
