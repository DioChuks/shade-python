"""
Tests for the internal ``_SyncHTTPClient`` wrapper (issue #15).

Covers:
* URL construction for GET, POST, PATCH, DELETE
* Default headers: User-Agent, Accept, Content-Type, Authorization
* Query parameters and JSON request bodies
* Response parsing and error handling
* Single shared httpx.Client per ShadeClient lifetime
* Closing ShadeClient closes the underlying client
* Resources delegate through the shared wrapper
"""
from __future__ import annotations

from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import httpx
import pytest

import shade
from shade import BaseResource, Gateway, ShadeClient
from shade.client import API_KEY_ENV_VAR, ENVIRONMENT_ENV_VAR, reset_default_client
from shade.config import Environment
from shade.config import config as _config
from shade.errors import (
    AuthenticationError,
    HTTPError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ShadeError,
)
from shade.http_client import _SyncHTTPClient, _build_full_url


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(ENVIRONMENT_ENV_VAR, raising=False)
    _config.reset()
    reset_default_client()
    yield
    _config.reset()
    reset_default_client()


# ---------------------------------------------------------------------------
# URL construction helper
# ---------------------------------------------------------------------------


class TestBuildFullUrl:
    @pytest.mark.parametrize(
        "base, path, expected",
        [
            ("https://api.example.com", "/users", "https://api.example.com/users"),
            ("https://api.example.com/", "users", "https://api.example.com/users"),
            ("https://api.example.com/", "/users", "https://api.example.com/users"),
            ("https://api.example.com", "users", "https://api.example.com/users"),
            (
                "https://api.example.com/v1",
                "/payments/pay_1",
                "https://api.example.com/v1/payments/pay_1",
            ),
            (
                "https://api.example.com/v1/",
                "payments/pay_1",
                "https://api.example.com/v1/payments/pay_1",
            ),
        ],
    )
    def test_combines_base_and_path_without_double_slashes(
        self, base: str, path: str, expected: str
    ):
        assert _build_full_url(base, path) == expected


# ---------------------------------------------------------------------------
# Helpers for intercepting httpx calls
# ---------------------------------------------------------------------------


def _stub_httpx_client(
    http_wrapper: _SyncHTTPClient,
    responses: List[httpx.Response],
) -> List[dict]:
    """Replace the underlying ``httpx.Client.request`` and capture calls.

    Returns a list that will be populated with the kwargs of every call.
    """
    captured: List[dict] = []
    response_iter = iter(responses)

    def fake_request(*args, **kwargs):
        captured.append({"args": args, **kwargs})
        return next(response_iter)

    http_wrapper._client.request = fake_request  # type: ignore[method-assign]
    return captured


def _resp(
    status: int = 200,
    *,
    json_body: Any = None,
    text: Optional[str] = None,
    headers: Optional[dict] = None,
    request: Optional[httpx.Request] = None,
) -> httpx.Response:
    kwargs: dict[str, Any] = {"status_code": status, "headers": headers or {}}
    if json_body is not None:
        kwargs["json"] = json_body
    elif text is not None:
        kwargs["text"] = text
    if request is not None:
        kwargs["request"] = request
    return httpx.Response(**kwargs)


def _make_client(**overrides) -> _SyncHTTPClient:
    kwargs = {
        "api_key": "sk_test_xxx",
        "api_base": "https://api.example.com",
        "timeout": 5.0,
        **overrides,
    }
    return _SyncHTTPClient(**kwargs)


# ---------------------------------------------------------------------------
# URL construction for each HTTP verb
# ---------------------------------------------------------------------------


class TestUrlConstruction:
    def test_get_builds_correct_url(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/resources")

        assert captured[0]["args"] == ("GET", "https://api.example.com/resources")

    def test_post_builds_correct_url(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.post("/resources", json={"name": "x"})

        assert captured[0]["args"] == ("POST", "https://api.example.com/resources")

    def test_patch_builds_correct_url(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.patch("/resources/1", json={"name": "y"})

        assert captured[0]["args"] == ("PATCH", "https://api.example.com/resources/1")

    def test_delete_builds_correct_url(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.delete("/resources/1")

        assert captured[0]["args"] == ("DELETE", "https://api.example.com/resources/1")

    def test_handles_trailing_slash_on_api_base(self):
        client = _make_client(api_base="https://api.example.com/")
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("users")

        assert captured[0]["args"] == ("GET", "https://api.example.com/users")


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_authorization_bearer_api_key(self):
        client = _make_client(api_key="sk_live_secret")
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x")

        assert captured[0]["headers"]["Authorization"] == "Bearer sk_live_secret"

    def test_accept_application_json(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x")

        assert captured[0]["headers"]["Accept"] == "application/json"

    def test_user_agent_includes_version(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x")

        expected = f"shade-python/{shade.__version__}"
        assert captured[0]["headers"]["User-Agent"] == expected

    def test_content_type_json_on_post_with_body(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.post("/x", json={"k": "v"})

        assert captured[0]["headers"]["Content-Type"] == "application/json"

    def test_content_type_json_on_patch_with_body(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.patch("/x", json={"k": "v"})

        assert captured[0]["headers"]["Content-Type"] == "application/json"

    def test_no_content_type_on_get(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x")

        assert "Content-Type" not in captured[0]["headers"]

    def test_no_content_type_on_delete_without_body(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.delete("/x")

        assert "Content-Type" not in captured[0]["headers"]

    def test_merge_headers_case_insensitive_replacement(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x", headers={"authorization": "Bearer custom_token"})

        assert "Authorization" not in captured[0]["headers"]
        assert captured[0]["headers"]["authorization"] == "Bearer custom_token"

    def test_merge_headers_case_insensitive_none_removal(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x", headers={"authorization": None})

        assert "Authorization" not in captured[0]["headers"]
        assert "authorization" not in captured[0]["headers"]


class TestCleartextHttp:
    def test_rejects_non_local_cleartext_http(self):
        client = _make_client(api_base="http://api.shadeprotocol.io")
        with pytest.raises(ValueError, match="HTTPS is required"):
            client.get("/x")

    def test_rejects_local_cleartext_http_when_authorization_not_withheld(self):
        client = _make_client(api_base="http://localhost:8000")
        with pytest.raises(ValueError, match="Cleartext HTTP API bases are not allowed"):
            client.get("/x")

    def test_allows_local_cleartext_http_when_authorization_withheld(self):
        client = _make_client(api_base="http://localhost:8000")
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x", headers={"Authorization": None})

        assert "Authorization" not in captured[0]["headers"]
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Request parameters
# ---------------------------------------------------------------------------


class TestRequestParameters:
    def test_query_params_are_forwarded(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x", params={"limit": 10, "status": "paid"})

        assert captured[0]["params"] == {"limit": 10, "status": "paid"}

    def test_json_body_is_forwarded(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])
        body = {"amount": 10.0, "currency": "USD"}

        client.post("/x", json=body)

        assert captured[0]["json"] == body

    def test_timeout_forwarded(self):
        client = _make_client(timeout=7.5)
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x")

        assert captured[0]["timeout"] == 7.5


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_200_returns_parsed_dict(self):
        client = _make_client()
        _stub_httpx_client(
            client, [_resp(200, json_body={"id": "res_1", "status": "ok"})]
        )

        result = client.get("/res/res_1")

        assert result == {"id": "res_1", "status": "ok"}

    def test_empty_body_returns_empty_dict(self):
        client = _make_client()
        _stub_httpx_client(client, [_resp(204, text="")])

        result = client.delete("/res/1")

        assert result == {}

    def test_non_dict_2xx_raises_shade_error(self):
        client = _make_client()
        _stub_httpx_client(client, [_resp(200, json_body=[1, 2, 3])])

        with pytest.raises(ShadeError, match="Invalid response from API"):
            client.get("/x")

    def test_non_json_2xx_raises_shade_error(self):
        client = _make_client()
        _stub_httpx_client(client, [_resp(200, text="not json")])

        with pytest.raises(ShadeError, match="Invalid response from API"):
            client.get("/x")


# ---------------------------------------------------------------------------
# Error mapping (consistent with existing conventions)
# ---------------------------------------------------------------------------


class TestErrorResponses:
    def test_401_maps_to_authentication_error(self):
        client = _make_client()
        _stub_httpx_client(
            client,
            [_resp(401, json_body={"error": {"message": "bad token"}})],
        )

        with pytest.raises(AuthenticationError) as exc:
            client.get("/x")
        assert exc.value.status_code == 401

    def test_400_maps_to_invalid_request_error(self):
        client = _make_client()
        _stub_httpx_client(
            client,
            [_resp(400, json_body={"error": {"message": "bad input"}})],
        )

        with pytest.raises(InvalidRequestError) as exc:
            client.post("/x", json={})
        assert exc.value.status_code == 400

    def test_404_maps_to_not_found_error(self):
        client = _make_client()
        _stub_httpx_client(
            client,
            [_resp(404, json_body={"error": {"message": "gone"}})],
        )

        with pytest.raises(NotFoundError) as exc:
            client.get("/missing")
        assert exc.value.status_code == 404

    def test_429_maps_to_rate_limit_error(self):
        client = _make_client(max_retries=0)
        _stub_httpx_client(
            client,
            [
                _resp(
                    429,
                    json_body={"error": {"message": "slow down"}},
                    headers={"Retry-After": "5"},
                )
            ],
        )

        with pytest.raises(RateLimitError) as exc:
            client.get("/x")
        assert exc.value.status_code == 429
        assert exc.value.retry_after == 5

    def test_5xx_maps_to_network_error_when_retries_exhausted(self):
        client = _make_client(max_retries=0)
        _stub_httpx_client(
            client,
            [_resp(502, json_body={"error": {"message": "upstream"}})],
        )

        with pytest.raises(NetworkError) as exc:
            client.get("/x")
        assert exc.value.status_code == 502

    def test_other_non_2xx_maps_to_http_error(self):
        client = _make_client()
        _stub_httpx_client(
            client,
            [_resp(418, json_body={"error": {"message": "teapot"}})],
        )

        with pytest.raises(HTTPError) as exc:
            client.get("/x")
        assert exc.value.status_code == 418


# ---------------------------------------------------------------------------
# Client lifecycle — single shared httpx.Client
# ---------------------------------------------------------------------------


class TestClientLifecycle:
    def test_single_httpx_client_instance_reused_across_requests(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(200, json_body={"a": 1}),
                _resp(200, json_body={"b": 2}),
                _resp(200, json_body={"c": 3}),
            ],
        )

        client.get("/one")
        client.post("/two", json={"k": "v"})
        client.delete("/three")

        # Three requests captured — confirming the same wrapper drove all three
        assert len(captured) == 3

    def test_shade_client_has_one_sync_http_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")

        assert isinstance(sc._http, _SyncHTTPClient)

    def test_resources_share_the_same_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")

        class Res(BaseResource):
            def fetch(self):
                return self._request("GET", "/x")

        a = Res(client=sc)
        b = Res(client=sc)

        assert a.client._http is b.client._http
        assert a.client._http is sc._http

    def test_close_closes_underlying_httpx_client(self):
        client = _make_client()
        underlying = client._client

        with patch.object(underlying, "close", wraps=underlying.close) as mock_close:
            client.close()
            mock_close.assert_called_once()

    def test_shade_client_close_closes_sync_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        wrapper = sc._http

        with patch.object(wrapper, "close", wraps=wrapper.close) as mock_close:
            sc.close()
            mock_close.assert_called_once()

    def test_context_manager_closes_wrapper(self):
        wrapper_close_calls = []
        sc = ShadeClient(api_key="sk_test_xxx")

        orig_close = sc._http.close
        sc._http.close = lambda: wrapper_close_calls.append(True)  # type: ignore[method-assign]

        with sc:
            pass

        assert len(wrapper_close_calls) == 1

    def test_no_per_request_httpx_client_construction(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        wrapper = sc._http
        original_client = wrapper._client

        _stub_httpx_client(
            wrapper,
            [
                _resp(200, json_body={}),
                _resp(200, json_body={}),
            ],
        )

        wrapper.get("/a")
        wrapper.post("/b", json={})

        # The same object reference — a new one was not created per call
        assert wrapper._client is original_client


# ---------------------------------------------------------------------------
# Integration via ShadeClient and resources
# ---------------------------------------------------------------------------


class _TestResource(BaseResource):
    def list(self, limit: int = 5):
        return self._request("GET", f"/things?limit={limit}")

    def create(self, data: dict):
        return self._request("POST", "/things", data)

    def update(self, thing_id: str, data: dict):
        return self._request("PATCH", f"/things/{thing_id}", data)

    def remove(self, thing_id: str):
        return self._request("DELETE", f"/things/{thing_id}")


class TestResourceIntegration:
    def test_resource_get_routes_through_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        captured = _stub_httpx_client(sc._http, [_resp(200, json_body={"data": []})])
        res = _TestResource(client=sc)

        res.list(limit=3)

        assert captured[0]["args"][0] == "GET"
        assert "/things" in captured[0]["args"][1]

    def test_resource_post_routes_through_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        captured = _stub_httpx_client(sc._http, [_resp(201, json_body={"id": "t1"})])
        res = _TestResource(client=sc)

        res.create({"name": "widget"})

        assert captured[0]["args"][0] == "POST"
        assert captured[0]["json"] == {"name": "widget"}

    def test_resource_patch_routes_through_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        captured = _stub_httpx_client(sc._http, [_resp(200, json_body={"id": "t1"})])
        res = _TestResource(client=sc)

        res.update("t1", {"name": "gizmo"})

        assert captured[0]["args"][0] == "PATCH"
        assert captured[0]["json"] == {"name": "gizmo"}

    def test_resource_delete_routes_through_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        captured = _stub_httpx_client(sc._http, [_resp(204, text="")])
        res = _TestResource(client=sc)

        res.remove("t1")

        assert captured[0]["args"][0] == "DELETE"
        assert captured[0]["args"][1].endswith("/things/t1")

    def test_gateway_process_payment_uses_post(self):
        gw = Gateway(api_key="sk_test_xxx")
        captured = _stub_httpx_client(gw._http, [_resp(200, json_body={"id": "p1"})])

        gw.process_payment(9.99, "USD")

        assert captured[0]["args"] == ("POST", "https://testnet.api.shadeprotocol.io/v1/payments")
        assert captured[0]["json"] == {"amount": 9.99, "currency": "USD"}


# ---------------------------------------------------------------------------
# Public API boundary — httpx types must not leak
# ---------------------------------------------------------------------------


class TestPublicApiBoundary:
    def test_sync_http_client_is_not_exported_from_package(self):
        public_api = getattr(shade, "__all__", [])
        assert "_SyncHTTPClient" not in public_api

    def test_wrapper_request_returns_dict_not_httpx_response(self):
        client = _make_client()
        _stub_httpx_client(client, [_resp(200, json_body={"ok": True})])

        result = client.get("/x")

        assert isinstance(result, dict)
        assert not isinstance(result, httpx.Response)

    def test_resource_request_returns_dict(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        _stub_httpx_client(sc._http, [_resp(200, json_body={"ok": True})])

        result = _TestResource(client=sc).list()

        assert isinstance(result, dict)
        assert not isinstance(result, httpx.Response)

    def test_gateway_process_payment_returns_dict(self):
        gw = Gateway(api_key="sk_test_xxx")
        _stub_httpx_client(gw._http, [_resp(200, json_body={"id": "p1"})])

        result = gw.process_payment(1.0, "USD")

        assert isinstance(result, dict)
        assert not isinstance(result, httpx.Response)


# ---------------------------------------------------------------------------
# Idempotency-safe retry behaviour
# ---------------------------------------------------------------------------


def _call_counting_responses(
    client: _SyncHTTPClient,
    responses: List[httpx.Response],
    method: str = "GET",
) -> int:
    """Drive responses through the wrapper and return the number of calls made.

    The last response in the list is expected to be a 2xx (or equivalent
    success) so ``request()`` returns; otherwise the count is the number
    of attempts before raising.
    """
    captured = _stub_httpx_client(client, list(responses))
    has_request = getattr(responses[0], "_request", None) is not None
    req_method = responses[0].request.method if has_request else method
    try:
        client.request(
            req_method,
            "/any",
            json={},
        )
    except ShadeError:
        pass
    return len(captured)


class TestIdempotencySafeRetry:
    def test_get_5xx_is_retried(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"ok": True}),
            ],
        )

        result = client.get("/x")

        assert result == {"ok": True}
        assert len(captured) == 2

    def test_post_5xx_is_not_retried(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"should": "never-reach-this"}),
            ],
        )

        with pytest.raises(NetworkError):
            client.post("/payments", json={"amount": 10})

        assert len(captured) == 1

    def test_patch_5xx_is_not_retried(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(503, json_body={"error": {"message": "unavailable"}}),
                _resp(200, json_body={"should": "never-reach-this"}),
            ],
        )

        with pytest.raises(NetworkError):
            client.patch("/x", json={"a": 1})

        assert len(captured) == 1

    def test_delete_5xx_is_retried(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(504, json_body={"error": {"message": "timeout"}}),
                _resp(204, text=""),
            ],
        )

        result = client.delete("/x")

        assert result == {}
        assert len(captured) == 2

    def test_post_5xx_is_retried_when_idempotency_key_present(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"id": "p1"}),
            ],
        )

        result = client.post(
            "/payments",
            json={"amount": 10},
            headers={"Idempotency-Key": "unique-key-abc"},
        )

        assert result == {"id": "p1"}
        assert len(captured) == 2

    def test_post_429_is_always_retried(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(
                    429,
                    json_body={"error": {"message": "slow"}},
                    headers={"Retry-After": "1"},
                ),
                _resp(200, json_body={"id": "p1"}),
            ],
        )

        with patch("time.sleep"):
            result = client.post("/payments", json={"amount": 10})

        assert result == {"id": "p1"}
        assert len(captured) == 2

    def test_post_429_with_idempotency_key_still_works(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(
                    429,
                    json_body={"error": {"message": "slow"}},
                    headers={"Retry-After": "1"},
                ),
                _resp(200, json_body={"id": "p1"}),
            ],
        )

        with patch("time.sleep"):
            result = client.post(
                "/payments",
                json={"amount": 10},
                headers={"Idempotency-Key": "abc123"},
            )

        assert result == {"id": "p1"}
        assert len(captured) == 2

    def test_post_transport_error_is_not_retried(self):
        client = _make_client()
        call_count = {"n": 0}

        def fake_request(*args, **kwargs):
            call_count["n"] += 1
            raise httpx.ConnectError("no route to host")

        client._client.request = fake_request  # type: ignore[method-assign]

        with pytest.raises(httpx.ConnectError):
            client.post("/payments", json={"amount": 10})

        assert call_count["n"] == 1

    def test_post_transport_error_is_retried_with_idempotency_key(self):
        client = _make_client()
        captured: List[dict] = []

        def fake_request(*args, **kwargs):
            captured.append({"args": args, **kwargs})
            if len(captured) == 1:
                raise httpx.ConnectError("no route to host")
            return httpx.Response(status_code=200, json={"id": "p1"})

        client._client.request = fake_request  # type: ignore[method-assign]

        with patch("time.sleep"):
            result = client.post(
                "/payments",
                json={"amount": 10},
                headers={"Idempotency-Key": "xyz"},
            )

        assert result == {"id": "p1"}
        assert len(captured) == 2

    def test_get_transport_error_is_retried(self):
        client = _make_client()
        captured: List[dict] = []

        def fake_request(*args, **kwargs):
            captured.append({"args": args, **kwargs})
            if len(captured) == 1:
                raise httpx.TimeoutException("timed out")
            return httpx.Response(status_code=200, json={"ok": True})

        client._client.request = fake_request  # type: ignore[method-assign]

        with patch("time.sleep"):
            result = client.get("/items")

        assert result == {"ok": True}
        assert len(captured) == 2

    def test_idempotency_key_header_case_insensitive(self):
        client = _make_client()
        captured = _stub_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"id": "p1"}),
            ],
        )

        result = client.post(
            "/payments",
            json={"amount": 10},
            headers={"idempotency-key": "lowercased-key"},
        )

        assert result == {"id": "p1"}
        assert len(captured) == 2

    def test_post_exhausts_retries_with_idempotency_key(self):
        client = _make_client(max_retries=2)
        captured = _stub_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
            ],
        )

        with patch("time.sleep"):
            with pytest.raises(NetworkError):
                client.post(
                    "/payments",
                    json={"amount": 10},
                    headers={"Idempotency-Key": "retry-3"},
                )

        # 3 total attempts: initial + 2 retries
        assert len(captured) == 3

    def test_extra_headers_are_merged(self):
        client = _make_client()
        captured = _stub_httpx_client(client, [_resp(200, json_body={})])

        client.get("/x", headers={"X-Custom": "hello"})

        assert captured[0]["headers"]["X-Custom"] == "hello"
        # defaults still present
        assert captured[0]["headers"]["Accept"] == "application/json"
        assert captured[0]["headers"]["Authorization"] == "Bearer sk_test_xxx"
