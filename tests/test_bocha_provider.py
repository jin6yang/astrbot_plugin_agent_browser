import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_agent_browser.models import ObscuraError, SearchConfig  # noqa: E402
from astrbot_plugin_agent_browser.search_providers.bocha import (  # noqa: E402
    BOCHA_ENDPOINTS,
    BochaProvider,
    check_bocha_error,
    parse_bocha_results,
)

RESPONSE_NESTED = {
    "code": 200,
    "msg": None,
    "data": {
        "webPages": {
            "totalEstimatedMatches": 1000,
            "value": [
                {
                    "name": "Example One",
                    "url": "https://example.com/one",
                    "snippet": "short snippet",
                    "summary": "Long summary of example one.",
                    "siteName": "Example",
                },
                {
                    "name": "Example Two",
                    "url": "https://example.org/two",
                    "snippet": "only snippet",
                    "summary": "",
                },
                {
                    "name": "Duplicate",
                    "url": "https://example.com/one",
                    "snippet": "dup",
                },
                {
                    "name": "",
                    "url": "https://example.com/no-title",
                    "snippet": "missing title",
                },
            ],
        }
    },
}

RESPONSE_TOP_LEVEL = {
    "code": "200",
    "webPages": {
        "value": [
            {"name": "Legacy", "url": "https://legacy.example.com", "snippet": "legacy"},
        ]
    },
}


class BochaParseTests(unittest.TestCase):
    def test_parse_nested_response(self):
        results = parse_bocha_results(RESPONSE_NESTED, limit=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Example One")
        self.assertEqual(results[0].url, "https://example.com/one")
        self.assertEqual(results[0].snippet, "Long summary of example one.")
        self.assertEqual(results[1].snippet, "only snippet")

    def test_parse_top_level_fallback(self):
        results = parse_bocha_results(RESPONSE_TOP_LEVEL, limit=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://legacy.example.com")

    def test_parse_limit(self):
        results = parse_bocha_results(RESPONSE_NESTED, limit=1)
        self.assertEqual(len(results), 1)

    def test_parse_malformed(self):
        self.assertEqual(parse_bocha_results({"code": 200}, limit=5), [])
        self.assertEqual(parse_bocha_results({"data": {"webPages": {"value": "nope"}}}, limit=5), [])

    def test_check_bocha_error(self):
        self.assertIsNone(check_bocha_error({"code": 200}))
        self.assertIsNone(check_bocha_error({"code": "200"}))
        self.assertIsNone(check_bocha_error({"data": {}}))
        self.assertEqual(check_bocha_error({"code": 403, "msg": "invalid key"}), "invalid key")
        self.assertEqual(check_bocha_error({"code": "500", "message": "boom"}), "boom")


class FakeBochaProvider(BochaProvider):
    def __init__(self, config, *, responses=None, errors=None):
        super().__init__(config)
        self._responses = responses or {}
        self._errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def _post(self, endpoint, body, api_key):
        import json as _json

        self.calls.append((endpoint, _json.loads(body.decode("utf-8"))))
        if endpoint in self._errors:
            raise self._errors[endpoint]
        return self._responses[endpoint]


class BochaProviderSearchTests(unittest.IsolatedAsyncioTestCase):
    def _config(self, api_key: str = "test-key") -> SearchConfig:
        return SearchConfig(search_engine="bocha_api", search_api_key=api_key, result_count=5)

    async def test_missing_api_key_raises(self):
        provider = BochaProvider(self._config(api_key=""))
        with self.assertRaises(ObscuraError):
            await provider.search("test")

    async def test_success_first_endpoint(self):
        provider = FakeBochaProvider(
            self._config(), responses={BOCHA_ENDPOINTS[0]: RESPONSE_NESTED}
        )
        response = await provider.search("test query")

        self.assertEqual(len(response.results), 2)
        self.assertEqual(len(provider.calls), 1)
        endpoint, payload = provider.calls[0]
        self.assertEqual(endpoint, BOCHA_ENDPOINTS[0])
        self.assertEqual(payload["query"], "test query")
        self.assertEqual(payload["count"], 5)
        self.assertTrue(payload["summary"])

    async def test_fallback_to_second_endpoint(self):
        provider = FakeBochaProvider(
            self._config(),
            responses={BOCHA_ENDPOINTS[1]: RESPONSE_NESTED},
            errors={BOCHA_ENDPOINTS[0]: ObscuraError("Bocha API HTTP 403: forbidden")},
        )
        response = await provider.search("test query")

        self.assertEqual(len(response.results), 2)
        self.assertEqual(len(provider.calls), 2)

    async def test_api_error_code_falls_back_then_raises(self):
        provider = FakeBochaProvider(
            self._config(),
            responses={
                BOCHA_ENDPOINTS[0]: {"code": 403, "msg": "invalid key"},
                BOCHA_ENDPOINTS[1]: {"code": 403, "msg": "invalid key"},
            },
        )

        with self.assertRaises(ObscuraError) as ctx:
            await provider.search("test query")
        self.assertIn("invalid key", str(ctx.exception))
        self.assertEqual(len(provider.calls), 2)

    async def test_all_endpoints_fail_raise(self):
        provider = FakeBochaProvider(
            self._config(),
            errors={
                BOCHA_ENDPOINTS[0]: ObscuraError("Bocha request failed: timeout"),
                BOCHA_ENDPOINTS[1]: ObscuraError("Bocha request failed: refused"),
            },
        )

        with self.assertRaises(ObscuraError) as ctx:
            await provider.search("test query")
        self.assertIn("refused", str(ctx.exception))

    async def test_empty_results_return_warning(self):
        provider = FakeBochaProvider(
            self._config(), responses={BOCHA_ENDPOINTS[0]: {"code": 200, "data": {}}}
        )
        response = await provider.search("test query")

        self.assertEqual(response.results, [])
        self.assertIn("没有解析到结果", response.warning)


if __name__ == "__main__":
    unittest.main()
