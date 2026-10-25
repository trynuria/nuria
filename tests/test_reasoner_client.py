import http.client
import io
import json
import unittest

from cognition.reasoner_client import LocalReasoner, ReasonerError


class RecordingOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return io.BytesIO(self.response)


def packet(content='{"tool":"list_tables","arguments":{}}', **changes):
    data = {
        "model": "default_model",
        "usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    data.update(changes)
    return json.dumps(data).encode()


class ReasonerClientTests(unittest.TestCase):
    def client(self, response, **kwargs):
        client = LocalReasoner(
            "http://127.0.0.1:19487/v1/chat/completions", "default_model", **kwargs
        )
        client._opener = RecordingOpener(response)
        return client

    def test_content_and_observed_usage_are_bound_to_one_request(self):
        client = self.client(packet())
        reply = client.complete(
            [{"role": "user", "content": "Inspect the database"}], max_tokens=32, seed=7
        )
        self.assertEqual(reply["usage"]["completion_tokens"], 15)
        self.assertEqual(reply["usage"]["total_tokens"], 45)
        self.assertEqual(len(reply["request_sha256"]), 64)
        request, timeout = client._opener.requests[0]
        body = json.loads(request.data)
        self.assertEqual(body["max_tokens"], 32)
        self.assertEqual(body["seed"], 7)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(timeout, 120)

    def test_missing_or_inconsistent_usage_stops_without_a_retry(self):
        for usage in (
            {},
            {"prompt_tokens": 1, "completion_tokens": -1},
            {"prompt_tokens": 1, "completion_tokens": True},
            {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 5},
        ):
            client = self.client(packet(usage=usage))
            with self.subTest(usage=usage), self.assertRaises(ReasonerError) as raised:
                client.complete([{"role": "user", "content": "Inspect"}])
            self.assertTrue(raised.exception.usage_unknown)
            self.assertIsNone(raised.exception.usage)
            self.assertEqual(len(client._opener.requests), 1)

    def test_truncated_empty_output_retains_valid_reasoning_inclusive_usage(self):
        for content in (None, "", "  \n"):
            response = packet(
                choices=[
                    {
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "reasoning": "private reasoning must never enter diagnostics",
                        },
                        "finish_reason": "length",
                    }
                ],
                usage={
                    "prompt_tokens": 73,
                    "completion_tokens": 32,
                    "total_tokens": 105,
                },
            )
            client = self.client(response)
            with (
                self.subTest(content=content),
                self.assertRaises(ReasonerError) as raised,
            ):
                client.complete([{"role": "user", "content": "Inspect"}], max_tokens=32)
            error = raised.exception
            self.assertEqual(error.code, "model_output_incomplete")
            self.assertFalse(error.usage_unknown)
            self.assertEqual(
                error.usage,
                {"prompt_tokens": 73, "completion_tokens": 32, "total_tokens": 105},
            )
            self.assertEqual(error.diagnostic["finish_reason"], "length")
            self.assertEqual(error.diagnostic["content_type"], type(content).__name__)
            self.assertEqual(len(error.diagnostic["request_sha256"]), 64)
            self.assertEqual(len(error.diagnostic["response_sha256"]), 64)
            self.assertNotIn("private reasoning", json.dumps(error.diagnostic))
            self.assertEqual(len(client._opener.requests), 1)

    def test_invalid_content_or_finish_reason_preserves_valid_usage(self):
        for content, finish in (
            (False, "stop"),
            ("", "stop"),
            ("generated private content", float("nan")),
        ):
            client = self.client(
                packet(
                    choices=[
                        {
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": finish,
                        }
                    ]
                )
            )
            with (
                self.subTest(content=content, finish=finish),
                self.assertRaises(ReasonerError) as raised,
            ):
                client.complete([{"role": "user", "content": "Inspect"}])
            self.assertEqual(raised.exception.code, "model_protocol_error")
            self.assertFalse(raised.exception.usage_unknown)
            self.assertEqual(raised.exception.usage["completion_tokens"], 15)
            self.assertNotIn(
                "generated private content",
                json.dumps(raised.exception.diagnostic, allow_nan=False),
            )

    def test_chunked_incomplete_read_has_unknown_usage_and_no_retry(self):
        class Socket:
            def makefile(self, mode):
                return io.BytesIO(
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n20\r\n{bad"
                )

        class TruncatedOpener:
            calls = 0

            def open(self, request, timeout):
                self.calls += 1
                response = http.client.HTTPResponse(Socket())
                response.begin()
                return response

        client = self.client(packet())
        client._opener = TruncatedOpener()
        with self.assertRaises(ReasonerError) as raised:
            client.complete([{"role": "user", "content": "Inspect"}])
        self.assertEqual(raised.exception.code, "model_transport_error")
        self.assertTrue(raised.exception.usage_unknown)
        self.assertIsNone(raised.exception.usage)
        self.assertEqual(client._opener.calls, 1)
        self.assertNotIn("response_sha256", raised.exception.diagnostic)

    def test_reply_cannot_exceed_the_requested_token_or_byte_bound(self):
        client = self.client(packet())
        with self.assertRaises(ReasonerError):
            client.complete([{"role": "user", "content": "Inspect"}], max_tokens=14)
        client = self.client(packet("x" * 2048), max_response_bytes=1024)
        with self.assertRaises(ReasonerError) as raised:
            client.complete([{"role": "user", "content": "Inspect"}])
        self.assertEqual(raised.exception.code, "model_response_limit")

    def test_no_remote_endpoint_credentials_or_redirect_target_in_configuration(self):
        for endpoint in (
            "https://api.example.com/v1/chat/completions",
            "http://localhost:19487/v1/chat/completions",
            "http://127.0.0.1:19487/v1/chat/completions?token=x",
            "http://account:secret@127.0.0.1:19487/v1/chat/completions",
            "http://127.0.0.1:19487/",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                LocalReasoner(endpoint, "default_model")

    def test_input_bound_is_checked_before_any_inference(self):
        client = self.client(packet(), max_input_bytes=1024)
        with self.assertRaises(ValueError):
            client.complete([{"role": "user", "content": "x" * 1500}])
        self.assertEqual(client._opener.requests, [])


if __name__ == "__main__":
    unittest.main()
