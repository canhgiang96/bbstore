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
    Field("quantity", "Số lượng"),
    Field("price", "Đơn giá"),
    Field("revenue", "Doanh thu"),
    Field("status", "Trạng thái đơn hàng"),
    Field("orderId", "Mã đơn hàng"),
    Field("skuVariant", "SKU phân loại hàng"),
    Field("originalPrice", "Giá gốc"),
    Field("cancelReason", "Lý do hủy"),
    Field("returnedQty", "SL sản phẩm hoàn trả"),
    Field("sellerSubsidy", "Người bán trợ giá"),
    Field("shopVoucher", "Mã giảm giá của Shop"),
    Field("buyerPaidAmount", "Số tiền người mua thanh toán"),
    Field("fixedFee", "Phí cố định"),
    Field("serviceFee", "Phí dịch vụ"),
    Field("transactionFee", "Phí xử lý giao dịch"),
]

KEYWORDS = {
    "date": ["ngay dat hang", "ngay ban", "ngay giao dich", "order date", "ngay", "date", "thoi gian"],
    "product": ["ten san pham", "ten mat hang", "ten hang", "san pham", "mat hang", "product", "item"],
    "category": ["ten phan loai hang", "danh muc san pham", "danh muc", "phan loai hang", "phan loai", "loai", "nhom", "category"],
    "customer": ["ten khach hang", "khach hang", "khach", "customer"],
    "quantity": ["so luong san pham", "so luong", "qty", "quantity", "sl"],
    "price": ["gia uu dai", "don gia", "gia ban", "gia", "price", "unit price"],
    "revenue": ["tong gia tri don hang", "tong so tien thanh toan", "doanh thu", "thanh tien", "tong tien", "gia tri don hang", "thanh toan", "revenue", "total", "amount", "gia tri"],
    "status": ["trang thai don hang", "trang thai", "status"],
    "orderId": ["ma don hang"],
    "skuVariant": ["sku phan loai hang", "sku phan loai"],
    "originalPrice": ["gia goc"],
    "cancelReason": ["ly do huy"],
    "returnedQty": ["so luong san pham duoc hoan tra", "so luong hoan tra", "sl hoan tra"],
    "sellerSubsidy": ["nguoi ban tro gia"],
    "shopVoucher": ["ma giam gia cua shop", "ma giam gia shop"],
    "buyerPaidAmount": ["so tien nguoi mua thanh toan"],
    "fixedFee": ["phi co dinh"],
    "serviceFee": ["phi dich vu"],
    "transactionFee": ["phi xu ly giao dich"],
}

IDENTIFIER_PREFIX = re.compile(r"^(sku|ma|id)\b")
NAME_LIKE_FIELDS = {"product", "category", "customer"}


def detect_mapping(headers: list[str]) -> dict[str, str]:
    normalized = [(h, normalize_header(h)) for h in headers]
    result: dict[str, str] = {}

    for field, keywords in KEYWORDS.items():
        best_header = None
        best_score = float("-inf")
        for h, n in normalized:
            is_identifier = bool(IDENTIFIER_PREFIX.match(n))
            for w in keywords:
                if n == w:
                    score = 100 + len(w)
                elif w in n:
                    score = len(w)
                else:
                    continue
                if is_identifier and field in NAME_LIKE_FIELDS:
                    score -= 50
                if score > best_score:
                    best_score = score
                    best_header = h
        if best_header is not None and best_score > 0:
            result[field] = best_header

    return result
