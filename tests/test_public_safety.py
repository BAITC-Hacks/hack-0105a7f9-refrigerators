import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from contractor_match.api import create_app
from contractor_match.request_limits import MAX_REQUEST_BYTES, RequestBodyLimitMiddleware


REQUEST = {"city": "Алматы", "category": "Ведущий", "event_format": "корпоратив",
           "date": "2026-10-15", "budget_kzt": 1200000}


class PublicSafetyTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AI_PROVIDER": "local", "RANKING_PROVIDER": "tfidf",
                                     "CATALOGUE_PROVIDER": "csv", "CORS_ALLOWED_ORIGINS": "http://localhost:5173"})
        env.start()
        self.addCleanup(env.stop)
        self.client = self.enterContext(TestClient(create_app()))

    def test_oversized_body_has_safe_error_cors_and_request_id(self):
        with patch("contractor_match.api.recommend", side_effect=AssertionError("must not run")):
            result = self.client.post("/recommendations", content=b"x" * (MAX_REQUEST_BYTES + 1),
                                      headers={"origin": "http://localhost:5173", "content-type": "application/json"})
        self.assertEqual(result.status_code, 413)
        self.assertEqual(result.json()["error"]["code"], "request_too_large")
        self.assertRegex(result.headers["x-request-id"], "^[a-f0-9]{32}$")
        self.assertEqual(result.headers["access-control-allow-origin"], "http://localhost:5173")

    def test_stream_limit_without_or_with_forged_length_and_disconnect(self):
        async def scenario(chunks, headers):
            calls, sent = [], []

            async def downstream(scope, receive, send):
                calls.append(await receive())

            events = iter(chunks)

            async def receive():
                return next(events)

            async def send(message):
                sent.append(message)

            middleware = RequestBodyLimitMiddleware(downstream)
            await middleware({"type": "http", "method": "POST", "path": "/recommendations", "headers": headers}, receive, send)
            return calls, sent

        for headers in [[], [(b"content-length", b"1")], [(b"content-length", b"invalid")]]:
            calls, sent = asyncio.run(scenario([
                {"type": "http.request", "body": b"x" * MAX_REQUEST_BYTES, "more_body": True},
                {"type": "http.request", "body": b"x", "more_body": False},
            ], headers))
            self.assertEqual(calls, [])
            self.assertEqual(sent[0]["status"], 413)
        calls, sent = asyncio.run(scenario([{"type": "http.disconnect"}], []))
        self.assertEqual((calls, sent), ([], []))
        calls, sent = asyncio.run(scenario([
            {"type": "http.request", "body": b"a", "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": False},
        ], []))
        self.assertEqual(calls[0]["body"], b"ab")

    def test_limit_boundary_and_safe_integer_budget(self):
        raw = json.dumps(REQUEST).encode()
        result = self.client.post("/recommendations", content=raw + b" " * (MAX_REQUEST_BYTES - len(raw)),
                                  headers={"content-type": "application/json"})
        self.assertEqual(result.status_code, 200)
        for budget, status in [(9007199254740991, 200), (9007199254740992, 422), (10**100, 422)]:
            result = self.client.post("/recommendations", json={**REQUEST, "budget_kzt": budget})
            self.assertEqual(result.status_code, status)
            if status == 422:
                self.assertEqual(result.json()["error"]["details"][0]["field"], "budget_kzt")

    def test_malformed_json_and_invalid_fields_never_become_internal_errors(self):
        for body in [b"{", b"null", b"[]", b"true", b'"text"', b"\xff"]:
            result = self.client.post("/recommendations", content=body, headers={"content-type": "application/json"})
            self.assertIn(result.status_code, [400, 422])
            self.assertIn("error", result.json())
            self.assertIn("x-request-id", result.headers)
        for changes in [{"duration_hours": float("inf")}, {"budget_kzt": True}, {"date": "2026-02-30"},
                        {"date": "2026-09-22"}, {"date": "2027-01-01"}, {"__proto__": "private-value"}]:
            result = self.client.post("/recommendations", content=json.dumps({**REQUEST, **changes}),
                                      headers={"content-type": "application/json"})
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("private-value", result.text)
