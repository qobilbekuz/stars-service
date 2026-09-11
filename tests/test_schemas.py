"""Validatsiya testlari."""

import pytest
from pydantic import ValidationError

from app.schemas.orders import (
    PremiumOrderCreate,
    StarsOrderCreate,
    normalize_username,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("durov", "durov"),
        ("@durov", "durov"),
        ("https://t.me/durov", "durov"),
        ("t.me/durov", "durov"),
        ("  @Durov  ", "Durov"),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize("raw", ["ab", "1user", "user!name", "", "@"])
def test_normalize_username_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_username(raw)


def test_stars_order_requires_recipient():
    with pytest.raises(ValidationError, match="recipient"):
        StarsOrderCreate(quantity=100)


def test_stars_order_rejects_out_of_range_quantity():
    with pytest.raises(ValidationError):
        StarsOrderCreate(recipient_username="@durov", quantity=1)
    with pytest.raises(ValidationError):
        StarsOrderCreate(recipient_username="@durov", quantity=99_999_999)


def test_premium_order_rejects_invalid_months():
    with pytest.raises(ValidationError):
        PremiumOrderCreate(recipient_username="@durov", months=5)

    order = PremiumOrderCreate(recipient_username="@durov", months=6)
    assert order.months == 6
    assert order.recipient_username == "durov"


def test_user_id_only_recipient_is_valid():
    order = StarsOrderCreate(recipient_user_id=123456789, quantity=100)
    assert order.recipient_username is None
