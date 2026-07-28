from __future__ import annotations

import httpx
from typing import Any, Dict, Optional

from . import config as _config
from .client import ShadeClient as ClientShadeClient
from .config import Environment, validate_client_settings, get_config
from .http import AsyncHTTPClient, SyncHTTPClient, DEFAULT_MAX_RETRIES


class Gateway:
    """
    Main entry point for the Shade Payment Gateway.

    Parameters
    ----------
    api_key : str, optional
        Your Shade API key. Defaults to module-level ``shade.api_key``.
    environment : str | Environment, optional
        Controls the Stellar network passphrase and the default API URL.
        Defaults to the module-level ``shade.environment`` (``Environment.SANDBOX``).
    api_base : str, optional
        Override the API host for this client (useful for local dev or staging).
        Takes precedence over the module-level ``shade.api_base`` and the
        URL derived from ``environment``. Trailing slashes are trimmed.
        Intended for development and testing only.
    base_url : str
        Deprecated. Prefer ``api_base``.
    max_retries : int, optional
        Number of automatic retries on HTTP 429 and transient failures.
        Defaults to the module-level ``shade.max_retries`` (3). Set to ``0``
        to disable auto-retry.
    timeout : float, optional
        Per-request socket timeout in seconds. Defaults to the module-level
        ``shade.timeout`` (30.0).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        environment: Optional[Environment | str] = None,
        api_base: Optional[str] = None,
        base_url: str = "",
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        debug: bool = False,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._environment = environment
        self._api_base = api_base or (base_url if base_url else None)
        self._max_retries = max_retries
        self._timeout = timeout
        self.debug = debug

        if max_retries is not None or timeout is not None:
            validate_client_settings(
                timeout if timeout is not None else _config.timeout,
                max_retries if max_retries is not None else _config.max_retries,
            )

        self._http = SyncHTTPClient(
            base_url=self._api_base,
            api_key=self._api_key,
            environment=self._environment,
            max_retries=self._max_retries,
            timeout=self._timeout,
        )
        self._async_http = AsyncHTTPClient(
            base_url=self._api_base,
            api_key=self._api_key,
            environment=self._environment,
            max_retries=self._max_retries,
            timeout=self._timeout,
        )

        self._client = ClientShadeClient(
            api_key=self._api_key,
            base_url=self._api_base,
            environment=self._environment,
            debug=debug,
            http_client=http_client,
        )


    @property
    def api_key(self) -> Optional[str]:
        return self._api_key if self._api_key is not None else _config.api_key

    @api_key.setter
    def api_key(self, value: Optional[str]) -> None:
        self._api_key = value
        self._http.api_key = value
        self._async_http.api_key = value
        self._client.api_key = value

    @property
    def environment(self) -> Environment:
        if self._environment is not None:
            return _config.parse_environment(self._environment)
        return _config.environment

    @environment.setter
    def environment(self, value: str | Environment) -> None:
        parsed = _config.parse_environment(value)
        self._environment = parsed
        self._http.environment = parsed
        self._async_http.environment = parsed
        self._client.environment = parsed


    @property
    def _base_url(self) -> str:
        if self._api_base:
            return self._api_base.rstrip("/")
        if _config.api_base:
            return _config.api_base.rstrip("/")
        return self.environment.base_url.rstrip("/")


    # ------------------------------------------------------------------
    # Sync API
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Gateway":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json: Any = None,
        content: Optional[bytes] = None,
    ) -> httpx.Response:
        return self._client.request(
            method,
            path,
            headers=headers,
            json=json,
            content=content,
        )

    def process_payment(self, amount: float, currency: str) -> Dict[str, Any]:
        """
        Process a payment (sync).

        Parameters
        ----------
        amount : float
            Payment amount.
        currency : str
            ISO 4217 currency code (e.g. ``"USD"``).

        Returns
        -------
        dict
            API response body.
        """
        return self._http.request(
            "POST",
            "/payments",
            {"amount": amount, "currency": currency},
        )

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def process_payment_async(
        self, amount: float, currency: str
    ) -> Dict[str, Any]:
        """Async variant of :meth:`process_payment`."""
        return await self._async_http.request(
            "POST",
            "/payments",
            {"amount": amount, "currency": currency},
        )

