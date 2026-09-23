"""Real catalogue regression matrix; expected sets do not call eligibility.py."""
import os
import unittest
from unittest.mock import patch

from contractor_match.catalogue import load_catalogue
from contractor_match.models import FIRST_DATE, LAST_DATE, RecommendationRequest
from contractor_match.service import recommend


class CatalogueMatrixTests(unittest.TestCase):
    def test_every_profile_price_hours_calendar_quotes_and_replayed_alternatives(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "local", "RANKING_PROVIDER": "tfidf", "CATALOGUE_PROVIDER": "csv"}):
            catalogue = load_catalogue()
            by_id = {profile.id: profile for profile in catalogue}

            def expected(request):
                scope = [p for p in catalogue if p.city == request.city and request.category in p.categories]
                eligible = {p.id for p in scope if request.date not in p.busy_dates
                            and request.event_format in p.event_formats and p.price_from_kzt <= request.budget_kzt
                            and (request.language is None or request.language in p.languages)
                            and (request.duration_hours is None or p.max_hours is None or request.duration_hours <= p.max_hours)}
                return scope, eligible

            for profile in catalogue:
                for day, budget, hours, language in [
                    (FIRST_DATE, profile.price_from_kzt, profile.max_hours, None),
                    (LAST_DATE, max(1, profile.price_from_kzt - 1), None, profile.languages[0]),
                    (min(profile.busy_dates, default=FIRST_DATE), profile.price_from_kzt, None, None),
                    (LAST_DATE, 9007199254740991, (profile.max_hours or 100) + .25, profile.languages[-1]),
                ]:
                    request = RecommendationRequest(city=profile.city, category=profile.categories[0],
                        event_format=profile.event_formats[0], date=day, budget_kzt=budget,
                        duration_hours=hours, language=language)
                    with self.subTest(profile=profile.id, date=day, budget=budget, hours=hours):
                        result = recommend(request)
                        scope, eligible = expected(request)
                        self.assertEqual(result.counts.category_total, len(scope))
                        self.assertEqual(result.counts.eligible_total, len(eligible))
                        self.assertEqual(result.status, "matched" if eligible else "no_eligible")
                        self.assertEqual(len(result.cards), min(3, len(eligible)))
                        self.assertEqual(result.model_dump(), recommend(request).model_dump())
                        for card in result.cards:
                            self.assertIn(card.id, eligible)
                            source = by_id[card.id]
                            self.assertIn(card.evidence_quote, source.description)
                            self.assertEqual((card.synthetic, card.city_imputed, card.price_imputed),
                                             (source.synthetic, source.city_imputed, source.price_imputed))
                        for alternative in result.alternatives:
                            _, new_ids = expected(alternative.request)
                            self.assertEqual(set(alternative.candidate_ids), new_ids)
                            self.assertEqual(alternative.eligible_count, len(new_ids))
                            self.assertEqual(alternative.added_count, len(new_ids - eligible))
                            self.assertGreater(len(new_ids), len(eligible))
                            self.assertEqual(recommend(alternative.request).counts.eligible_total, len(new_ids))
