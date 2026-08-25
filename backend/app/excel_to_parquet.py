"""Excel -> Parquet conversion for one Report.

Port of buildDashboardRecords() in ../../js/app.js: reads the uploaded
Excel file, auto-detects the column mapping, computes the same derived
fields per row (sku, doanhSo, soLuongThuc, trangThai), and writes the
result as a Parquet table — this is the "data.parquet" that query_engine.py
later queries for the Dashboard. Rows whose date column can't be parsed are
dropped, matching the JS `if (!date) continue;`.
"""
from __future__ import annotations

import io

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .derive import compute_discount, compute_piship_fee, compute_platform_fee, compute_voucher, derive_row_fields
from .mapping import FIELDS, detect_mapping
from .parsing import parse_date_value, to_number


class MappingError(ValueError):
    """Raised when the uploaded file doesn't have enough recognizable columns."""


def _is_nan(v) -> bool:
    return isinstance(v, float) and v != v  # NaN != NaN


def read_excel_rows(file_like, sheet_name=0) -> tuple[list[dict], list[str]]:
    # openpyxl's default mode builds a full in-memory Cell/style object graph
    # for every cell, which dominates upload time on large sheets;
    # read_only=True switches it to a lazy row iterator instead (values
    # only, no styles), several times faster and lighter on memory. Falls
    # back to the plain auto-detected engine for non-.xlsx uploads (.xls,
    # which openpyxl can't read at all) rather than failing the upload.
    try:
        df = pd.read_excel(
            file_like, sheet_name=sheet_name, keep_default_na=False, dtype=object,
            engine="openpyxl", engine_kwargs={"read_only": True, "data_only": True},
        )
    except Exception:
        if hasattr(file_like, "seek"):
            file_like.seek(0)
        df = pd.read_excel(file_like, sheet_name=sheet_name, keep_default_na=False, dtype=object)
    headers = [str(c).strip() for c in df.columns]
    df.columns = headers
    rows = df.to_dict(orient="records")
    # Mirror SheetJS's sheet_to_json(ws, {defval: ""}): blanks become "".
    cleaned = [{k: ("" if (v is None or _is_nan(v)) else v) for k, v in r.items()} for r in rows]
    return cleaned, headers


def _text_or_unknown(row: dict, mapping: dict, field_key: str) -> str:
    col = mapping.get(field_key)
    if not col:
        return "(Không rõ)"
    v = str(row.get(col, "") if row.get(col, "") is not None else "").strip()
    return v or "(Không rõ)"


def _text_or_empty(row: dict, mapping: dict, field_key: str) -> str:
    col = mapping.get(field_key)
    if not col:
        return ""
    v = row.get(col, "")
    return str(v).strip() if v is not None else ""


def _order_paid_totals(raw_rows: list[dict], mapping: dict) -> dict:
    """Sums "Số tiền người mua thanh toán" per Mã đơn hàng across every raw
    row (before date-filtering) — the denominator used to prorate the
    order-level "Mã giảm giá của Shop" voucher fairly across an order's
    lines by each line's share of what the buyer actually paid.
    """
    order_col = mapping.get("orderId")
    paid_col = mapping.get("buyerPaidAmount")
    totals: dict = {}
    if not order_col or not paid_col:
        return totals
    for row in raw_rows:
        order_id = row.get(order_col)
        totals[order_id] = totals.get(order_id, 0.0) + to_number(row.get(paid_col))
    return totals


def _order_fallback_weight_totals(raw_rows: list[dict], mapping: dict) -> dict:
    """Fallback proration denominator for when "Số tiền người mua thanh
    toán" isn't mapped, so _order_paid_totals is empty and Voucher/Phí sàn
    would otherwise always come out 0 for the whole report (order_paid_ratio
    dividing by a missing total). Prorates by "Số lượng" (quantity share)
    instead — summing the resulting ratio back across an order's lines
    reconciles to the order-level total instead of silently losing it.
    "orderId"/"quantity" are both required fields (see mapping.FIELDS), so
    both columns are always present here.
    """
    order_col = mapping["orderId"]
    qty_col = mapping["quantity"]
    totals: dict = {}
    for row in raw_rows:
        order_id = row.get(order_col)
        totals[order_id] = totals.get(order_id, 0.0) + to_number(row.get(qty_col))
    return totals


def build_dashboard_rows(raw_rows: list[dict], mapping: dict) -> list[dict]:
    # "orderId" is validated as required by excel_to_parquet() before this
    # ever runs, so order_col is always truthy here — every row can always
    # be grouped into its real order (Piship/proration below rely on this).
    date_col = mapping.get("date")
    order_col = mapping["orderId"]
    paid_col = mapping.get("buyerPaidAmount")
    order_paid_totals = _order_paid_totals(raw_rows, mapping)
    order_fallback_weight_totals = {} if paid_col else _order_fallback_weight_totals(raw_rows, mapping)
    seen_order_ids: set = set()
    out = []

    for row in raw_rows:
        date = parse_date_value(row.get(date_col)) if date_col else None
        if date is None:
            continue

        derived = derive_row_fields(row, mapping)

        price = to_number(row.get(mapping["price"])) if mapping.get("price") else 0.0
        revenue = to_number(row.get(mapping["revenue"])) if mapping.get("revenue") else None
        if revenue is None and mapping.get("price"):
            revenue = price * derived["quantity"]  # "quantity" is a required field, always mapped
        if revenue is None:
            revenue = 0.0

        seller_subsidy = to_number(row.get(mapping["sellerSubsidy"])) if mapping.get("sellerSubsidy") else 0.0
        shop_voucher = to_number(row.get(mapping["shopVoucher"])) if mapping.get("shopVoucher") else 0.0
        if paid_col:
            order_total_paid = order_paid_totals.get(row.get(order_col))
            line_paid = to_number(row.get(paid_col))
            order_paid_ratio = (line_paid / order_total_paid) if order_total_paid else 0.0
        else:
            # "Số tiền người mua thanh toán" isn't mapped (unlike the
            # required fields, this one legitimately isn't always present
            # — see test_discount_and_voucher_default_to_zero_when_columns_absent)
            # -> prorate by quantity share instead of payment share.
            order_total_weight = order_fallback_weight_totals.get(row.get(order_col))
            line_weight = derived["quantity"]
            order_paid_ratio = (line_weight / order_total_weight) if order_total_weight else 0.0

        discount = compute_discount(seller_subsidy, derived["quantity"], derived["soLuongThuc"])
        voucher = compute_voucher(shop_voucher, order_paid_ratio, derived["quantity"], derived["soLuongThuc"])

        fixed_fee = to_number(row.get(mapping["fixedFee"])) if mapping.get("fixedFee") else 0.0
        service_fee = to_number(row.get(mapping["serviceFee"])) if mapping.get("serviceFee") else 0.0
        transaction_fee = to_number(row.get(mapping["transactionFee"])) if mapping.get("transactionFee") else 0.0
        platform_fee = compute_platform_fee(fixed_fee, service_fee, transaction_fee, order_paid_ratio)

        order_key = row.get(order_col)
        is_first_line_of_order = order_key not in seen_order_ids
        seen_order_ids.add(order_key)
        piship_fee = compute_piship_fee(is_first_line_of_order)

        out.append({
            "date": date,
            "product": _text_or_unknown(row, mapping, "product"),
            "category": _text_or_unknown(row, mapping, "category"),
            "customer": _text_or_unknown(row, mapping, "customer"),
            "quantity": derived["quantity"],
            "price": price,
            "revenue": revenue,
            "status": derived["status"],
            "orderId": _text_or_empty(row, mapping, "orderId"),
            # "sku" (the parent code) is deliberately NOT persisted here:
            # query_engine.py always recomputes it from skuVariant at query
            # time (split_part(..., '-', 1)), because a Combo explosion can
            # swap in a different skuVariant than the one this row derived
            # from — a stored "sku" would go stale in that case, and no
            # query reads it back anyway.
            "skuVariant": derived["skuVariant"],
            "originalPrice": derived["originalPrice"],
            "returnedQty": derived["returnedQty"],
            "soLuongThuc": derived["soLuongThuc"],
            "doanhSo": derived["doanhSo"],
            "trangThai": derived["trangThai"],
            "discount": discount,
            "voucher": voucher,
            "platformFee": platform_fee,
            "piship": piship_fee,
            # Persisted (not just used transiently here) so query_engine.py
            # can later join in order-level values from OTHER reports (e.g.
            # Phí AFF from Dòng tiền) at query time and prorate them the same
            # way Voucher/Phí sàn already are.
            "orderPaidRatio": order_paid_ratio,
        })

    return out


def rows_to_parquet_bytes(rows: list[dict]) -> bytes:
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def get_original_headers(file_like, sheet_name=0) -> list[str]:
    """Just the header row — used by the "Chỉnh cột" UI to offer a dropdown
    of real columns instead of free-text input.
    """
    _, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    return headers


def excel_to_parquet(file_like, sheet_name=0, mapping_override: dict | None = None) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    mapping_override, when given, is used as-is instead of running
    detect_mapping() — this is how PATCH /reports/{id}/mapping actually
    takes effect: the Parquet's columns are fixed at conversion time, so
    changing the mapping means reconverting from the original file with the
    admin's chosen mapping, not just editing stored metadata.
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = dict(mapping_override) if mapping_override else detect_mapping(headers)
    # Drop blank/unset entries so downstream `mapping.get(field)` checks stay falsy.
    mapping = {k: v for k, v in mapping.items() if v}

    # Every field marked required=True in mapping.FIELDS feeds a KPI
    # calculation somewhere downstream (orderId -> Piship/proration,
    # quantity/originalPrice -> Doanh số/GMV, returnedQty -> Doanh số hoàn,
    # status/cancelReason -> trạng thái classification) — silently
    # defaulting any of these to 0/"" produces confidently wrong numbers
    # instead of an obviously missing one, so the upload is rejected
    # instead of guessed at.
    missing = [f.label for f in FIELDS if f.required and f.key not in mapping]
    if missing:
        raise MappingError(f"Thiếu cột bắt buộc trong file: {', '.join(missing)}.")

    dashboard_rows = build_dashboard_rows(raw_rows, mapping)
    if not dashboard_rows:
        raise MappingError("Không có dòng dữ liệu hợp lệ nào (không đọc được ngày ở bất kỳ dòng nào).")

    parquet_bytes = rows_to_parquet_bytes(dashboard_rows)
    return parquet_bytes, len(dashboard_rows), mapping
