import sys
from types import ModuleType
from typing import Optional

from .client import ShadeClient
from .config import config, Environment
from .gateway import Gateway
from .http import AsyncHTTPClient, SyncHTTPClient
from .errors import (
    AuthenticationError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    HTTPError,
    RateLimitError,
    ShadeError,
)
from .models import SwapPayment, SwapStatus

__version__ = "0.1.0"

# ShadeClient is an alias for Gateway.
ShadeClient = Gateway

__all__ = [
    "AsyncHTTPClient",
    "AuthenticationError",
    "Environment",
    "Gateway",
    "HTTPError",
    "InvalidRequestError",
    "NetworkError",
    "NotFoundError",
    "RateLimitError",
    "ShadeClient",
    "ShadeError",
    "SyncHTTPClient",
    "SwapPayment",
    "SwapStatus",
    "config",
    "api_base",
    "environment",
    "max_retries",
    "timeout",
]

class _ShadeModule(ModuleType):
    """Module subclass that exposes config-backed attributes on the shade package."""

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
