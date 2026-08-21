from datetime import datetime

from app.parsing import parse_date_value, to_number


def test_to_number_plain():
    assert to_number(295200) == 295200.0
    assert to_number("295200") == 295200.0
    assert to_number("") == 0.0
    assert to_number(None) == 0.0


def test_to_number_thousands_separator():
    assert to_number("1.234.567") == 1234567.0


def test_to_number_negative_fee():
    assert to_number(-47527) == -47527.0


def test_parse_date_shopee_text_with_time():
    # Regression: the real Shopee export stores "Ngày đặt hàng" as plain text
    # like "2026-02-01 00:01", not a real date cell — this bit the JS version
    # too (see the timezone/date-parsing fixes during Phase 1).
    d = parse_date_value("2026-02-01 00:01")
    assert d == datetime(2026, 2, 1)


def test_parse_date_dmy_slash():
    assert parse_date_value("01/02/2026") == datetime(2026, 2, 1)


def test_parse_date_ymd_dash():
    assert parse_date_value("2026-02-01") == datetime(2026, 2, 1)


def test_parse_date_native_datetime_passthrough():
    d = datetime(2026, 2, 1, 10, 30)
    assert parse_date_value(d) == d


def test_parse_date_invalid_returns_none():
    assert parse_date_value("not a date") is None
    assert parse_date_value("") is None
