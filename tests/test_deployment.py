import copy
import csv
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from contractor_match.catalogue import DATA_FILE, load_catalogue, read_catalogue
from contractor_match.config import CatalogueSettings, ConfigurationError, load_catalogue_settings
from contractor_match.doctor import run_checks
from contractor_match.ranking import catalog_index
from contractor_match.supabase_catalogue import CatalogueUnavailable, fetch_catalogue


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AI_PROVIDER": "local", "RANKING_PROVIDER": "tfidf",
                                     "CATALOGUE_PROVIDER": "csv", "CORS_ALLOWED_ORIGINS": ""})
        env.start()
        self.addCleanup(env.stop)
        load_catalogue.cache_clear()
        catalog_index.cache_clear()
        self.addCleanup(load_catalogue.cache_clear)
        self.addCleanup(catalog_index.cache_clear)
        self.original = read_catalogue(DATA_FILE)
        with DATA_FILE.open(encoding="utf-8-sig", newline="") as file:
            self.rows = list(csv.DictReader(file))
        for row in self.rows:
            for name in ("synthetic", "city_imputed", "price_imputed"):
                row[name] = row[name] == "True"
            row["price_from_kzt"] = int(row["price_from_kzt"])
            row["max_hours"] = float(row["max_hours"]) if row["max_hours"] else None
        self.settings = CatalogueSettings("supabase", "https://firebird-test.supabase.co", "PRIVATE-secret")

    def fetch(self, payload, status=200):
        def respond(request):
            self.assertEqual(request.headers["apikey"], "PRIVATE-secret")
            self.assertNotIn("authorization", request.headers)
            self.assertEqual(request.url.path, "/rest/v1/firebird_contractors")
            self.assertEqual(request.url.params["limit"], "67")
            return httpx.Response(status, json=payload)
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("contractor_match.supabase_catalogue.httpx.Client", return_value=client):
            return fetch_catalogue(self.settings, self.original)

    def test_database_preserves_every_profile_calendar_and_original_order(self):
        self.assertEqual(self.fetch(list(reversed(self.rows))), self.original)

    def test_database_rejects_partial_extra_duplicate_or_changed_rows(self):
        changed = copy.deepcopy(self.rows)
        changed[0]["busy_dates"] = "2026-09-23"
        bad_flag = copy.deepcopy(self.rows)
        bad_flag[0]["synthetic"] = "False"
        for rows in [[], self.rows[:-1], self.rows + [self.rows[0]],
                     self.rows[:-1] + [self.rows[0]], changed, bad_flag, {"error": "PRIVATE-payload"}]:
            with self.subTest(size=len(rows)), self.assertRaises(CatalogueUnavailable) as error:
                self.fetch(rows)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_database_http_failure_has_safe_error_without_fallback(self):
        for status in [401, 403, 429, 500]:
            with self.subTest(status=status), self.assertRaises(CatalogueUnavailable) as error:
                self.fetch({"message": "PRIVATE-payload"}, status)
            self.assertIsNone(error.exception.__cause__)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_failed_database_load_is_not_cached_and_api_recovers(self):
        from contractor_match.api import create_app
        with patch.dict(os.environ, {"CATALOGUE_PROVIDER": "supabase",
                                    "SUPABASE_URL": self.settings.url, "SUPABASE_SECRET_KEY": self.settings.key}), \
             patch("contractor_match.supabase_catalogue.fetch_catalogue", side_effect=[
                 CatalogueUnavailable("safe"), CatalogueUnavailable("safe"), self.original,
             ]) as fetch:
            with TestClient(create_app()) as client:
                failed = client.get("/health")
                self.assertEqual(failed.status_code, 503)
                self.assertEqual(failed.json()["error"]["code"], "catalogue_unavailable")
                self.assertRegex(failed.headers["x-request-id"], "^[a-f0-9]{32}$")
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/catalogue/options").status_code, 200)
            self.assertEqual(fetch.call_count, 3)

    def test_database_config_rejects_unsafe_urls_without_echoing_values(self):
        for value in ["http://firebird.supabase.co", "https://other.example",
                      "https://PRIVATE@firebird.supabase.co", "https://firebird.supabase.co?key=PRIVATE",
                      "https://firebird.supabase.co/rest/v1"]:
            with patch.dict(os.environ, {"CATALOGUE_PROVIDER": "supabase", "SUPABASE_URL": value,
                                        "SUPABASE_SECRET_KEY": "PRIVATE"}), self.assertRaises(ConfigurationError) as error:
                load_catalogue_settings()
            self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn("PRIVATE", repr(self.settings))

    def test_doctor_stays_offline_with_supabase_configured(self):
        with patch.dict(os.environ, {"CATALOGUE_PROVIDER": "supabase", "SUPABASE_URL": self.settings.url,
                                    "SUPABASE_SECRET_KEY": self.settings.key}), \
             patch("httpx.Client", side_effect=AssertionError("Network forbidden")):
            report = run_checks()
        self.assertEqual(report["status"], "ok")
        source = next(row for row in report["checks"] if row["name"] == "catalogue_source")
        self.assertEqual(source["provider"], "supabase")
        self.assertFalse(source["live_database_checked"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_fullstack_entrypoint_serves_frontend_api_and_no_repository_files(self):
        from app import app
        with TestClient(app) as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn('src="./app.js"', page.text)
            for route in ["/web/app.js", "/web/styles.css", "/integration/api-client.mjs", "/catalogue/options"]:
                self.assertEqual(client.get(route).status_code, 200, route)
            for route in ["/.env", "/data/contractors.csv", "/contractor_match/config.py", "/.git/config",
                          "/integration/examples/matched.json", "/web/%2e%2e/data/contractors.csv"]:
                self.assertEqual(client.get(route).status_code, 404, route)
            result = client.post("/recommendations", json={"city": "Алматы", "category": "Ведущий",
                "event_format": "корпоратив", "date": "2026-12-19", "budget_kzt": 1200000})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json()["cards"]), 1)


if __name__ == "__main__":
    unittest.main()
