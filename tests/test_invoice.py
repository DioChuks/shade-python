from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from stellar_sdk import Keypair

import shade
from shade import InvalidRequestError, Invoice, InvoiceStatus, ShadeObject

TOKEN = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
PAYER = Keypair.random().public_key

NOW = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
FUTURE = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
PAST = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
U64_MAX = 2**64 - 1


def _contract_response(**overrides):
    """A representative raw Soroban contract payload (u64/i128 wire types)."""
    data = {
        "id": 42,
        "description": "Consulting services - August",
        "amount": 150000000,
        "token": TOKEN,
        "status": "pending",
        "merchant_id": 7,
        "payer": None,
        "date_created": NOW,
        "date_paid": None,
        "amount_paid": 0,
        "amount_refunded": 0,
        "expires_at": FUTURE,
    }
    data.update(overrides)
    return data


def test_from_dict_populates_all_fields():
    invoice = Invoice.from_dict(_contract_response())

    assert invoice.id == 42
    assert invoice.description == "Consulting services - August"
    assert invoice.amount == Decimal("150000000")
    assert invoice.token == TOKEN
    assert invoice.status == InvoiceStatus.PENDING
    assert invoice.merchant_id == 7
    assert invoice.payer is None
    assert invoice.date_created == datetime.fromtimestamp(NOW, tz=timezone.utc)
    assert invoice.date_paid is None
    assert invoice.amount_paid == Decimal("0")
    assert invoice.amount_refunded == Decimal("0")
    assert invoice.expires_at == datetime.fromtimestamp(FUTURE, tz=timezone.utc)


def test_amount_fields_are_decimal_not_int_or_float():
    invoice = Invoice.from_dict(
        _contract_response(amount=999, amount_paid=100, amount_refunded=1)
    )
    assert isinstance(invoice.amount, Decimal)
    assert isinstance(invoice.amount_paid, Decimal)
    assert isinstance(invoice.amount_refunded, Decimal)
    assert not isinstance(invoice.amount, float)


def test_timestamp_fields_are_datetime_not_int():
    invoice = Invoice.from_dict(_contract_response(date_paid=NOW))
    assert isinstance(invoice.date_created, datetime)
    assert isinstance(invoice.date_paid, datetime)
    assert isinstance(invoice.expires_at, datetime)
    assert not isinstance(invoice.date_created, int)


def test_id_and_merchant_id_are_int():
    invoice = Invoice.from_dict(_contract_response())
    assert isinstance(invoice.id, int)
    assert isinstance(invoice.merchant_id, int)


def test_payer_is_none_for_unpaid_invoice():
    invoice = Invoice.from_dict(_contract_response(status="pending", payer=None))
    assert invoice.payer is None


def test_payer_is_set_for_paid_invoice():
    invoice = Invoice.from_dict(
        _contract_response(
            status="paid", payer=PAYER, date_paid=NOW, amount_paid=150000000
        )
    )
    assert invoice.payer == PAYER


def test_is_expired_true_when_expires_at_in_past():
    invoice = Invoice.from_dict(_contract_response(expires_at=PAST))
    assert invoice.is_expired is True


def test_is_expired_false_when_expires_at_in_future():
    invoice = Invoice.from_dict(_contract_response(expires_at=FUTURE))
    assert invoice.is_expired is False


def test_is_expired_false_when_no_expiry_set():
    invoice = Invoice.from_dict(_contract_response(expires_at=None))
    assert invoice.is_expired is False


def test_outstanding_equals_amount_minus_amount_paid():
    invoice = Invoice.from_dict(
        _contract_response(
            status="partially_paid",
            amount=1000,
            amount_paid=400,
        )
    )
    assert invoice.outstanding == Decimal("600")


def test_outstanding_is_zero_when_fully_paid():
    invoice = Invoice.from_dict(
        _contract_response(status="paid", amount=1000, amount_paid=1000)
    )
    assert invoice.outstanding == Decimal("0")


def test_is_paid_true_only_when_status_is_paid():
    invoice = Invoice.from_dict(
        _contract_response(status="paid", amount_paid=150000000)
    )
    assert invoice.is_paid is True


@pytest.mark.parametrize("status", ["pending", "expired", "partially_paid", "refunded"])
def test_is_paid_false_for_non_paid_statuses(status):
    invoice = Invoice.from_dict(_contract_response(status=status))
    assert invoice.is_paid is False


def test_status_is_invoice_status_enum():
    for raw in ("pending", "paid", "expired", "partially_paid", "refunded"):
        invoice = Invoice.from_dict(_contract_response(status=raw))
        assert isinstance(invoice.status, InvoiceStatus)
        assert invoice.status.value == raw


def test_invalid_status_raises():
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(status="bogus"))
    assert exc_info.value.param == "status"


def test_boolean_id_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(id=True))
    assert exc_info.value.param == "id"


def test_boolean_merchant_id_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(merchant_id=False))
    assert exc_info.value.param == "merchant_id"


@pytest.mark.parametrize("field", ["id", "merchant_id"])
def test_float_id_fields_are_rejected(field):
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(**{field: 1.9}))
    assert exc_info.value.param == field


@pytest.mark.parametrize("field", ["date_created", "date_paid", "expires_at"])
def test_float_timestamp_fields_are_rejected(field):
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(**{field: 1.9}))
    assert exc_info.value.param == field


@pytest.mark.parametrize("field", ["id", "merchant_id"])
def test_negative_id_fields_are_rejected(field):
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(**{field: -1}))
    assert exc_info.value.param == field


def test_negative_expires_at_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(expires_at=-100))
    assert exc_info.value.param == "expires_at"


@pytest.mark.parametrize("field", ["id", "merchant_id"])
def test_id_fields_exceeding_u64_max_are_rejected(field):
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(**{field: U64_MAX + 1}))
    assert exc_info.value.param == field


def test_expires_at_exceeding_u64_max_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Invoice.from_dict(_contract_response(expires_at=U64_MAX + 1))
    assert exc_info.value.param == "expires_at"


def test_from_dict_rejects_non_dict_input():
    with pytest.raises(InvalidRequestError) as excinfo:
        Invoice.from_dict(["id", 42])

    assert "expects a dict" in str(excinfo.value)


def test_from_dict_preserves_unknown_keys():
    invoice = Invoice.from_dict(_contract_response(contract_version=3))
    assert invoice.contract_version == 3
    assert invoice.to_dict()["contract_version"] == 3


def test_round_trip_via_to_dict_from_dict():
    invoice = Invoice.from_dict(
        _contract_response(status="partially_paid", amount_paid=500)
    )
    round_tripped = Invoice.from_dict(invoice.to_dict())

    assert round_tripped == invoice
    assert isinstance(round_tripped.amount, Decimal)
    assert isinstance(round_tripped.date_created, datetime)
    assert round_tripped.outstanding == invoice.outstanding


def test_invoice_is_exported_from_package():
    assert shade.Invoice is Invoice
    assert shade.InvoiceStatus is InvoiceStatus
    assert issubclass(Invoice, ShadeObject)


def test_model_has_no_legacy_fields():
    invoice = Invoice.from_dict(_contract_response())
    for forbidden in ("line_items", "customer_email", "payment_url", "total"):
        assert forbidden not in type(invoice).model_fields
        assert not hasattr(invoice, forbidden)


def test_invoice_status_values_match_spec():
    assert {member.value for member in InvoiceStatus} == {
        "pending",
        "paid",
        "expired",
        "partially_paid",
        "refunded",
    }
