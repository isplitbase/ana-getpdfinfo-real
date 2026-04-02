from __future__ import annotations

import os
import random
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

import boto3
from botocore.client import Config


JST = ZoneInfo("Asia/Tokyo")


def make_timestamp_jst() -> str:
    return datetime.now(JST).strftime("%Y%m%d%H%M%S")


def make_random_token(n: int = 15) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


def get_expires_in_seconds(payload: Dict[str, Any], default_seconds: int = 3600) -> int:
    """
    署名付きURLのExpires（秒）を取得。
    payload.expires_sec / payload.expires を優先し、未指定なら default_seconds。
    SigV4 の一般的な上限（7日）を超える値は 604800 秒に丸める。
    """
    raw = payload.get("expires_sec", None)
    if raw is None:
        raw = payload.get("expires", None)

    try:
        seconds = int(raw) if raw is not None else int(default_seconds)
    except Exception:
        seconds = int(default_seconds)

    if seconds <= 0:
        seconds = int(default_seconds)

    # 7 days cap for presigned URL
    return min(seconds, 604800)


@dataclass
class S3Config:
    bucket: str
    region: str
    access_key: str
    secret_key: str

    @staticmethod
    def from_env_and_payload(payload: Dict[str, Any]) -> "S3Config":
        bucket = str(payload.get("s3_bucket") or os.environ.get("S3_BUCKET") or "").strip()
        region = str(payload.get("s3_region") or os.environ.get("S3_REGION") or "").strip() or "ap-northeast-1"

        # ユーザー指定の変数名に合わせる（後で手動定義する想定）
        access_key = str(os.environ.get("S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
        secret_key = str(os.environ.get("S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()

        if not bucket:
            raise ValueError("S3 bucket が未指定です。payload.s3_bucket か環境変数 S3_BUCKET を指定してください。")
        if not access_key or not secret_key:
            raise ValueError("S3 credentials が未指定です。環境変数 S3_ACCESS_KEY / S3_SECRET_KEY を指定してください。")

        return S3Config(bucket=bucket, region=region, access_key=access_key, secret_key=secret_key)


def make_s3_key(ai_case_id: Any, filename: str, prefix: str = "cash-ai-05") -> str:
    """
    仕様: cash-ai-05/<ai_case_id>/<filename>
    """
    case = str(ai_case_id).strip() if ai_case_id is not None else "unknown"
    return f"{prefix}/{case}/{filename}"


def upload_html_and_presign(local_html_path: Path, s3_cfg: S3Config, key: str, expires_in: int) -> Tuple[str, str]:
    """
    HTMLファイルをS3へアップロードし、署名付きURLを返す（publicではない）。
    戻り値: (key, presigned_url)
    """
    client = boto3.client(
        "s3",
        region_name=s3_cfg.region,
        aws_access_key_id=s3_cfg.access_key,
        aws_secret_access_key=s3_cfg.secret_key,
        config=Config(signature_version="s3v4"),
    )

    # デフォルトは private。ACLは明示しない（public化しない）
    client.upload_file(
        str(local_html_path),
        s3_cfg.bucket,
        key,
        ExtraArgs={"ContentType": "text/html; charset=utf-8"},
    )

    presigned = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": s3_cfg.bucket, "Key": key},
        ExpiresIn=int(expires_in),
        HttpMethod="GET",
    )
    return key, presigned
