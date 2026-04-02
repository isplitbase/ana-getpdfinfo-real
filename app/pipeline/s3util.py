from __future__ import annotations

import os
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

import google.auth
import google.auth.transport.requests
from google.cloud import storage as gcs_storage


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
    GCS v4 署名付きURLの上限（7日）を超える値は 604800 秒に丸める。
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

    # 7 days cap for signed URL
    return min(seconds, 604800)


@dataclass
class GCSConfig:
    bucket: str

    @staticmethod
    def from_env_and_payload(payload: Dict[str, Any]) -> "GCSConfig":
        bucket = str(payload.get("gcs_bucket") or os.environ.get("GCS_BUCKET") or "").strip()

        if not bucket:
            raise ValueError("GCS bucket が未指定です。payload.gcs_bucket か環境変数 GCS_BUCKET を指定してください。")

        return GCSConfig(bucket=bucket)


# 後方互換エイリアス（runner141/142 が S3Config という名前で import しているため）
S3Config = GCSConfig


def make_s3_key(ai_case_id: Any, filename: str, prefix: str = "cash-ai-05") -> str:
    """
    仕様: cash-ai-05/<ai_case_id>/<filename>
    （関数名は後方互換のため維持）
    """
    case = str(ai_case_id).strip() if ai_case_id is not None else "unknown"
    return f"{prefix}/{case}/{filename}"


def upload_html_and_presign(local_html_path: Path, gcs_cfg: GCSConfig, key: str, expires_in: int) -> Tuple[str, str]:
    """
    HTMLファイルをGCSへアップロードし、署名付きURLを返す。
    戻り値: (key, presigned_url)
    """
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    client = gcs_storage.Client(credentials=credentials)
    bucket = client.bucket(gcs_cfg.bucket)
    blob = bucket.blob(key)

    blob.upload_from_filename(
        str(local_html_path),
        content_type="text/html; charset=utf-8",
    )

    service_account_email = getattr(credentials, "service_account_email", None)
    if service_account_email:
        # サービスアカウントキーまたは Workload Identity で署名
        signed_url = blob.generate_signed_url(
            expiration=timedelta(seconds=expires_in),
            method="GET",
            version="v4",
            service_account_email=service_account_email,
            access_token=credentials.token,
        )
    else:
        # フォールバック: 公開URLを返す（署名不可環境）
        signed_url = blob.public_url

    return key, signed_url
