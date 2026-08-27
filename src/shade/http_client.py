from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

import httpx

from ._debug import log_request, log_response
from .config import Environment, get_config, validate_client_settings
from .config import config as _config
from .errors import NetworkError, ShadeError
from .http import (
    _BASE_BACKOFF,
    _is_retryable_error,
    _parse_response,
    _parse_retry_after,
    _retry_delay,
)

logger = logging.getLogger(__name__)


def _build_full_url(base: str, path: str) -> str:
    """Combine a base URL and a path, avoiding double or missing slashes.

    Examples
    --------
    >>> _build_full_url("https://api.example.com", "/users")
    'https://api.example.com/users'
    >>> _build_full_url("https://api.example.com/", "users")
    'https://api.example.com/users'
    """
    if not base:
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


class _SyncHTTPClient:
    """Internal synchronous HTTP client wrapping ``httpx.Client``.

    This is an implementation detail shared by sync resource methods through
    :class:`~shade.client.ShadeClient`. It centralises header construction,
    URL building, response parsing and retry logic so resources never need
    to import or reference ``httpx`` directly.

    Parameters
    ----------
    api_key : str, optional
        Bearer token. Resolved against the global config at request time
        when omitted.
    api_base : str, optional
        Override the API base URL. Resolved against the global config at
        request time when omitted.
    timeout : float, optional
        Per-request socket timeout in seconds. Resolved against the global
        config at request time when omitted.
    environment : str | Environment, optional
        Controls the default ``api_base`` and the Stellar network.
    max_retries : int, optional
        How many times to retry HTTP 429 and transient 5xx errors. Defaults
        to the global ``shade.max_retries``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[float] = None,
        environment: Optional[Environment | str] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.api_key = api_key
        self._api_base = api_base.rstrip("/") if api_base else None
        self.environment = environment
        self._timeout = timeout
        self._max_retries = max_retries
        if timeout is not None or max_retries is not None:
            validate_client_settings(
                timeout if timeout is not None else _config.timeout,
                max_retries if max_retries is not None else _config.max_retries,
            )
        import shade

        self._user_agent = f"shade-python/{shade.__version__}"
        self._client = httpx.Client()

    @property
    def max_retries(self) -> int:
        return self._max_retries if self._max_retries is not None else _config.max_retries

    @property
    def timeout(self) -> float:
        return self._timeout if self._timeout is not None else _config.timeout

    @property
    def api_base(self) -> Optional[str]:
        return self._api_base

    @property
    def base_url(self) -> str:
        if self._api_base:
            return self._api_base
        env = (
            _config.parse_environment(self.environment)
            if self.environment is not None
            else _config.environment
        )
        return _config.api_base or env.base_url.rstrip("/")

    def close(self) -> None:
        """Close the underlying ``httpx.Client``."""
        self._client.close()

    def __enter__(self) -> "_SyncHTTPClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _headers(
        self,
        api_key: str,
        has_json_body: bool,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": self._user_agent,
        }
        if has_json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request, retrying on 429/transient errors.

        Parameters
        ----------
        method : str
            HTTP verb (``"GET"``, ``"POST"``, …).
        path : str
            API path, e.g. ``"/payments"``. Combined with the resolved
            base URL.
        params : Mapping[str, Any], optional
            Query-string parameters encoded and appended to the URL.
        json : Any, optional
            JSON-serializable request body. When provided the
            ``Content-Type: application/json`` header is added.

        Returns
        -------
        dict
            Decoded JSON response body.

        Raises
        ------
        ~shade.errors.AuthenticationError
            For HTTP 401/403.
        ~shade.errors.InvalidRequestError
            For HTTP 400/422.
        ~shade.errors.NotFoundError
            For HTTP 404.
        ~shade.errors.RateLimitError
            For HTTP 429 once retries are exhausted.
        ~shade.errors.NetworkError
            For HTTP 5xx once retries are exhausted, or unrecoverable
            transport failures.
        ~shade.errors.HTTPError
            For any other non-2xx status.
        ~shade.errors.ShadeError
            When a 2xx response body is not valid JSON.
        """
        cfg = get_config(
            api_key=self.api_key,
            environment=self.environment,
            api_base=self._api_base,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

        url = _build_full_url(cfg.base_url, path)
        headers = self._headers(cfg.api_key, has_json_body=json is not None)

        attempt = 0
        while True:
            if _config.debug:
                log_request(method, url, headers, json if json is not None else params)

            try:
                response = self._client.request(
                    method.upper(),
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                    timeout=cfg.timeout,
                )
            except Exception as exc:
                if _is_retryable_error(exc):
                    if attempt >= cfg.max_retries:
                        raise NetworkError(
                            "Request failed after exhausting retries",
                            status_code=None,
                        ) from exc
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after transient failure (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        delay,
                    )
                    import time

                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

            if _config.debug:
                log_response(response.status_code, response.headers, response.text)

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers)
                if attempt < cfg.max_retries:
                    wait = (
                        retry_after
                        if retry_after is not None
                        else _retry_delay(attempt, _BASE_BACKOFF)
                    )
                    logger.debug(
                        "Retrying request after 429 (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        wait,
                    )
                    import time

                    time.sleep(wait)
                    attempt += 1
                    continue

            try:
                return _parse_response(response)
            except Exception as exc:
                if (
                    attempt < cfg.max_retries
                    and _is_retryable_error(exc)
                ):
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after retryable status (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        delay,
                    )
                    import time

                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

    def get(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.request("GET", path, params=params)

    def post(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
    ) -> Dict[str, Any]:
        return self.request("POST", path, params=params, json=json)

    def patch(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
    ) -> Dict[str, Any]:
        return self.request("PATCH", path, params=params, json=json)

    def delete(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
    ) -> Dict[str, Any]:
        return self.request("DELETE", path, params=params, json=json)
