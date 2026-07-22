import pytest
from stellar_sdk import Keypair

import shade
from shade import InvalidRequestError, Merchant, ShadeObject

VALID_ADDRESS = Keypair.random().public_key


def _api_response(**overrides):
    """A representative camelCase backend payload."""
    data = {
        "id": "clx123",
        "merchantId": 42,
        "address": VALID_ADDRESS,
        "account": "GACCOUNT",
        "email": "owner@acme.test",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "businessName": "Acme Payments",
        "category": "software",
        "description": "We take money.",
        "logo": "https://cdn.test/logo.png",
        "webhook": "https://acme.test/hooks",
        "active": True,
        "verified": True,
    }
    data.update(overrides)
    return data


def test_from_dict_maps_camelcase_to_snake_case():
    merchant = Merchant.from_dict(_api_response())

    assert merchant.id == "clx123"
    assert merchant.merchant_id == 42
    assert merchant.address == VALID_ADDRESS
    assert merchant.first_name == "Ada"
    assert merchant.last_name == "Lovelace"
    assert merchant.business_name == "Acme Payments"
    assert merchant.active is True
    assert merchant.verified is True


def test_merchant_id_is_int():
    merchant = Merchant.from_dict(_api_response(merchantId=7))
    assert isinstance(merchant.merchant_id, int)
    assert merchant.merchant_id == 7


def test_merchant_is_exported_from_package():
    assert shade.Merchant is Merchant
    assert issubclass(Merchant, ShadeObject)


def test_from_dict_ignores_unknown_keys():
    merchant = Merchant.from_dict(_api_response(createdAt="2026-01-01", extra="x"))
    assert merchant.merchant_id == 42


def test_from_dict_requires_a_mapping():
    with pytest.raises(InvalidRequestError):
        Merchant.from_dict([("id", "x")])  # type: ignore[arg-type]


def test_invalid_address_raises_on_construction():
    with pytest.raises(InvalidRequestError) as exc_info:
        Merchant.from_dict(_api_response(address="not-a-stellar-key"))
    assert exc_info.value.param == "address"


def test_address_wrong_length_is_rejected():
    with pytest.raises(InvalidRequestError):
        Merchant(
            id="x",
            merchant_id=1,
            address="G" + "A" * 55,  # starts with G but too short / bad checksum
            active=True,
            verified=False,
        )


def test_non_integer_merchant_id_raises():
    with pytest.raises(InvalidRequestError) as exc_info:
        Merchant.from_dict(_api_response(merchantId="abc"))
    assert exc_info.value.param == "merchant_id"


def test_display_name_prefers_business_name():
    merchant = Merchant.from_dict(_api_response())
    assert merchant.display_name == "Acme Payments"


def test_display_name_falls_back_to_full_name():
    merchant = Merchant.from_dict(_api_response(businessName=None))
    assert merchant.display_name == "Ada Lovelace"


def test_display_name_trims_missing_last_name():
    merchant = Merchant.from_dict(_api_response(businessName=None, lastName=None))
    assert merchant.display_name == "Ada"


def test_display_name_falls_back_to_email():
    merchant = Merchant.from_dict(
        _api_response(businessName=None, firstName=None, lastName=None)
    )
    assert merchant.display_name == "owner@acme.test"


def test_optional_fields_default_to_none():
    merchant = Merchant(
        id="x",
        merchant_id=1,
        address=VALID_ADDRESS,
        active=False,
        verified=False,
    )
    assert merchant.account is None
    assert merchant.email is None
    assert merchant.display_name is None


def test_to_dict_round_trips_to_camelcase():
    payload = _api_response()
    merchant = Merchant.from_dict(payload)
    assert merchant.to_dict() == payload
    assert Merchant.from_dict(merchant.to_dict()) == merchant
