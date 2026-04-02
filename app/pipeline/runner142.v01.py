from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict

from app.pipeline.s3util import S3Config, get_expires_in_seconds, make_random_token, make_timestamp_jst, make_s3_key, upload_html_and_presign

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORIGINALS_DIR = PROJECT_ROOT / "app" / "pipeline" / "originals"

ORIGINAL_SCRIPT = ORIGINALS_DIR / "colab1-4-2.py"  # colab1-4-1.py or colab1-4-2.py


def _run(cmd: list[str], cwd: Path, env: Dict[str, str]) -> str:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            f"cmd={cmd}\n"
            f"returncode={p.returncode}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}\n"
        )
    return p.stdout


def _download_excel(url: str, dst: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "cash-ai-05"})
    with urllib.request.urlopen(req, timeout=60) as r:
        dst.write_bytes(r.read())


def run_html(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入力例:
      {
        "mode": "both"|"141"|"142",
        "ai_case_id": 8888,
        "url": "https://...signed...",
        "s3_bucket": "zlite",
        "s3_region": "ap-northeast-1"
      }

    返却:
      {"runner":"runner141/runner142", "ai_case_id":..., "html_filename":..., "s3_key":..., "html_url":...}
    """
    ai_case_id = payload.get("ai_case_id")
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("payload.url が未指定です（Excelの署名付きURLを指定してください）")

    # 1) 作業ディレクトリ
    run_dir = Path(tempfile.mkdtemp(prefix="cashai04_142_", dir="/tmp"))
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 2) Excelダウンロード
    xlsx_path = work_dir / "input.xlsx"
    _download_excel(url, xlsx_path)

    # 3) HTML生成ファイル名
    ts = make_timestamp_jst()
    token = make_random_token(15)
    html_filename = f"{ai_case_id}-142-{ts}-{token}.html" if ai_case_id else f"142-{ts}-{token}.html"
    html_path = work_dir / html_filename

    # 4) colabスクリプト実行（envで入力/出力を渡す）
    env = dict(os.environ)
    env["INPUT_XLSX"] = str(xlsx_path)
    env["OUTPUT_HTML"] = str(html_path)

    _run(["python3", str(ORIGINAL_SCRIPT)], cwd=work_dir, env=env)

    if not html_path.exists():
        raise RuntimeError("HTMLファイルが生成されませんでした。")

    # 5) S3アップロード（private）+ 署名付きURL
    s3_cfg = S3Config.from_env_and_payload(payload)
    expires_in = get_expires_in_seconds(payload, default_seconds=3600)
    s3_key = make_s3_key(ai_case_id, html_filename, prefix="cash-ai-05")
    s3_key, html_url = upload_html_and_presign(html_path, s3_cfg, s3_key, expires_in)

    return {
        "runner": "runner142",
        "ai_case_id": ai_case_id,
        "html_filename": html_filename,
        "s3_key": s3_key,
        "html_url": html_url,
    }
