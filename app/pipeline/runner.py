from __future__ import annotations
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORIGINALS_DIR = PROJECT_ROOT / "app" / "pipeline" / "originals"


def _run(cmd: list[str], cwd: Path, env: Dict[str, str]) -> None:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n--- output ---\n{p.stdout}")


def run_001_002_003(payload: Dict[str, Any]) -> Dict[str, Any]:
    data_json = {
        "BS": payload.get("BS", []),
        "PL": payload.get("PL", []),
        "販売費": payload.get("SGA", []),
        "製造原価": payload.get("MFG", []),
    }

    run_dir = Path(tempfile.mkdtemp(prefix="cashai_", dir="/tmp"))
    (run_dir / "data.json").write_text(json.dumps(data_json, ensure_ascii=False), encoding="utf-8")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    env = dict(os.environ)
    if api_key:
        env["OPENAI_API_KEY2"] = api_key

    env["PYTHONPATH"] = str(PROJECT_ROOT)

    _run(["python3", str(ORIGINALS_DIR / "cloab001.py")], cwd=run_dir, env=env)
    _run(["python3", str(ORIGINALS_DIR / "cloab002.py")], cwd=run_dir, env=env)
    _run(["python3", str(ORIGINALS_DIR / "cloab003.py")], cwd=run_dir, env=env)

    out_path = run_dir / "output_updated.json"
    if not out_path.exists():
        out_path = run_dir / "output.json"
    if not out_path.exists():
        raise RuntimeError("output_updated.json / output.json が生成されませんでした。")

    return json.loads(out_path.read_text(encoding="utf-8"))


def run_getpdfinfo(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入力例:
      {"files": ["s3://zlite/...pdf", "s3://zlite/...pdf"], "file_names": ["a.pdf", "b.pdf"]}
      {"file": "s3://zlite/...pdf"}

    出力:
      {"result_json": {... financial_data.json 相当 ...}, ...}
    """
    files = payload.get("files")
    if files is None:
        files = payload.get("file", [])

    if isinstance(files, str):
        files = [files]

    if not isinstance(files, list) or not files:
        raise ValueError("payload.files が未指定です")

    file_names = payload.get("file_names")
    if file_names is None:
        file_names = payload.get("filenames")

    if file_names is None:
        file_names = []
    elif isinstance(file_names, str):
        file_names = [file_names]
    elif not isinstance(file_names, list):
        raise ValueError("payload.file_names は配列で指定してください")

    normalized: list[str] = []
    for i, f in enumerate(files):
        if not isinstance(f, str):
            raise ValueError(f"payload.files[{i}] が文字列ではありません: {f!r}")
        f = f.strip()
        if not f:
            raise ValueError(f"payload.files[{i}] が空です")
        if not f.startswith("s3://"):
            raise ValueError(f"payload.files[{i}] は s3:// 形式ではありません: {f}")
        normalized.append(f)

    normalized_names: list[str] = []
    for i, name in enumerate(file_names):
        if name is None:
            normalized_names.append("")
            continue
        if not isinstance(name, str):
            raise ValueError(f"payload.file_names[{i}] が文字列ではありません: {name!r}")
        normalized_names.append(name)

    if normalized_names and len(normalized_names) != len(normalized):
        raise ValueError("payload.file_names の件数は payload.files と同じにしてください")

    from app.pipeline.originals.getpdfinfo11 import run_getpdfinfo as _run_getpdfinfo_original

    try:
        result = _run_getpdfinfo_original(normalized, normalized_names)
        return {
            "result_json": result.get("result_json"),
            "logs": result.get("logs", []),
            "apimessage": result.get("apimessage", []),
            "company_warning": result.get("company_warning"),
            "position_warnings": result.get("position_warnings", []),
            "period_mapping": result.get("period_mapping", []),
        }
    except Exception as e:
        raise RuntimeError(f"getpdfinfo11 実行失敗: {e}") from e
