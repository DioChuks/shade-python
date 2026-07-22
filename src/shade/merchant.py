"""
Merchant model.

Mirrors the Shade backend's Prisma ``Merchant`` schema, with field names
converted from ``camelCase`` (Prisma/JSON) to ``snake_case`` (Python).  The
:attr:`Merchant.merchant_id` field (from Prisma ``merchantId: Int``) is the
numeric identifier the Soroban contract stamps onto every invoice, making it
the bridge between the backend and the on-chain world.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from stellar_sdk.strkey import StrKey

from .base import ShadeObject
from .errors import InvalidRequestError


class Merchant(ShadeObject):
    """A Shade merchant account.

    Construct from an API response with :meth:`ShadeObject.from_dict`, which
    maps camelCase JSON keys to the snake_case attributes below.  The
    ``address`` must be a valid Stellar ed25519 public key; anything else
    raises :class:`~shade.errors.InvalidRequestError` on construction.
    """

    _ALIASES: ClassVar[Dict[str, str]] = {
        "merchantId": "merchant_id",
        "firstName": "first_name",
        "lastName": "last_name",
        "businessName": "business_name",
    }

    def __init__(
        self,
        *,
        id: str,
        merchant_id: int,
        address: str,
        active: bool,
        verified: bool,
        account: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        business_name: Optional[str] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
        logo: Optional[str] = None,
        webhook: Optional[str] = None,
    ) -> None:
        self.id: str = id
        self.merchant_id: int = _coerce_merchant_id(merchant_id)
        self.address: str = _validate_stellar_address(address)
        self.active: bool = bool(active)
        self.verified: bool = bool(verified)
        self.account: Optional[str] = account
        self.email: Optional[str] = email
        self.first_name: Optional[str] = first_name
        self.last_name: Optional[str] = last_name
        self.business_name: Optional[str] = business_name
        self.category: Optional[str] = category
        self.description: Optional[str] = description
        self.logo: Optional[str] = logo
        self.webhook: Optional[str] = webhook

    @property
    def display_name(self) -> Optional[str]:
        """The most informative human-readable name available.

        Prefers ``business_name``; falls back to the person's full name
        (``"{first_name} {last_name}"`` trimmed); finally ``email``.
        """
        if self.business_name:
            return self.business_name
        full_name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        if full_name:
            return full_name
        return self.email


def _coerce_merchant_id(value: Any) -> int:
    """Return ``value`` as an ``int``, rejecting bools and non-integers."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise InvalidRequestError(
            f"merchant_id must be an integer, got {type(value).__name__}",
            param="merchant_id",
        )
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidRequestError(
            f"merchant_id must be an integer, got {value!r}",
            param="merchant_id",
        )


def _validate_stellar_address(address: Any) -> str:
    """Validate a Stellar ed25519 public key (starts with ``G``, 56 chars)."""
    if not isinstance(address, str) or not StrKey.is_valid_ed25519_public_key(address):
        raise InvalidRequestError(
            f"address must be a valid Stellar public key "
            f"(starts with 'G', 56 characters), got {address!r}",
            param="address",
        )
    return address
