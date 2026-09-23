from __future__ import annotations

import asyncio
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contractor_match.api import app
from contractor_match import catalogue as catalogue_module
from contractor_match.catalogue import load_catalogue
from contractor_match.explanations import generate_explanations
from contractor_match.models import RecommendationRequest
from contractor_match.ranking import evidence_options
from contractor_match.service import recommend


def request(**changes: object) -> RecommendationRequest:
    fields: dict[str, object] = {
        "city": "Алматы",
        "date": "2026-10-15",
        "event_format": "корпоратив",
        "category": "Ведущий",
        "budget_kzt": 1_200_000,
        "brief": "спокойный ведущий делового форума",
    }
    fields.update(changes)
    return RecommendationRequest(**fields)


class RecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.no_key = patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "NVIDIA_API_KEY": "", "AI_PROVIDER": "openai"},
        )
        self.no_key.start()
        self.addCleanup(self.no_key.stop)

    def test_catalogue_and_date_change(self) -> None:
        catalogue = load_catalogue()
        self.assertEqual(len(catalogue), 66)
        self.assertEqual(sum(p.synthetic for p in catalogue), 13)
        autumn = recommend(request())
        december = recommend(request(date="2026-12-19"))
        self.assertEqual(autumn.status, "matched")
        self.assertEqual(len(autumn.cards), 3)
        self.assertEqual(len(december.cards), 1)
        self.assertNotEqual(
            [card.id for card in autumn.cards], [card.id for card in december.cards]
        )
        by_id = {profile.id: profile for profile in catalogue}
        for day, response in ((request().date, autumn), (request(date="2026-12-19").date, december)):
            for card in response.cards:
                self.assertNotIn(day, by_id[card.id].busy_dates)
                self.assertIn(card.evidence_quote, by_id[card.id].description)
                self.assertEqual(card.synthetic, by_id[card.id].synthetic)
        self.assertIn("Меньше трёх", december.message)
        self.assertGreater(december.reasons["busy"], 0)

    def test_three_outcomes_and_reasons(self) -> None:
        rare = recommend(request(category="Флорист", budget_kzt=300_000))
        self.assertEqual(rare.status, "matched")
        self.assertEqual(len(rare.cards), 1)
        self.assertIn("Меньше трёх", rare.message)

        absent = recommend(request(city="Астана", category="Декоратор"))
        self.assertEqual(absent.status, "category_absent")
        self.assertEqual(absent.cards, [])
        self.assertIn("нет подрядчиков", absent.message)

        rejected = recommend(
            request(city="Астана", category="Флорист", budget_kzt=100_000)
        )
        self.assertEqual(rejected.status, "no_eligible")
        self.assertEqual(rejected.cards, [])
        self.assertEqual(rejected.reasons["busy"], 1)
        self.assertEqual(rejected.reasons["over_budget"], 1)

    def test_determinism_and_optional_filters(self) -> None:
        item = request()
        first = recommend(item)
        second = recommend(item)
        self.assertEqual(
            [card.id for card in first.cards], [card.id for card in second.cards]
        )
        self.assertEqual(first.ai_mode, "fallback")
        long_event = recommend(request(duration_hours=24))
        self.assertEqual(long_event.status, "no_eligible")
        self.assertGreater(long_event.reasons["too_short"], 0)
        english = recommend(request(language="английский"))
        self.assertTrue(english.cards)
        by_id = {profile.id: profile for profile in load_catalogue()}
        self.assertTrue(all("английский" in by_id[c.id].languages for c in english.cards))

    def test_venue_uses_same_calendar_and_duration(self) -> None:
        hall = request(category="Банкетный зал", budget_kzt=5_000_000)
        available = recommend(hall)
        self.assertEqual(available.status, "matched")
        self.assertEqual(len(available.cards), 3)
        self.assertTrue(
            all(hall.date not in p.busy_dates for p in load_catalogue() if p.id in {c.id for c in available.cards})
        )
        nine_hours = recommend(
            request(category="Банкетный зал", budget_kzt=5_000_000, duration_hours=9)
        )
        self.assertEqual(nine_hours.status, "no_eligible")
        self.assertGreater(nine_hours.reasons["too_short"], 0)

    def test_invalid_input(self) -> None:
        for changes in (
            {"date": "2027-01-01"},
            {"date": "2026-02-30"},
            {"budget_kzt": 0},
            {"duration_hours": -1},
            {"event_format": "фестиваль"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                request(**changes)

    def test_ai_quotes_are_checked_and_fallback_is_safe(self) -> None:
        item = request()
        profiles = list(load_catalogue()[:2])
        quotes = [evidence_options(profile, item)[0] for profile in profiles]
        items = [
            {"id": profile.id, "quote": quote}
            for profile, quote in zip(profiles, quotes)
        ]
        valid_response = Mock()
        valid_response.raise_for_status.return_value = None
        valid_response.json.return_value = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"items": items})}]}
            ]
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch(
            "contractor_match.explanations.httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=valid_response,
        ) as post:
            explained = generate_explanations(item, profiles)
        self.assertEqual(explained.mode, "openai")
        self.assertEqual(explained.quotes[profiles[0].id], quotes[0])
        self.assertEqual(post.call_count, 1)

        bad_response = Mock()
        bad_response.raise_for_status.return_value = None
        bad_response.json.return_value = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"items": [items[0], {"id": profiles[1].id, "quote": "выдуманный опыт"}]})}]}
            ]
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch(
            "contractor_match.explanations.httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=bad_response,
        ):
            explained = generate_explanations(item, profiles)
        self.assertEqual(explained.mode, "fallback")
        self.assertIn(explained.quotes[profiles[1].id], profiles[1].description)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch(
            "contractor_match.explanations.httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("slow"),
        ):
            self.assertEqual(generate_explanations(item, profiles).mode, "fallback")

    def test_nvidia_adapter_uses_one_call(self) -> None:
        item = request()
        profiles = list(load_catalogue()[:2])
        items = [
            {"id": profile.id, "quote": evidence_options(profile, item)[0]}
            for profile in profiles
        ]
        api_response = Mock()
        api_response.raise_for_status.return_value = None
        api_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"items": items})}}]
        }
        with patch.dict(
            os.environ, {"AI_PROVIDER": "nvidia", "NVIDIA_API_KEY": "test-key"}
        ), patch(
            "contractor_match.explanations.httpx.AsyncClient.post",
            new_callable=AsyncMock,
            return_value=api_response,
        ) as post:
            explained = generate_explanations(item, profiles)
        self.assertEqual(explained.mode, "nvidia")
        self.assertEqual(post.call_count, 1)
        self.assertIn("integrate.api.nvidia.com", post.call_args.args[0])

    def test_auto_provider_prefers_openai_and_has_total_deadline(self) -> None:
        item = request()
        profiles = list(load_catalogue()[:1])
        quote = evidence_options(profiles[0], item)[0]
        raw = json.dumps({"items": [{"id": profiles[0].id, "quote": quote}]})
        with patch.dict(os.environ, {
            "AI_PROVIDER": "auto", "OPENAI_API_KEY": "test-key", "NVIDIA_API_KEY": "test-key"
        }), patch(
            "contractor_match.explanations._call_openai",
            new_callable=AsyncMock,
            return_value=raw,
        ) as openai_call, patch(
            "contractor_match.explanations._call_nvidia", new_callable=AsyncMock
        ) as nvidia_call:
            result = generate_explanations(item, profiles)
        self.assertEqual(result.mode, "openai")
        openai_call.assert_awaited_once()
        nvidia_call.assert_not_awaited()

        async def slow_call(*args: object) -> str:
            await asyncio.sleep(0.1)
            return raw

        with patch.dict(os.environ, {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"}), patch(
            "contractor_match.explanations._call_openai", side_effect=slow_call
        ), patch("contractor_match.explanations.TIMEOUT_SECONDS", 0.01):
            result = generate_explanations(item, profiles)
        self.assertEqual(result.mode, "fallback")

    def test_api_contract(self) -> None:
        client = TestClient(app)
        response = client.post("/recommendations", json=request().model_dump(mode="json"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "matched")
        self.assertEqual(len(response.json()["cards"]), 3)
        wrong_date = request().model_dump(mode="json")
        wrong_date["date"] = "2027-01-01"
        date_error = client.post("/recommendations", json=wrong_date)
        self.assertEqual(date_error.status_code, 422)
        self.assertEqual(date_error.json()["error"]["code"], "invalid_request")
        self.assertEqual(date_error.json()["error"]["details"][0]["field"], "date")
        wrong_city = request(city="Алматы").model_dump(mode="json")
        wrong_city["city"] = "Караганда"
        city_error = client.post("/recommendations", json=wrong_city)
        self.assertEqual(city_error.status_code, 422)
        self.assertEqual(city_error.json()["error"]["code"], "invalid_request")
        self.assertEqual(city_error.json()["error"]["details"][0]["field"], "city")
        self.assertEqual(client.get("/health").json(), {"status": "ok"})

    def test_options_and_case_insensitive_input(self) -> None:
        client = TestClient(app)
        options = client.get("/catalogue/options")
        self.assertEqual(options.status_code, 200)
        data = options.json()
        self.assertIn("Алматы", data["cities"])
        self.assertIn("Ведущий", data["categories_by_city"]["Алматы"])
        self.assertEqual(data["calendar_start"], "2026-09-23")
        self.assertEqual(data["calendar_end"], "2026-12-31")

        canonical = request(language="русский")
        variant = request(
            city="  алматы  ",
            category="ведущий",
            event_format="  КОРПОРАТИВ  ",
            language="РУССКИЙ",
        )
        self.assertEqual(
            [card.id for card in recommend(canonical).cards],
            [card.id for card in recommend(variant).cards],
        )
        self.assertTrue(all(card.city == "Алматы" for card in recommend(variant).cards))
        self.assertTrue(all(card.category == "Ведущий" for card in recommend(variant).cards))

        unknown = canonical.model_dump(mode="json")
        unknown["category"] = "ведущеее"
        response = client.post("/recommendations", json=unknown)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Доступны:", response.json()["error"]["details"][0]["message"])

    def test_catalogue_validation_rejects_corrupted_profiles(self) -> None:
        with catalogue_module.DATA_FILE.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames
            rows = list(reader)
        self.assertIsNotNone(fieldnames)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contractors.csv"
            for field, value, expected in (
                ("price_from_kzt", "0", "price_from_kzt"),
                ("busy_dates", "2027-01-01", "busy_dates"),
                ("description", "", "description"),
            ):
                with self.subTest(field=field):
                    changed = [row.copy() for row in rows]
                    changed[0][field] = value
                    with path.open("w", encoding="utf-8", newline="") as target:
                        writer = csv.DictWriter(target, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(changed)
                    with patch.object(catalogue_module, "DATA_FILE", path):
                        load_catalogue.cache_clear()
                        with self.assertRaisesRegex(ValueError, expected):
                            load_catalogue()
                        load_catalogue.cache_clear()

    def test_invalid_catalogue_prevents_api_startup(self) -> None:
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
        with patch("contractor_match.api.load_catalogue", side_effect=ValueError("bad catalogue")):
            with self.assertRaisesRegex(ValueError, "bad catalogue"):
                with TestClient(app):
                    pass


if __name__ == "__main__":
    unittest.main()
