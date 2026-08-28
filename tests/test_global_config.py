"""
Tests for global shade module configuration (issue: global shade module configuration).

Covers:
* shade.api_key = "sk_live_xxx" sets global key accessible across resource calls
* shade.environment = "sandbox" / "production" switches active environment
* Setting shade.api_key = None and calling resource raises AuthenticationError
* Concurrent threads modifying config do not bleed into each other
* Instance-level overrides take precedence over global settings
"""

from __future__ import annotations

import concurrent.futures
from unittest.mock import patch

import pytest

import shade
from shade import Gateway, ShadeClient, Environment, AuthenticationError
from shade.config import config


@pytest.fixture(autouse=True)
def _reset_global_config():
    config.reset()
    yield
    config.reset()


class TestGlobalConfigAssignments:
    def test_api_key_global_assignment(self):
        assert shade.api_key is None
        shade.api_key = "sk_live_12345"
        assert shade.api_key == "sk_live_12345"
        assert config.api_key == "sk_live_12345"

    def test_environment_string_assignment(self):
        assert shade.environment == Environment.SANDBOX
        shade.environment = "production"
        assert shade.environment == Environment.PRODUCTION
        assert config.environment == Environment.PRODUCTION

    def test_environment_enum_assignment(self):
        shade.environment = Environment.SANDBOX
        assert shade.environment == Environment.SANDBOX

    def test_invalid_environment_raises(self):
        with pytest.raises(ValueError, match="Invalid environment"):
            shade.environment = "invalid_env"


class TestAuthenticationErrorOnMissingKey:
    def test_none_api_key_raises_authentication_error_on_gateway_call(self):
        shade.api_key = None
        gateway = Gateway()

        with pytest.raises(AuthenticationError, match="No API key provided"):
            gateway.process_payment(100.0, "USD")

    def test_none_api_key_raises_authentication_error_on_client_request(self):
        shade.api_key = None
        client = ShadeClient()

        with pytest.raises(AuthenticationError, match="No API key provided"):
            client.request("GET", "/test")

    def test_setting_api_key_none_after_init_raises(self):
        shade.api_key = "sk_test_init"
        gateway = Gateway()

        shade.api_key = None

        with pytest.raises(AuthenticationError, match="No API key provided"):
            gateway.process_payment(50.0, "USD")


def _patch_httpx_and_capture(client_wrapper, json_response_body=None, status_code=200, headers=None):
    """Patch ``wrapper._client.request`` and return (patch_context, capture_list).

    ``capture_list`` will contain one dict per call with keys: ``url``, ``headers``,
    ``method``, ``params``, ``json``.
    """
    import httpx

    captured = []

    def fake_request(*args, **kwargs):
        captured.append(
            {
                "method": args[0] if args else kwargs.get("method", ""),
                "url": args[1] if len(args) > 1 else kwargs.get("url", ""),
                "headers": kwargs.get("headers", {}),
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
            }
        )
        body_kwargs: dict = {"text": ""}
        if json_response_body is not None:
            body_kwargs = {"json": json_response_body}
        return httpx.Response(status_code=status_code, headers=headers or {}, **body_kwargs)

    return patch.object(client_wrapper._client, "request", side_effect=fake_request), captured


class TestGlobalConfigResourceCalls:
    def test_gateway_uses_global_api_key_and_environment(self):
        shade.api_key = "sk_live_global"
        shade.environment = "production"

        gateway = Gateway()

        ctx, captured = _patch_httpx_and_capture(
            gateway._http, {"id": "pay_1", "status": "success"}
        )
        with ctx:
            res = gateway.process_payment(200.0, "USD")

        assert res == {"id": "pay_1", "status": "success"}
        assert len(captured) == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer sk_live_global"
        assert captured[0]["url"].startswith("https://api.shadeprotocol.io/v1")


class TestInstanceOverridesBeatsGlobalConfig:
    def test_instance_api_key_beats_global(self):
        shade.api_key = "sk_global"
        gateway = Gateway(api_key="sk_instance_override")

        ctx, captured = _patch_httpx_and_capture(gateway._http, {"ok": True})
        with ctx:
            gateway.process_payment(10.0, "USD")

        assert len(captured) == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer sk_instance_override"

    def test_instance_api_base_beats_global(self):
        shade.api_base = "https://global-base.example.com"
        gateway = Gateway(api_key="sk_test", api_base="https://override-base.example.com")

        ctx, captured = _patch_httpx_and_capture(gateway._http, {"ok": True})
        with ctx:
            gateway.process_payment(10.0, "USD")

        assert len(captured) == 1
        assert captured[0]["url"].startswith("https://override-base.example.com")

    def test_gateway_environment_override_propagates_to_subclients(self):
        shade.environment = "sandbox"
        gateway = Gateway(api_key="sk_test", environment="production")

        ctx, captured = _patch_httpx_and_capture(gateway._http, {"ok": True})
        with ctx:
            gateway.process_payment(10.0, "USD")

        assert len(captured) == 1
        assert captured[0]["url"].startswith(Environment.PRODUCTION.base_url)

    def test_gateway_setter_updates_propagate_to_subclients(self):
        gateway = Gateway(api_key="sk_initial", environment="sandbox")

        gateway.api_key = "sk_updated_setter"
        gateway.environment = "production"

        ctx, captured = _patch_httpx_and_capture(gateway._http, {"ok": True})
        with ctx:
            gateway.process_payment(10.0, "USD")

        assert len(captured) == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer sk_updated_setter"
        assert captured[0]["url"].startswith(Environment.PRODUCTION.base_url)





class TestThreadSafety:
    def test_concurrent_threads_do_not_bleed(self):
        shade.api_key = "sk_main_thread"

        results = {}

        def worker(thread_id: int, key: str):
            shade.api_key = key
            # Simulate work
            gateway = Gateway()
            # Inspect resolved key for this thread
            resolved_key = gateway.api_key
            results[thread_id] = resolved_key

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            f1 = executor.submit(worker, 1, "sk_thread_1")
            f2 = executor.submit(worker, 2, "sk_thread_2")
            f3 = executor.submit(worker, 3, "sk_thread_3")
            f4 = executor.submit(worker, 4, "sk_thread_4")
            concurrent.futures.wait([f1, f2, f3, f4])

        assert results[1] == "sk_thread_1"
        assert results[2] == "sk_thread_2"
        assert results[3] == "sk_thread_3"
        assert results[4] == "sk_thread_4"
        # Main thread key should remain untouched
        assert shade.api_key == "sk_main_thread"

    def test_reset_invalidates_thread_local_overrides_across_reused_workers(self):
        worker_results = {}

        def set_override(key: str):
            shade.api_key = key
            return shade.api_key

        def read_override():
            return shade.api_key

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            # Step 1: Set thread-local override in worker thread
            future_set = executor.submit(set_override, "sk_worker_override")
            assert future_set.result() == "sk_worker_override"

            # Step 2: Reset config from main thread (e.g. between tests)
            config.reset()

            # Step 3: Worker thread re-used; check that stale thread-local override is invalidated
            future_read = executor.submit(read_override)
            assert future_read.result() is None
