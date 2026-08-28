"""Column auto-detection for uploaded Orders Excel files.

This is a line-by-line port of the client-side logic in
../../js/app.js (stripDiacritics, normalizeHeader, FIELDS, KEYWORDS,
IDENTIFIER_PREFIX, NAME_LIKE_FIELDS, detectMapping). Keep it in sync with
that file rather than "improving" it independently — the two must agree on
which column a given Vietnamese header maps to.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def strip_diacritics(s: str) -> str:
    s = str(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub("[đĐ]", "d", s)
    return s.lower().strip()


def normalize_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_diacritics(h)).strip()


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool = False


FIELDS = [
    Field("date", "Ngày", required=True),
    Field("product", "Sản phẩm"),
    Field("category", "Danh mục"),
    Field("customer", "Khách hàng"),
    Field("quantity", "Số lượng", required=True),
    Field("price", "Đơn giá"),
    Field("revenue", "Doanh thu"),
    # status/originalPrice/cancelReason are no longer unconditionally
    # required=True here — a file with no order-status concept at all (e.g.
    # the in-house POS/social/web/Zalo export, confirmed with the user
    # 2026-08-28: "các dòng trong file không có đơn hủy") has none of these
    # three columns, yet is still a valid Orders upload. The real
    # requiredness is enforced conditionally in excel_to_parquet() instead:
    # when "status" IS mapped (Shopee/TikTok-shaped), originalPrice/
    # cancelReason are still required (status-based derivation depends on
    # them); when it isn't, "revenue" becomes the required stand-in for
    # originalPrice instead (see derive_row_fields' fallback) and
    # cancelReason is simply unused (every row defaults to "Hoàn thành" —
    # see derive_order_status's status_known param).
    Field("status", "Trạng thái đơn hàng"),
    Field("orderId", "Mã đơn hàng", required=True),
    Field("skuVariant", "SKU phân loại hàng"),
    Field("originalPrice", "Giá gốc"),
    Field("cancelReason", "Lý do hủy"),
    Field("returnedQty", "SL sản phẩm hoàn trả", required=True),
    Field("sellerSubsidy", "Người bán trợ giá"),
    Field("shopVoucher", "Mã giảm giá của Shop"),
    Field("buyerPaidAmount", "Số tiền người mua thanh toán"),
    Field("fixedFee", "Phí cố định"),
    Field("serviceFee", "Phí dịch vụ"),
    Field("transactionFee", "Phí xử lý giao dịch"),
    # TikTok-only, optional — feed the Dashboard's "Kênh nhỏ" (LIVE/VIDEO/
    # PSA/AFF) classification (see derive.classify_kenh_nho). skuId is
    # TikTok's own internal numeric SKU id — NOT the same value space as
    # skuVariant/Seller SKU above — it's the join key against the Kênh AFF
    # Report (app/aff_channel_to_parquet.py), confirmed against real files
    # 2026-08-27 (Seller SKU "V3609-2" vs SKU ID "1730315401307982614" for
    # the same order line — only SKU ID matches the Kênh AFF export).
    Field("skuId", "SKU ID"),
    Field("creatorHandle", "Người sáng tạo (Handle)"),
    Field("contentChannel", "Kênh nội dung"),
    # In-house channels (31 LVS/HARA/WEBSITE/ZALO) share one combined file
    # structure and mark each row's channel with this column — confirmed
    # with the user 2026-08-28 (real file sale_report_28_08_2026_927871_1)
    # so all 4 can be uploaded together as one Report instead of 4 separate
    # ones. Optional — feeds a per-row Kênh bán hàng override (see
    # derive.normalize_inhouse_channel / query_engine._build_orders_working)
    # instead of the usual per-Report sales_channel_id tagging.
    Field("channelRaw", "Kênh bán hàng (trong file)"),
    Field("discountAmount", "Giảm giá (số tiền, đã tính sẵn theo dòng)"),
    Field("refundAmount", "Số tiền hoàn trả (đã tính sẵn theo dòng)"),
]

KEYWORDS = {
    # Each field's keyword list mixes Shopee's Vietnamese headers with
    # TikTok Shop's English ones — detect_mapping just picks whichever
    # scores best per file, and since the two languages never share
    # substrings there's no cross-channel collision risk from mixing them
    # into one list (see the TikTok sample analyzed 2026-08-26).
    "date": ["ngay dat hang", "ngay ban", "ngay giao dich", "order date", "created time", "ngay", "date", "thoi gian"],
    "product": ["ten san pham", "ten mat hang", "ten hang", "san pham", "mat hang", "product", "item"],
    "category": ["ten phan loai hang", "danh muc san pham", "danh muc", "phan loai hang", "phan loai", "loai", "nhom", "category"],
    "customer": ["ten khach hang", "khach hang", "khach", "customer"],
    "quantity": ["so luong san pham", "so san pham", "so luong", "qty", "quantity", "sl"],
    "price": ["gia uu dai", "don gia", "gia ban", "gia", "price", "unit price"],
    "revenue": ["tong gia tri don hang", "tong so tien thanh toan", "doanh thu", "thanh tien", "tong tien", "gia tri don hang", "thanh toan", "revenue", "total", "amount", "gia tri"],
    "status": ["trang thai don hang", "trang thai", "order status", "status"],
    "orderId": ["ma don hang", "order id"],
    "skuVariant": ["sku phan loai hang", "sku phan loai", "seller sku", "sku"],
    "originalPrice": ["gia goc", "sku unit original price"],
    "cancelReason": ["ly do huy", "cancel reason"],
    "returnedQty": ["so luong san pham duoc hoan tra", "so san pham tra", "so luong hoan tra", "sl hoan tra", "sku quantity of return"],
    "sellerSubsidy": ["nguoi ban tro gia", "sku seller discount"],
    "shopVoucher": ["ma giam gia cua shop", "ma giam gia shop"],
    "buyerPaidAmount": ["so tien nguoi mua thanh toan"],
    "fixedFee": ["phi co dinh"],
    "serviceFee": ["phi dich vu"],
    "transactionFee": ["phi xu ly giao dich"],
    "skuId": ["sku id"],
    "creatorHandle": ["creator handle"],
    "contentChannel": ["order channel"],
    "channelRaw": ["kenh ban hang"],
    "discountAmount": ["giam gia"],
    "refundAmount": ["hoan tra"],
}

IDENTIFIER_PREFIX = re.compile(r"^(sku|ma|id)\b")
NAME_LIKE_FIELDS = {"product", "category", "customer"}


def score_headers(headers: list[str], keywords: dict[str, list[str]], score_adjust=None) -> dict[str, str]:
    """Exact-match-priority header scoring: for each field, picks the
    header whose normalized text scores highest against that field's
    keyword list (an exact normalized match beats a substring match;
    longer keyword wins ties within the same match type).

    Shared by detect_mapping below and by the Master File / Điều chỉnh
    doanh thu importers (app.master_to_parquet, app.adjustments_to_parquet)
    — all three need this real scoring, not just "first match wins", to
    disambiguate a header that's a substring of another (e.g. "SKU" inside
    "SKU phân loại"). Combo/Cashflow don't have that collision and use the
    simpler first_match_mapping instead.

    score_adjust, if given, is called as score_adjust(field, header,
    normalized_header, score) -> adjusted_score for a field-specific
    penalty/bonus (detect_mapping uses this to penalize identifier-looking
    headers for name-like fields).
    """
    normalized = [(h, normalize_header(h)) for h in headers]
    result: dict[str, str] = {}

    for field, words in keywords.items():
        best_header = None
        best_score = float("-inf")
        for h, n in normalized:
            for w in words:
                if n == w:
                    score = 100 + len(w)
                elif w in n:
                    score = len(w)
                else:
                    continue
                if score_adjust is not None:
                    score = score_adjust(field, h, n, score)
                if score > best_score:
                    best_score = score
                    best_header = h
        if best_header is not None and best_score > 0:
            result[field] = best_header

    return result


def first_match_mapping(headers: list[str], keywords: dict[str, list[str]]) -> dict[str, str]:
    """Simpler "first header, in file order, that exact-matches or contains
    a field's keyword wins" detector — used by Cashflow and Combo, whose
    headers don't have Master File-style substring collisions (see
    score_headers's docstring for when that's not safe).
    """
    normalized = [(h, normalize_header(h)) for h in headers]
    result: dict[str, str] = {}
    for field, words in keywords.items():
        for h, n in normalized:
            for w in words:
                if n == w or w in n:
                    result[field] = h
                    break
            if field in result:
                break
    return result


# These 3 fields' keywords are short/generic enough to falsely substring-
# match an unrelated, longer real header — "giam gia" (discountAmount)
# inside "Mã giảm giá của Shop" (shopVoucher's actual header), "hoan tra"
# (refundAmount) inside "Số lượng sản phẩm được hoàn trả" (returnedQty's).
# Require an exact normalized match (score >= 100) for these, or don't map
# at all, rather than risk binding to the wrong column on a Shopee/TikTok
# file that happens to contain the substring.
EXACT_MATCH_ONLY_FIELDS = {"discountAmount", "refundAmount", "channelRaw"}


def _detect_mapping_score_adjust(field, header, normalized_header, score):
    if field in NAME_LIKE_FIELDS and IDENTIFIER_PREFIX.match(normalized_header):
        return score - 50
    if field in EXACT_MATCH_ONLY_FIELDS and score < 100:
        return 0
    return score


def detect_mapping(headers: list[str]) -> dict[str, str]:
    return score_headers(headers, KEYWORDS, score_adjust=_detect_mapping_score_adjust)
