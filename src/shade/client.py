"""
Per-instance SDK configuration.

``ShadeClient`` binds a set of credentials and connection settings to a single
object, so an application acting on behalf of several merchants can hold one
client per tenant instead of mutating the global ``shade`` module config.
Anything left unset falls back to the global config at construction time.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from .config import Environment, validate_client_settings
from .config import config as _config
from .errors import AuthenticationError
from .http import AsyncHTTPClient, HTTPXTransport, SyncHTTPClient

API_KEY_ENV_VAR = "SHADE_API_KEY"
ENVIRONMENT_ENV_VAR = "SHADE_ENVIRONMENT"


class ShadeClient:
    """An isolated Shade API client carrying its own credentials and settings.

    Two clients built with different API keys never share state, so a
    multi-tenant application can keep one per merchant::

        acme = ShadeClient(api_key="sk_live_acme")
        globex = ShadeClient(api_key="sk_live_globex")

    Every parameter falls back to the matching global setting
    (``shade.api_key``, ``shade.environment``, …) when omitted, and the fallback
    is resolved once at construction — later changes to the global config do not
    retroactively alter an existing client.

    Parameters
    ----------
    api_key : str, optional
        Your Shade API key. Defaults to the module-level ``shade.api_key``.
    environment : str | Environment, optional
        Controls the Stellar network passphrase and the default API URL.
        Defaults to the module-level ``shade.environment``.
    api_base : str, optional
        Override the API host for this client (local dev, staging, or a
        self-hosted backend). Takes precedence over the module-level
        ``shade.api_base`` and the URL derived from ``environment``. Trailing
        slashes are trimmed.
    timeout : float, optional
        Per-request socket timeout in seconds. Defaults to ``shade.timeout``.
    max_retries : int, optional
        Automatic retries on HTTP 429 and transient failures. Defaults to
        ``shade.max_retries``. Set to ``0`` to disable auto-retry.
    base_url : str
        Deprecated. Prefer ``api_base``.
    debug : bool
        Log requests and responses for this client. The global
        ``shade.config.debug`` enables logging regardless of this flag.
    http_client : httpx.Client, optional
        Reuse an existing httpx client instead of creating one. The caller
        keeps ownership: :meth:`close` will not close a client it was given.

    Raises
    ------
    AuthenticationError
        If no API key is given and no global ``shade.api_key`` is set.
    ValueError
        If ``timeout`` or ``max_retries`` is out of range, or ``environment``
        is not a recognised value.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        environment: Optional[Environment | str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        base_url: str = "",
        debug: bool = False,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        resolved_api_key = api_key or _config.api_key
        if not resolved_api_key:
            raise AuthenticationError(
                "No API key provided. Pass api_key= to ShadeClient, set "
                f"shade.api_key, or set the {API_KEY_ENV_VAR} environment variable."
            )
        self.api_key = resolved_api_key

        if environment is not None:
            self.environment = _config.parse_environment(environment)
        else:
            self.environment = _config.environment

        self.max_retries = _config.max_retries if max_retries is None else max_retries
        self.timeout = _config.timeout if timeout is None else timeout
        validate_client_settings(self.timeout, self.max_retries)

        # Resolution order: explicit api_base > module-level shade.api_base
        # > legacy base_url > environment URL
        resolved = api_base or _config.api_base or base_url or self.environment.base_url
        self._base_url = resolved.rstrip("/")
        self.debug = debug

        self._http = SyncHTTPClient(
            base_url=self._base_url,
            api_key=self.api_key,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )
        self._async_http = AsyncHTTPClient(
            base_url=self._base_url,
            api_key=self.api_key,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )
        self._client = HTTPXTransport(
            api_key=self.api_key,
            base_url=self._base_url,
            debug=debug,
            http_client=http_client,
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "ShadeClient":
        """Build a client from ``SHADE_API_KEY`` and ``SHADE_ENVIRONMENT``.

        Either variable may be absent, in which case the usual global-config
        fallback applies — so a missing ``SHADE_API_KEY`` with no
        ``shade.api_key`` set raises :class:`~shade.errors.AuthenticationError`.

        Any keyword argument overrides the corresponding environment variable,
        letting callers take the key from the environment while setting the rest
        explicitly::

            client = ShadeClient.from_env(timeout=5.0)
        """
        env_kwargs: Dict[str, Any] = {}
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if api_key:
            env_kwargs["api_key"] = api_key
        environment = os.environ.get(ENVIRONMENT_ENV_VAR)
        if environment:
            env_kwargs["environment"] = environment
        env_kwargs.update(overrides)
        return cls(**env_kwargs)

    @property
    def api_base(self) -> str:
        """The resolved API base URL this client sends requests to."""
        return self._base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ShadeClient":
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
        """Send a request and return the raw ``httpx.Response``."""
        return self._client.request(
            method,
            path,
            headers=headers,
            json=json,
            content=content,
        )

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} api_key={_mask_api_key(self.api_key)!r} "
            f"environment={self.environment.value!r} api_base={self._base_url!r}>"
        )


def _mask_api_key(api_key: str) -> str:
    """Show only the last four characters of a key, for use in reprs."""
    if len(api_key) <= 4:
        return "****"
    return "*" * (len(api_key) - 4) + api_key[-4:]


_default_client: Optional[ShadeClient] = None
_default_client_settings: Optional[tuple] = None


def default_client() -> ShadeClient:
    """Return the shared client built from the global ``shade`` config.

    Resources fall back to this when constructed without an explicit
    ``client=``. The instance is cached, but rebuilt whenever a global setting
    changes, so assigning ``shade.api_key`` after the first call still takes
    effect.

    Raises:
        AuthenticationError: If no global ``shade.api_key`` has been set.
    """
    global _default_client, _default_client_settings

    settings = (
        _config.api_key,
        _config.environment,
        _config.api_base,
        _config.timeout,
        _config.max_retries,
    )
    if _default_client is None or _default_client_settings != settings:
        _default_client = ShadeClient()
        _default_client_settings = settings
    return _default_client


def reset_default_client() -> None:
    """Drop the cached global client. Primarily useful in tests."""
    global _default_client, _default_client_settings
    _default_client = None
    _default_client_settings = None
