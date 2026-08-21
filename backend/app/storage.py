"""Cloudflare R2 (S3-compatible) client — stores original.xlsx and
data.parquet per Report, under reports/<report_id>/.
"""
from __future__ import annotations

import boto3

from .config import get_settings


def _client():
    s = get_settings()
    endpoint = f"https://{s.r2_account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        region_name="auto",
    )


def original_key(report_id: str, filename: str) -> str:
    return f"reports/{report_id}/original.xlsx"


def parquet_key(report_id: str) -> str:
    return f"reports/{report_id}/data.parquet"


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    s = get_settings()
    _client().put_object(Bucket=s.r2_bucket_name, Key=key, Body=data, ContentType=content_type)


def download_to_path(key: str, local_path: str) -> None:
    s = get_settings()
    _client().download_file(s.r2_bucket_name, key, local_path)


def delete_objects(keys: list) -> None:
    s = get_settings()
    keys = [k for k in keys if k]
    if not keys:
        return
    _client().delete_objects(Bucket=s.r2_bucket_name, Delete={"Objects": [{"Key": k} for k in keys]})
