from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    role: str
    display_name: str


class MeResponse(BaseModel):
    id: str
    email: str
    role: str
    display_name: str


class ReportOut(BaseModel):
    id: str
    name: str
    uploaded_at: datetime
    uploaded_by: str
    row_count: Optional[int] = None
    status: Literal["processing", "ready", "failed"]
    error_message: Optional[str] = None
    sales_channel_id: Optional[str] = None
    locked: bool = False


class LockUpdateRequest(BaseModel):
    locked: bool


class ReportDetailOut(ReportOut):
    original_filename: str
    sheet_name: Optional[str] = None
    mapping: Optional[dict[str, str]] = None


class ReportCreatedOut(BaseModel):
    id: str
    status: Literal["processing"]


class MappingUpdateRequest(BaseModel):
    mapping: dict[str, str]


class ChannelUpdateRequest(BaseModel):
    sales_channel_id: Optional[str] = None


class SalesChannelCreateRequest(BaseModel):
    name: str


class SalesChannelUpdateRequest(BaseModel):
    name: str


class SalesChannelOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    created_by: str


class InhouseHandleCreateRequest(BaseModel):
    name: str


class InhouseHandleUpdateRequest(BaseModel):
    name: str


class InhouseHandleOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    created_by: str


class KpiOut(BaseModel):
    doanhSo: float
    gmv: float
    huyChuaXK: float
    huySauXK: float
    hoan: float
    discount: float
    voucher: float
    platformFee: float
    piship: float
    phiAff: float
    doanhThuThuan: float
    nmv: float
    giaVon: float
    loiNhuanGop: float
    rowCount: int
    doanhSoOrders: int
    huyChuaXKOrders: int
    huySauXKOrders: int
    hoanOrders: int
    gmvOrders: int
    doanhThuThuanOrders: int
    nmvOrders: int
    pishipOrders: int
    phiAffOrders: int


class SeriesPoint(BaseModel):
    label: str
    value: float


class TimelinePoint(BaseModel):
    month: str
    value: float


class FacetsOut(BaseModel):
    categories: list[str]
    statuses: list[str]
    warehouseTypes: list[str]
    itemGroups: list[str]
    productTypes: list[str]
    salesChannels: list[str]
    kenhNho: list[str]


class SummaryOut(BaseModel):
    kpis: KpiOut
    timeline: list[TimelinePoint]
    topProducts: list[SeriesPoint]
    categoryBreakdown: list[SeriesPoint]
    topCustomers: list[SeriesPoint]
    facets: FacetsOut


class RowsOut(BaseModel):
    rows: list[dict[str, Any]]
    total: int
    page: int
    pageSize: int
