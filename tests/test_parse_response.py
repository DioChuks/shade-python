"""
Tests for the single ``_parse_response`` response funnel (http.py).

Acceptance criteria covered:
* Every 4xx/5xx response maps to the correct typed exception.
* The raw response body and HTTP status are accessible on every exception.
* A non-JSON body raises ShadeError instead of a raw JSONDecodeError.
* 2xx responses carrying an ``error`` key are still treated as errors.
"""
from __future__ import annotations

import httpx
import pytest

from shade.errors import (
    AuthenticationError,
    HTTPError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ShadeError,
)
from shade.http import _parse_response


def _resp(status: int, *, json_body=None, text=None, headers=None) -> httpx.Response:
    """Build an httpx.Response with either a JSON or raw-text body."""
    kwargs = {"status_code": status, "headers": headers or {}}
    if json_body is not None:
        kwargs["json"] = json_body
    elif text is not None:
        kwargs["text"] = text
    return httpx.Response(**kwargs)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestSuccess:
    def test_2xx_returns_decoded_dict(self):
        resp = _resp(200, json_body={"id": "pay_1", "status": "ok"})
        assert _parse_response(resp) == {"id": "pay_1", "status": "ok"}

    def test_201_returns_decoded_dict(self):
        resp = _resp(201, json_body={"id": "inv_1"})
        assert _parse_response(resp) == {"id": "inv_1"}

    def test_empty_body_returns_empty_dict(self):
        resp = _resp(204, text="")
        assert _parse_response(resp) == {}

    def test_2xx_with_error_key_is_treated_as_error(self):
        resp = _resp(200, json_body={"error": {"message": "soft failure"}})
        with pytest.raises(ShadeError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == 200
        assert "soft failure" in str(exc_info.value)
        assert exc_info.value.response_body == resp.text

    def test_2xx_with_falsy_error_key_is_success(self):
        resp = _resp(200, json_body={"id": "pay_1", "error": None})
        assert _parse_response(resp) == {"id": "pay_1", "error": None}

    def test_2xx_non_dict_json_raises_shade_error(self):
        resp = _resp(200, json_body=[1, 2, 3])
        with pytest.raises(ShadeError) as exc_info:
            _parse_response(resp)
        assert "Invalid response from API" in str(exc_info.value)


# ---------------------------------------------------------------------------
# JSON decode failure
# ---------------------------------------------------------------------------

class TestDecodeFailure:
    def test_2xx_non_json_body_raises_shade_error_not_jsondecodeerror(self):
        resp = _resp(200, text="<html>not json</html>")
        with pytest.raises(ShadeError) as exc_info:
            _parse_response(resp)
        assert str(exc_info.value).startswith("Invalid response from API")
        assert exc_info.value.status_code == 200
        assert exc_info.value.response_body == "<html>not json</html>"

    def test_error_status_non_json_body_still_maps_to_typed_error(self):
        resp = _resp(500, text="upstream exploded")
        with pytest.raises(NetworkError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == 500
        assert exc_info.value.response_body == "upstream exploded"


# ---------------------------------------------------------------------------
# Error status mapping
# ---------------------------------------------------------------------------

class TestErrorMapping:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_error(self, status):
        resp = _resp(status, json_body={"error": {"message": "bad token"}})
        with pytest.raises(AuthenticationError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == status
        assert "bad token" in str(exc_info.value)

    @pytest.mark.parametrize("status", [400, 422])
    def test_invalid_request_error(self, status):
        resp = _resp(status, json_body={"error": {"message": "bad input"}})
        with pytest.raises(InvalidRequestError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == status

    def test_invalid_request_carries_nested_field_errors(self):
        body = {"error": {"message": "validation failed", "fields": {"amount": "required"}}}
        resp = _resp(422, json_body=body)
        with pytest.raises(InvalidRequestError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.field_errors == {"amount": "required"}

    def test_invalid_request_carries_top_level_field_errors(self):
        body = {"message": "bad", "errors": [{"field": "currency", "msg": "unknown"}]}
        resp = _resp(400, json_body=body)
        with pytest.raises(InvalidRequestError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.field_errors == [{"field": "currency", "msg": "unknown"}]

    def test_invalid_request_field_errors_none_when_absent(self):
        resp = _resp(400, json_body={"error": {"message": "bad"}})
        with pytest.raises(InvalidRequestError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.field_errors is None

    def test_not_found_error(self):
        resp = _resp(404, json_body={"error": {"message": "missing"}})
        with pytest.raises(NotFoundError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == 404

    def test_rate_limit_error_parses_retry_after(self):
        resp = _resp(429, json_body={"error": {"message": "slow down"}},
                     headers={"Retry-After": "12"})
        with pytest.raises(RateLimitError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 12

    def test_rate_limit_error_retry_after_none_when_absent(self):
        resp = _resp(429, json_body={"error": {"message": "slow down"}})
        with pytest.raises(RateLimitError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.retry_after is None

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_error_maps_to_network_error(self, status):
        resp = _resp(status, json_body={"error": {"message": "boom"}})
        with pytest.raises(NetworkError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == status

    def test_other_4xx_maps_to_http_error(self):
        resp = _resp(418, json_body={"error": {"message": "teapot"}})
        with pytest.raises(HTTPError) as exc_info:
            _parse_response(resp)
        assert exc_info.value.status_code == 418


# ---------------------------------------------------------------------------
# Raw body + status accessible on every exception
# ---------------------------------------------------------------------------

class TestExceptionContext:
    @pytest.mark.parametrize(
        "status, exc_type",
        [
            (401, AuthenticationError),
            (400, InvalidRequestError),
            (404, NotFoundError),
            (429, RateLimitError),
            (503, NetworkError),
            (418, HTTPError),
        ],
    )
    def test_raw_body_and_status_present(self, status, exc_type):
        resp = _resp(status, json_body={"error": {"message": "x"}})
        with pytest.raises(exc_type) as exc_info:
            _parse_response(resp)
        err = exc_info.value
        assert err.status_code == status
        assert err.response_body == resp.text
        assert isinstance(err, ShadeError)

    def test_message_falls_back_when_body_has_no_message(self):
        resp = _resp(404, json_body={"foo": "bar"})
        with pytest.raises(NotFoundError) as exc_info:
            _parse_response(resp)
        assert "Resource not found" in str(exc_info.value)
