import sys
from types import ModuleType
from typing import Optional

from .client import ShadeClient, default_client, reset_default_client
from .config import config, Environment
from .gateway import Gateway
from .http import AsyncHTTPClient, SyncHTTPClient
from .resources import BaseResource
from .errors import (
    AuthenticationError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    HTTPError,
    RateLimitError,
    ShadeError,
    SignatureVerificationError,
)
from .models import AssetBalance, Balance, Merchant, ShadeObject, Transfer, TransferStatus

__version__ = "0.1.0"

__all__ = [
    "AssetBalance",
    "AsyncHTTPClient",
    "AuthenticationError",
    "Balance",
    "BaseResource",
    "Environment",
    "Gateway",
    "HTTPError",
    "InvalidRequestError",
    "Merchant",
    "NetworkError",
    "NotFoundError",
    "RateLimitError",
    "ShadeClient",
    "ShadeError",
    "SignatureVerificationError",
    "ShadeObject",
    "SyncHTTPClient",
    "Transfer",
    "TransferStatus",
    "config",
    "api_base",
    "api_key",
    "default_client",
    "environment",
    "max_retries",
    "reset_default_client",
    "timeout",
]

class _ShadeModule(ModuleType):
    """Module subclass that exposes config-backed attributes on the shade package."""

    @property
    def api_key(self) -> Optional[str]:
        from . import config as _config
        return _config.api_key

    @api_key.setter
    def api_key(self, value: Optional[str]) -> None:
        from . import config as _config
        _config.api_key = value

    @property
    def api_base(self) -> Optional[str]:
        from . import config as _config
        return _config.api_base

    @api_base.setter
    def api_base(self, value: Optional[str]) -> None:
        from . import config as _config
        _config.api_base = value

    @property
    def timeout(self) -> float:
        from . import config as _config
        return _config.timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        from . import config as _config
        _config.timeout = value

    @property
    def max_retries(self) -> int:
        from . import config as _config
        return _config.max_retries

    @max_retries.setter
    def max_retries(self, value: int) -> None:
        from . import config as _config
        _config.max_retries = value

    @property
    def environment(self) -> Environment:
        from . import config as _config
        return _config.environment

    @environment.setter
    def environment(self, value: str | Environment) -> None:
        from . import config as _config
        _config.environment = _config.parse_environment(value)


sys.modules[__name__].__class__ = _ShadeModule
