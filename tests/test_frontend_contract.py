import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from contractor_match.api import create_app
from contractor_match.config import ConfigurationError, load_cors_origins
from contractor_match.export_contract import build_artifacts
from test_hardening import request


class FrontendContractTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AI_PROVIDER": "local", "RANKING_PROVIDER": "tfidf",
                                    "CORS_ALLOWED_ORIGINS": "http://localhost:4317"})
        env.start()
        self.addCleanup(env.stop)

    def test_browser_preflight_and_actual_response(self):
        with TestClient(create_app()) as client:
            preflight = client.options('/recommendations', headers={
                'Origin': 'http://localhost:4317', 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'content-type',
            })
            response = client.post('/recommendations', json=request().model_dump(mode='json'),
                                   headers={'Origin': 'http://localhost:4317'})
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(response.status_code, 200)
        for item in (preflight, response):
            self.assertEqual(item.headers['access-control-allow-origin'], 'http://localhost:4317')
            self.assertNotIn('access-control-allow-credentials', item.headers)
            self.assertIn('Origin', item.headers['vary'])

    def test_unknown_origin_or_method_is_not_allowed(self):
        with TestClient(create_app()) as client:
            for origin, method in (('https://unlisted.example', 'POST'), ('http://localhost:4317', 'DELETE')):
                result = client.options('/recommendations', headers={
                    'Origin': origin, 'Access-Control-Request-Method': method,
                })
                self.assertEqual(result.status_code, 400)
            result = client.get('/health', headers={'Origin': 'https://unlisted.example'})
            self.assertNotIn('access-control-allow-origin', result.headers)

    def test_cors_configuration_normalizes_and_rejects_secrets_or_wildcard(self):
        with patch.dict(os.environ, {'CORS_ALLOWED_ORIGINS': ' https://EXAMPLE.org:443/,http://localhost:4317,https://example.org '}):
            self.assertEqual(load_cors_origins(), ['https://example.org', 'http://localhost:4317'])
        for value in ('*', 'null', 'https://example.org/path', 'https://secret@example.org',
                      'https://example.org?key=secret', 'https://example.org:bad'):
            with self.subTest(value=value), patch.dict(os.environ, {'CORS_ALLOWED_ORIGINS': value}):
                with self.assertRaises(ConfigurationError) as caught:
                    create_app()
                self.assertNotIn('secret', str(caught.exception))
        with patch.dict(os.environ, {'CORS_ALLOWED_ORIGINS': ''}):
            self.assertEqual(load_cors_origins(), [])

    def test_internal_error_is_safe_json_with_cors(self):
        with TestClient(create_app(), raise_server_exceptions=False) as client, patch(
            'contractor_match.api.recommend', side_effect=RuntimeError('SENSITIVE-TEST-DETAIL')
        ), self.assertLogs('contractor_match.api', level='ERROR') as logs:
            response = client.post('/recommendations', json=request().model_dump(mode='json'),
                                   headers={'Origin': 'http://localhost:4317'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error']['code'], 'internal_error')
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:4317')
        self.assertNotIn('SENSITIVE', response.text + str(logs.output))

    def test_validation_http_and_config_errors_share_envelope(self):
        with TestClient(create_app()) as client:
            responses = [client.get('/not-a-route'), client.get('/recommendations'),
                         client.post('/recommendations', json=request().model_dump(mode='json') | {'budget_kzt': '100'}),
                         client.post('/recommendations', content='{', headers={'Content-Type': 'application/json'})]
            with patch.dict(os.environ, {'AI_PROVIDER': 'invalid'}):
                responses.append(client.get('/health'))
        self.assertEqual([r.status_code for r in responses], [404, 405, 422, 422, 503])
        for response in responses:
            error = response.json()['error']
            self.assertTrue(error['code'])
            self.assertTrue(error['message'])
            self.assertIsInstance(error['details'], list)
        self.assertEqual(responses[2].json()['error']['details'][0]['field'], 'budget_kzt')

    def test_export_is_current_and_never_calls_external_models(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'openai', 'OPENAI_API_KEY': 'test-only'}), patch(
            'contractor_match.explanations._call_openai', new_callable=AsyncMock
        ) as provider, patch('contractor_match.nim._call_rerank', new_callable=AsyncMock) as nim:
            artifacts = build_artifacts()
        provider.assert_not_called()
        nim.assert_not_called()
        root = Path(__file__).resolve().parent.parent
        for name, expected in artifacts.items():
            with self.subTest(name=name):
                self.assertEqual((root / name).read_text(encoding='utf-8'), expected)


if __name__ == '__main__':
    unittest.main()
