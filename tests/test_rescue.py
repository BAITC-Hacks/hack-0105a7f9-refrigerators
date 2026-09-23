import asyncio
import json
import os
import time
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient

from contractor_match.alternatives import suggest_alternatives
from contractor_match.api import app
from contractor_match.catalogue import load_catalogue
from contractor_match.config import ConfigurationError, load_ranking_settings, load_settings
from contractor_match.demo import run_demo
from contractor_match.eligibility import failures
from contractor_match.explanations import generate_explanations
from contractor_match.models import FIRST_DATE, LAST_DATE
from contractor_match.nim import _call_rerank, validate_scores
from contractor_match.preferences import understand
from contractor_match.ranking import evidence_options
from contractor_match.service import recommend
from test_hardening import fixture_catalogue, profile, request


NIM_ENV = {"AI_PROVIDER": "brev", "NIM_BASE_URL": "http://127.0.0.1:8001/v1",
           "NIM_MODEL": "test-chat", "NIM_API_KEY": "", "RANKING_PROVIDER": "brev",
           "NIM_RERANK_BASE_URL": "http://127.0.0.1:8002/v1",
           "NIM_RERANK_MODEL": "test-rerank", "NIM_RERANK_API_KEY": ""}


class RescueTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AI_PROVIDER": "local", "RANKING_PROVIDER": "tfidf",
                                    "OPENAI_API_KEY": "", "NVIDIA_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_minimal_single_changes_and_replay(self):
        item = request(budget_kzt=100, duration_hours=6, language="русский")
        profiles = [profile("busy", busy_dates=frozenset({item.date})),
                    profile("expensive", price_from_kzt=101), profile("short", max_hours=5),
                    profile("still-wrong", price_from_kzt=102, languages=("казахский",))]
        options, note = suggest_alternatives(item, profiles, [])
        self.assertEqual([o.changes[0].field for o in options], ["date", "budget_kzt", "duration_hours"])
        self.assertEqual([o.changes[0].to_value for o in options], ["2026-10-16", 101, 5.0])
        self.assertIn("не применяются автоматически", note)
        for option in options:
            valid = [p for p in profiles if not failures(p, option.request)]
            self.assertEqual(option.candidate_ids, sorted(p.id for p in valid))
            self.assertEqual(option.eligible_count, len(valid))
            with fixture_catalogue(profiles):
                self.assertEqual(recommend(option.request).counts.eligible_total, len(valid))
        self.assertEqual(item.date, date(2026, 10, 15))

    def test_combined_change_never_presents_budget_as_fix_for_busy(self):
        item = request(budget_kzt=100)
        bad = profile(busy_dates=frozenset({item.date}), price_from_kzt=200)
        options, _ = suggest_alternatives(item, [bad], [])
        self.assertEqual(len(options), 1)
        self.assertEqual({c.field for c in options[0].changes}, {"date", "budget_kzt"})
        self.assertEqual(options[0].eligible_count, 1)
        self.assertTrue(failures(bad, item.model_copy(update={"budget_kzt": 200})))
        self.assertFalse(failures(bad, options[0].request))

    def test_three_changes_null_and_unfixable_conditions(self):
        item = request(budget_kzt=100, duration_hours=8)
        bad = profile(busy_dates=frozenset({item.date}), price_from_kzt=200, max_hours=4)
        options, _ = suggest_alternatives(item, [bad], [])
        self.assertEqual(len(options[0].changes), 3)
        for candidate in (profile(event_formats=("свадьба",)), profile(city="Астана"),
                          profile(categories=("Флорист",)), profile(languages=("казахский",))):
            options, _ = suggest_alternatives(item.model_copy(update={"language": "русский"}), [candidate], [])
            self.assertEqual(options, [])
        unrestricted = profile(max_hours=None)
        options, _ = suggest_alternatives(item, [unrestricted], [unrestricted])
        self.assertEqual(options, [])

    def test_calendar_boundaries_and_all_dates_busy(self):
        for day, expected in ((FIRST_DATE, FIRST_DATE + timedelta(days=1)), (LAST_DATE, LAST_DATE - timedelta(days=1))):
            item = request(date=day)
            options, _ = suggest_alternatives(item, [profile(busy_dates=frozenset({day}))], [])
            self.assertEqual(options[0].request.date, expected)
        all_days = frozenset(FIRST_DATE + timedelta(days=i) for i in range((LAST_DATE - FIRST_DATE).days + 1))
        options, _ = suggest_alternatives(request(), [profile(busy_dates=all_days)], [])
        self.assertEqual(options, [])

    def test_short_results_only_offer_larger_counts_and_no_model_calls(self):
        current = profile("current")
        bad = profile("bad", busy_dates=frozenset({date(2026, 10, 15)}))
        with fixture_catalogue([current, bad]), patch("contractor_match.nim._call_rerank", new_callable=AsyncMock) as nim:
            result = recommend(request())
        self.assertEqual(len(result.cards), 1)
        self.assertEqual(result.alternatives[0].eligible_count, 2)
        self.assertEqual(result.alternatives[0].added_count, 1)
        nim.assert_not_called()
        self.assertEqual(suggest_alternatives(request(), [current] * 3, [current] * 3)[0], [])

    def test_real_story_and_every_alternative_is_replayable(self):
        report = run_demo(story=True)
        results = [row["response"] for row in report["scenarios"]]
        self.assertEqual([r["counts"]["eligible_total"] for r in results], [6, 1, 3, 0, 1])
        self.assertEqual(results[1]["alternatives"][0]["request"]["date"], "2026-12-17")
        self.assertEqual(results[3]["alternatives"][0]["request"]["budget_kzt"], 300000)
        catalogue = load_catalogue()
        for row in report["scenarios"]:
            from contractor_match.models import RecommendationRequest
            for option in row["response"]["alternatives"]:
                item = RecommendationRequest(**option["request"])
                valid = [p.id for p in catalogue if not failures(p, item)]
                self.assertEqual(sorted(valid), option["candidate_ids"])

    def test_brief_interpretation_language_filter_and_conflict(self):
        item, info = understand(request(brief="Нужен спокойный ведущий, без конкурсов, на русском"))
        self.assertEqual(info.styles, ["спокойный"])
        self.assertEqual(info.unwanted, ["без конкурсов"])
        self.assertEqual((item.language, info.language_source), ("русский", "brief"))
        profiles = [profile("ru"), profile("kk", languages=("казахский",))]
        with fixture_catalogue(profiles):
            result = recommend(request(brief="Нужен спокойный ведущий, без конкурсов, на русском"))
        self.assertEqual([c.id for c in result.cards], ["ru"])
        self.assertTrue(any("спокойный" in text and "уточнить" in text for text in result.cards[0].to_clarify))
        item, info = understand(request(language="казахский", brief="на русском"))
        self.assertEqual(item.language, "казахский")
        self.assertIn("отличается", info.notes[0])
        for text in ("не на русском", "на русском и казахском", "на русском или на английском"):
            item, info = understand(request(brief=text))
            self.assertIsNone(item.language)

    def test_comparisons_use_only_visible_facts_and_do_not_claim_absence(self):
        profiles = [profile("A", price_from_kzt=100), profile("B", price_from_kzt=150)]
        with fixture_catalogue(profiles):
            result = recommend(request())
        self.assertIn("Самая низкая", result.cards[0].differences[0])
        self.assertIn("50 ₸", result.cards[1].differences[0])
        self.assertTrue(all("Итоговую смету" in c.to_clarify[0] for c in result.cards))
        with TestClient(app) as client:
            response = client.post("/recommendations", json=request(date="2026-12-19").model_dump(mode="json"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("alternatives", response.json())
        self.assertIn("why_fits", response.json()["cards"][0])

    def test_nim_is_opt_in_and_configuration_is_safe(self):
        with patch.dict(os.environ, NIM_ENV):
            self.assertEqual(load_settings().provider, "brev")
            self.assertEqual(load_ranking_settings().provider, "brev")
            for value in ("", "http://remote.example/v1", "https://secret@server/v1", "https://server/v1?key=secret", "https://server:bad/v1"):
                with self.subTest(value=value), patch.dict(os.environ, {"NIM_BASE_URL": value}):
                    with self.assertRaises(ConfigurationError) as error:
                        load_settings()
                    self.assertNotIn("secret", str(error.exception))
        with patch.dict(os.environ, {"RANKING_PROVIDER": "oops"}):
            with self.assertRaises(ConfigurationError):
                load_settings()

    def test_reranker_receives_only_eligible_profiles_and_stable_ties(self):
        profiles = [profile("C", price_from_kzt=200), profile("B"), profile("A"),
                    profile("busy", busy_dates=frozenset({date(2026, 10, 15)}))]
        raw = {"rankings": [{"index": i, "logit": 1.0} for i in (2, 0, 1)]}
        with patch.dict(os.environ, NIM_ENV | {"AI_PROVIDER": "local"}), fixture_catalogue(profiles), patch(
            "contractor_match.nim._call_rerank", new_callable=AsyncMock, return_value=raw
        ) as call:
            first, second = recommend(request()), recommend(request())
        self.assertEqual([p.id for p in call.call_args.args[2]], ["A", "B", "C"])
        self.assertEqual([c.id for c in first.cards], ["A", "B", "C"])
        self.assertEqual([c.id for c in first.cards], [c.id for c in second.cards])
        self.assertEqual(first.ranking_mode, "brev")

    def test_reranker_rejects_incomplete_duplicate_nonfinite_and_foreign_indices(self):
        for raw in ({}, {"rankings": []}, {"rankings": [{"index": 0, "logit": True}]},
                    {"rankings": [{"index": 0, "logit": float("nan")}]},
                    {"rankings": [{"index": 5, "logit": 1}]},
                    {"rankings": [{"index": 0, "logit": 1}] * 2}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_scores(raw, 1)
        with patch.dict(os.environ, NIM_ENV | {"AI_PROVIDER": "local"}), patch(
            "contractor_match.nim._call_rerank", new_callable=AsyncMock, return_value={}
        ):
            result = recommend(request())
        self.assertEqual((result.ranking_mode, result.ranking_reason), ("tfidf", "invalid_response"))

    def test_nim_transport_schema_and_grounded_chat_output(self):
        item = request()
        candidate = profile()
        response = Mock()
        response.raise_for_status.return_value = None
        raw = json.dumps({"items": [{"id": candidate.id, "quote": evidence_options(candidate, item)[0]}]})
        response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": raw}}]}
        with patch.dict(os.environ, NIM_ENV | {"RANKING_PROVIDER": "tfidf", "NIM_API_KEY": "test-workload-token"}), patch(
            "contractor_match.explanations.httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response
        ) as post:
            result = generate_explanations(item, [candidate])
        self.assertEqual(result.mode, "brev")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8001/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "test-chat")
        self.assertNotIn("test-workload-token", json.dumps(post.call_args.kwargs["json"]))
        response.json.return_value = {"rankings": [{"index": 0, "logit": 1.0}]}
        with patch.dict(os.environ, NIM_ENV), patch(
            "contractor_match.nim.httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response
        ) as post:
            raw = asyncio.run(_call_rerank(load_ranking_settings(), item, [candidate], 1))
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8002/v1/ranking")
        self.assertEqual(post.call_args.kwargs["json"]["passages"], [{"text": candidate.description}])
        self.assertEqual(validate_scores(raw, 1), {0: 1.0})

    def test_shared_external_deadline_cancels_both_stages(self):
        async def slow_ranking(settings, item, profiles, timeout):
            await asyncio.sleep(.06)
            return {"rankings": [{"index": i, "logit": float(i)} for i in range(len(profiles))]}

        cancelled = []
        async def slow_chat(*args):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)

        with patch.dict(os.environ, NIM_ENV), patch("contractor_match.service.EXTERNAL_BUDGET_SECONDS", .12), patch(
            "contractor_match.nim._call_rerank", side_effect=slow_ranking
        ), patch("contractor_match.explanations._call_brev", side_effect=slow_chat):
            start = time.monotonic()
            result = recommend(request())
            elapsed = time.monotonic() - start
        self.assertEqual(result.ranking_mode, "brev")
        self.assertEqual(result.ai_reason, "timeout")
        self.assertEqual(cancelled, [True])
        self.assertLess(elapsed, .35)

    def test_empty_results_never_call_nim_and_errors_fall_back(self):
        with patch.dict(os.environ, NIM_ENV), patch("contractor_match.nim._call_rerank", new_callable=AsyncMock) as rank, patch(
            "contractor_match.explanations._call_brev", new_callable=AsyncMock
        ) as chat:
            result = recommend(request(city="Астана", category="Флорист", budget_kzt=100000))
        self.assertEqual(result.status, "no_eligible")
        rank.assert_not_called()
        chat.assert_not_called()
        for error, expected in ((httpx.ConnectError("private details"), "network_error"),
                                (httpx.TimeoutException("private details"), "timeout")):
            with patch.dict(os.environ, NIM_ENV | {"AI_PROVIDER": "local"}), patch(
                "contractor_match.nim._call_rerank", new_callable=AsyncMock, side_effect=error
            ):
                result = recommend(request())
            self.assertEqual(result.ranking_reason, expected)


if __name__ == "__main__":
    unittest.main()
