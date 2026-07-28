from typing import Any, Mapping, Optional

import httpx

from shade._debug import log_request, log_response
from shade.config import config, get_config


class ShadeClient:
    """HTTP client for the Shade Payment Gateway API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        debug: bool = False,
        http_client: Optional[httpx.Client] = None,
    ):
        self.api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else None
        self.debug = debug
        self._http = http_client or httpx.Client()
        self._owns_http_client = http_client is None

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        return config.api_base or config.environment.base_url.rstrip("/")

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> "ShadeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _should_debug(self) -> bool:
        return self.debug or config.debug

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json: Any = None,
        content: Optional[bytes] = None,
    ) -> httpx.Response:
        cfg = get_config(
            api_key=self.api_key,
            api_base=self._base_url,
        )
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{cfg.base_url}{normalized_path}"
        request_headers = {"Authorization": f"Bearer {cfg.api_key}", **(headers or {})}

        if self._should_debug():
            log_request(method, url, request_headers, content if content is not None else json)

        response = self._http.request(
            method,
            url,
            headers=request_headers,
            json=json,
            content=content,
        )

        if self._should_debug():
            log_response(response.status_code, response.headers, response.text)

        return response

