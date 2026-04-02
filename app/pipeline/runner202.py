from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_SCRIPT = PROJECT_ROOT / "app" / "pipeline" / "originals" / "colab202.py"

# 期待する固定ファイル名（WORK_DIR 配下）
SPEC_FILENAME = "エクセル転記仕様.xlsx"
TEMPLATE_FILENAME = "CF付財務分析表（経営指標あり）_ReadingData.xlsx"


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


def _ensure_work_assets(work_dir: Path) -> None:
    """
    colab202.py が参照する /tmp/work 相当のディレクトリに、
    仕様ExcelとテンプレExcelを配置する。

    既定では /app/app/pipeline/assets/ 以下を探す（Dockerに同梱する想定）。
    """
    assets_dir = PROJECT_ROOT / "app" / "pipeline" / "assets"
    spec_src = assets_dir / SPEC_FILENAME
    tpl_src = assets_dir / TEMPLATE_FILENAME

    missing = []
    if not spec_src.exists():
        missing.append(str(spec_src))
    if not tpl_src.exists():
        missing.append(str(tpl_src))
    if missing:
        raise FileNotFoundError(
            "必要なExcelテンプレ/仕様ファイルが見つかりませんでした。\n"
            "Dockerイメージに同梱するか、起動時に配置してください。\n"
            f"探した場所: {assets_dir}\n"
            f"不足: {missing}"
        )

    (work_dir / SPEC_FILENAME).write_bytes(spec_src.read_bytes())
    (work_dir / TEMPLATE_FILENAME).write_bytes(tpl_src.read_bytes())


def run_colab202(api_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    API入力:
      {"data":[...], "ai_case_id": 123, "loginkey": "..."}
    を受け取り、colab202.py を実行して「更新済みExcel」を返す。
    """
    ai_case_id = api_payload.get("ai_case_id")
    loginkey = api_payload.get("loginkey")

    # 1) 専用の作業ディレクトリ（同時実行でも衝突しない）
    run_dir = Path(tempfile.mkdtemp(prefix="cashai03_202_", dir="/tmp"))
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 2) 必要なExcelファイルを配置（WORK_DIR配下）
    _ensure_work_assets(work_dir)

    # 3) 入力データを output_updated.json として保存（colab202.pyが読む）
    data = api_payload.get("data", api_payload)
    (work_dir / "output_updated.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4) 実行環境
    env = dict(os.environ)
    env["WORK_DIR"] = str(work_dir)

    # 5) 実行
    _run(["python3", str(ORIGINAL_SCRIPT)], cwd=work_dir, env=env)

    # 6) 出力を読み込み（Excel + log）
    out_excel = work_dir / "CF付財務分析表（経営指標あり）_ReadingData_updated.xlsx"
    out_log = work_dir / "transfer_log.txt"

    if not out_excel.exists():
        raise RuntimeError("更新済みExcelが生成されませんでした（colab202.py のログを確認してください）")

    excel_b64 = base64.b64encode(out_excel.read_bytes()).decode("ascii")
    log_text = out_log.read_text(encoding="utf-8", errors="replace") if out_log.exists() else ""

    # 返却（APIで扱いやすい形）
    filename = f"CF付財務分析表_ai_case_{ai_case_id}_202.xlsx" if ai_case_id else "CF付財務分析表_202.xlsx"
    return {
        "runner": "runner202",
        "ai_case_id": ai_case_id,
        "loginkey": loginkey,
        "excel_filename": filename,
        "excel_base64": excel_b64,
        "transfer_log": log_text,
    }
