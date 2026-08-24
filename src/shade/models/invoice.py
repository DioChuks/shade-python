"""
Invoice model.

A faithful mirror of the Soroban smart contract's invoice struct: a single
description + amount + token tuple, with no line items. Contract-native
types are converted to their Python equivalents on the way in — ``u64``
identifiers/timestamps to :class:`int`/:class:`~datetime.datetime`, and
``i128`` monetary amounts to :class:`~decimal.Decimal` — so callers never
have to reason about on-chain wire types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator

from ..errors import InvalidRequestError
from .base import ShadeObject

_U64_FIELDS = ("id", "merchant_id")
_I128_FIELDS = ("amount", "amount_paid", "amount_refunded")
_TIMESTAMP_FIELDS = ("date_created", "date_paid", "expires_at")
_U64_MAX = 2**64 - 1


def _validate_u64(field: str, value: object) -> int:
    """Coerce ``value`` to a strictly valid on-chain ``u64``.

    Booleans and floats are rejected outright rather than coerced, since a
    silent bool->int or float->int cast could hide a malformed contract
    payload. Everything else is coerced via ``int()`` and range-checked
    against ``[0, 2**64 - 1]``.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not a boolean")
    if isinstance(value, float):
        raise ValueError(f"{field} must be an integer, not a float")
    if isinstance(value, int):
        result = value
    else:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field} must be an integer, got {type(value).__name__}"
            ) from exc
    if not 0 <= result <= _U64_MAX:
        raise ValueError(
            f"{field} must be between 0 and {_U64_MAX} (u64 range), got {result}"
        )
    return result


class InvoiceStatus(str, Enum):
    """Lifecycle status of an invoice, as tracked by the Soroban contract."""

    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    PARTIALLY_PAID = "partially_paid"
    REFUNDED = "refunded"


class Invoice(ShadeObject):
    """An invoice, mirroring the Soroban contract's on-chain struct.

    Build one from a raw contract response with :meth:`from_dict`, which
    converts ``u64`` identifiers/timestamps and ``i128`` amounts into their
    Python equivalents via :meth:`_from_contract_dict` before validation.
    There are no line items on-chain: a single ``description`` + ``amount``
    + ``token`` tuple is the whole invoice body.

    Attributes:
        id: The on-chain ``u64`` invoice ID.
        description: Free-text description of what the invoice is for.
        amount: Total amount due, converted from the contract's ``i128``.
        token: Contract address of the token the invoice is denominated in.
        status: Current lifecycle status.
        merchant_id: The merchant's numeric ID, as stamped by the contract.
        payer: Address of the account that paid, ``None`` until paid.
        date_created: When the invoice was created on-chain.
        date_paid: When the invoice was paid, ``None`` until paid.
        amount_paid: Amount paid so far, converted from the contract's ``i128``.
        amount_refunded: Amount refunded so far, converted from the
            contract's ``i128``.
        expires_at: When the invoice expires, if it has an expiry.
    """

    id: int
    description: str
    amount: Decimal
    token: str
    status: InvoiceStatus
    merchant_id: int
    payer: Optional[str] = None
    date_created: datetime
    date_paid: Optional[datetime] = None
    amount_paid: Decimal
    amount_refunded: Decimal
    expires_at: Optional[datetime] = None

    @field_validator("id", "merchant_id", mode="before")
    @classmethod
    def _reject_bool_ids(cls, value: object) -> object:
        # pydantic would otherwise coerce True/False to 1/0; a bool is never
        # a valid on-chain u64 id.
        if isinstance(value, bool):
            raise ValueError("must be an integer, not a boolean")
        return value

    @property
    def is_paid(self) -> bool:
        """Whether the invoice has been paid in full."""
        return self.status is InvoiceStatus.PAID

    @property
    def is_expired(self) -> bool:
        """Whether ``expires_at`` has passed, regardless of ``status``."""
        return self.expires_at is not None and self.expires_at < datetime.now(
            timezone.utc
        )

    @property
    def outstanding(self) -> Decimal:
        """Amount still owed: ``amount`` minus ``amount_paid``."""
        return self.amount - self.amount_paid

    @classmethod
    def _from_contract_dict(cls, data: dict) -> dict:
        """Convert raw Soroban contract field types to their Python equivalents.

        ``u64`` identifiers become :class:`int`, ``i128`` amounts become
        :class:`~decimal.Decimal`, and ``u64`` Unix timestamps become UTC
        :class:`~datetime.datetime`. Values that are already the target type
        (or ``None``) pass through untouched, so re-running a previously
        converted dict — as happens on a ``to_dict()`` -> ``from_dict()``
        round trip — is a no-op rather than a double conversion.

        Raises:
            ValueError: If a ``u64`` or timestamp field is a boolean, a
                float, negative, or exceeds ``2**64 - 1``.
            OverflowError: If a timestamp is within the valid ``u64`` range
                but too large for :class:`~datetime.datetime` to represent.
        """
        converted = dict(data)

        for field in _U64_FIELDS:
            value = converted.get(field)
            if value is None:
                continue
            converted[field] = _validate_u64(field, value)

        for field in _I128_FIELDS:
            value = converted.get(field)
            if value is None or isinstance(value, Decimal):
                continue
            converted[field] = Decimal(str(value))

        for field in _TIMESTAMP_FIELDS:
            value = converted.get(field)
            if value is None or isinstance(value, datetime):
                continue
            timestamp = _validate_u64(field, value)
            converted[field] = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        return converted

    @classmethod
    def from_dict(cls, data: dict) -> "Invoice":
        """Build an :class:`Invoice` from a raw contract (or round-tripped) dict.

        Raises:
            InvalidRequestError: If ``data`` is not a dict or fails validation.
        """
        if not isinstance(data, dict):
            raise InvalidRequestError(
                f"{cls.__name__}.from_dict() expects a dict, got "
                f"{type(data).__name__}"
            )
        try:
            converted = cls._from_contract_dict(data)
        except (ValueError, OverflowError) as err:
            field = str(err).split(" ", 1)[0]
            param = field if field in (*_U64_FIELDS, *_TIMESTAMP_FIELDS) else None
            raise InvalidRequestError(str(err), param=param) from err
        return cls(**converted)
