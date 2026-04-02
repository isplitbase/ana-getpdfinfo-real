"""
getpdfinfo11.new.py (Cloud Run / API互換版)

目的:
- getpdfinfo11.py の run_getpdfinfo() と同じ入出力に寄せる
- ただし判定ロジックは getpdfinfo11.new.py の「複数PDFを1回でGeminiへ送る」方式を使う
- apimessage も getpdfinfo11.py と同様に記録する
- reason を result_json に必ず含める
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import boto3
from botocore.client import Config
from pdf2image import convert_from_path
from zoneinfo import ZoneInfo

# NOTE:
# This repository contains a local "/app/google" stub package for old Colab helpers.
# It shadows the real "google-genai" package on Cloud Run, so we must import
# google.genai only after temporarily removing the project root from sys.path.
import importlib
import sys

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
_removed_sys_path = []
for _p in list(sys.path):
    if _p in ("", _PROJECT_ROOT, "/app"):
        _removed_sys_path.append(_p)
        try:
            sys.path.remove(_p)
        except ValueError:
            pass

_google_mod = sys.modules.get("google")
if _google_mod is not None and str(getattr(_google_mod, "__file__", "")).startswith(_PROJECT_ROOT + "/google"):
    del sys.modules["google"]

genai = importlib.import_module("google.genai")
genai_types = importlib.import_module("google.genai.types")

for _p in reversed(_removed_sys_path):
    if _p not in sys.path:
        sys.path.insert(0, _p)


MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


# ────────────────────────────────
# ログユーティリティ
# ────────────────────────────────
def _now_hms() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%H:%M:%S")


def _format_apimessage(msg: str) -> str:
    return f"[{_now_hms()}] {msg}"


# ────────────────────────────────
# Geminiプロンプト
# ────────────────────────────────
def build_meta_prompt(pdf_infos: list) -> str:
    """
    pdf_infos = [
      {"index": 1, "file_name": "A.pdf"},
      {"index": 2, "file_name": "B.pdf"},
      ...
    ]
    """
    file_list_text = "\n".join(
        [f"- PDF{p['index']} = {p['file_name']}" for p in pdf_infos]
    )

    return f"""
あなたは日本の決算書を読み取る専門家です。

これから複数のPDFが送られます。
各PDFには番号とファイル名があります。
必ずその対応関係を守って判定してください。

PDF一覧:
{file_list_text}

目的:
各PDFが「今期」「前期」「前々期」「前々期の前期」のどれに該当するか判定してください。

特別パターンサンプル1：
　1枚目PDF（pdf1.pdf）：[N]年度と[N+1]年度の情報が含まれている
　2枚目PDF（pdf2.pdf）：[N+2]年度と[N+3]度の情報が含まれている
　の場合
　1枚目PDF（pdf1.pdf）：「前々期」/「前々期の前期」
　2枚目PDF（pdf2.pdf）：「今期」/「前期」

特別パターンサンプル2：
　1枚目PDF（pdf1.pdf）：令和6年9月の貸借対照表と損益計算書が含まれている
　2枚目PDF（pdf2.pdf）：令和6年12月の貸借対照表と損益計算書が含まれている
　3枚目PDF（pdf3.pdf）：令和7年9月の貸借対照表と損益計算書が含まれている
　の場合
　1枚目PDF（pdf1.pdf）：「前々期」
　2枚目PDF（pdf2.pdf）：「前期」
　3枚目PDF（pdf3.pdf）：「今期」

特別パターンサンプル3：
　1枚目PDF（pdf1.pdf）：令和6年1月の貸借対照表と損益計算書が含まれている
　2枚目PDF（pdf2.pdf）：令和6年9月の貸借対照表と損益計算書が含まれている
　3枚目PDF（pdf3.pdf）：令和7年9月の貸借対照表と損益計算書が含まれている
　の場合
　1枚目PDF（pdf1.pdf）：「前々期」
　2枚目PDF（pdf2.pdf）：「前期」
　3枚目PDF（pdf3.pdf）：「今期」

「プロンプトの特別パターンサンプルはあくまで形式を教えるためのものであり、日付などの事実は必ずPDF内の記載から抽出すること」

重要ルール:
- 各PDFは同じ企業の決算書です。
- 各PDFは複数期を含む可能性があります。
- 販売費及び一般管理費の資料を見ないでください。
- たな卸資産の資料を見ないでください。
- 製造原価の資料を見ないでください。
- ファイル数は1且つ1年度の情報しかないの場合、必ず今期になる。
- その場合は、そのPDFに含まれる期ラベルをすべて列挙してください。
- PDF番号とファイル名を取り違えないでください。
- 出力は必ずJSONのみを返してください。説明文やMarkdownは不要です。
- 決算情報がある年度をすべて出してください。
- 「今期」「前期」「前々期」「前々期の前期」をすべてのPDFをトータル見て判断してください
- すべてのPDFの中の最新決算期間は「今期」です
- ラベルは必ず次の4種類のみを使ってください: ["今期", "前期", "前々期", "前々期の前期"]
- 必ず各PDFごとに reason を返してください
- reason には、どの年度・決算年月・相対比較によりそのラベルになったかを簡潔に書いてください
- 順番は、送信されたPDF順（PDF1, PDF2, PDF3, ...）で返してください。

出力JSON形式:
{{
  "results": [
    {{
      "pdf_index": 1,
      "file_name": "A.pdf",
      "labels": ["今期"],
      "reason": "令和7年9月期が全PDF中で最新のため今期",
      "年度": ["令和7年度"]
    }},
    {{
      "pdf_index": 2,
      "file_name": "B.pdf",
      "labels": ["前期"],
      "reason": "令和6年9月期で、全PDF中の最新期の1期前に当たるため前期",
      "年度": ["令和6年度"]
    }}
  ]
}}
""".strip()


# ────────────────────────────────
# Gemini API: JSON抽出ヘルパー（リトライ付き）
# ────────────────────────────────
def _extract_json_text(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def _call_gemini_json(client: genai.Client, contents: list, max_tokens: int = 4000) -> dict:
    import time as _time

    MAX_RETRIES = 3
    last_err = None

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                ),
            )

            text = None
            try:
                text = response.text
            except Exception:
                pass

            if not text:
                try:
                    text = response.candidates[0].content.parts[0].text
                except Exception:
                    pass

            if not text:
                raise ValueError("Gemini APIレスポンス取得失敗")

            text = _extract_json_text(text)
            data = json.loads(text)

            if not isinstance(data, dict):
                raise ValueError("GeminiのJSON応答がdictではありません")

            if "results" not in data or not isinstance(data["results"], list):
                raise ValueError("GeminiのJSON応答に results がありません")

            return data

        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"[WARN] Gemini API retry {attempt + 1}/{MAX_RETRIES}: {e}")
            _time.sleep(wait)

    raise RuntimeError(f"Gemini API {MAX_RETRIES}回失敗: {last_err}")


# ────────────────────────────────
# PDFまとめ解析
# ────────────────────────────────
def analyze_multiple_pdfs_with_gemini(client: genai.Client, pdf_paths: list, file_names: list) -> dict:
    """
    すべてのPDFをまとめて1回でGeminiへ送る。
    PDF番号・ファイル名を明示することで、どのPDFがどの判定かを安定化する。
    """
    if len(pdf_paths) != len(file_names):
        raise ValueError("pdf_paths と file_names の数が一致しません")

    pdf_infos = []
    for i, file_name in enumerate(file_names, start=1):
        pdf_infos.append({
            "index": i,
            "file_name": file_name,
        })

    prompt = build_meta_prompt(pdf_infos)
    contents = [prompt]

    for i, pdf_path in enumerate(pdf_paths, start=1):
        file_name = file_names[i - 1]
        contents.append(f"以下が PDF{i} / ファイル名: {file_name} です。")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        contents.append(
            genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type="application/pdf",
                    data=pdf_bytes,
                )
            )
        )

    result = _call_gemini_json(client, contents)

    results = result.get("results", [])
    results = sorted(results, key=lambda x: x.get("pdf_index", 9999))
    result["results"] = results
    return result


# ────────────────────────────────
# 表示用
# ────────────────────────────────
def build_display_text(result_json: dict) -> str:
    lines = []
    for item in result_json.get("results", []):
        pdf_index = item.get("pdf_index", "")
        file_name = item.get("file_name", "")
        labels = item.get("labels", [])
        reason = item.get("reason", "")

        if not isinstance(labels, list):
            labels = [str(labels)]

        label_text = " / ".join([str(x) for x in labels]) if labels else "不明"

        lines.append(f"{pdf_index}枚目PDF（{file_name}）：{label_text}")
        if reason:
            lines.append(f"  理由: {reason}")
        lines.append("")

    return "\n".join(lines).strip()


# ────────────────────────────────
# S3 / ファイル補助
# ────────────────────────────────
def _parse_s3_url(url: str) -> Tuple[str, str]:
    if not url.startswith("s3://"):
        raise ValueError(f"s3:// 形式ではありません: {url}")
    rest = url[len("s3://") :]
    if "/" not in rest:
        raise ValueError(f"s3://bucket/key 形式ではありません: {url}")
    bucket, key = rest.split("/", 1)
    return bucket, key


def download_s3_to_dir(s3_urls: List[str], out_dir: Path) -> List[Path]:
    access_key = str(os.environ.get("S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = str(os.environ.get("S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    region = str(os.environ.get("S3_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-1").strip()

    if not access_key or not secret_key:
        raise ValueError("S3 credentials が未指定です。環境変数 S3_ACCESS_KEY / S3_SECRET_KEY を指定してください。")

    client = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )

    local_paths: List[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for url in s3_urls:
        bucket, key = _parse_s3_url(url)
        filename = Path(key).name or "input.pdf"
        local_path = out_dir / filename
        client.download_file(bucket, key, str(local_path))
        local_paths.append(local_path)

    return local_paths


def normalize_pdf_inplace(pdf_path: Path) -> None:
    gs_exe = shutil.which("gs") or shutil.which("gswin64c.exe")
    if not gs_exe:
        return

    norm_path = pdf_path.with_suffix(pdf_path.suffix + ".norm.pdf")
    cmd = [
        gs_exe,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        "-dNOPAUSE",
        "-dBATCH",
        "-dSAFER",
        "-dFIXEDMEDIA",
        "-sPAPERSIZE=a4",
        "-dPDFFitPage",
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        "-dDownsampleColorImages=true",
        "-dColorImageDownsampleType=/Average",
        "-dColorImageResolution=150",
        "-dDownsampleGrayImages=true",
        "-dGrayImageDownsampleType=/Average",
        "-dGrayImageResolution=150",
        "-dDownsampleMonoImages=true",
        "-dMonoImageDownsampleType=/Subsample",
        "-dMonoImageResolution=150",
        f"-sOutputFile={str(norm_path)}",
        str(pdf_path),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,   # ここを変更
    )
    if proc.returncode == 0 and norm_path.exists():
        shutil.move(str(norm_path), str(pdf_path))


def get_pdf_page_count(pdf_path: str) -> int:
    pages = convert_from_path(pdf_path, dpi=72)
    return len(pages)


def _s3_display_name_from_url(url: str) -> str:
    try:
        _, key = _parse_s3_url(url)
        return Path(key).name
    except Exception:
        return Path(url).name or url


def _strip_pdf_suffix(name: str) -> str:
    return name[:-4] if name.lower().endswith(".pdf") else name


def _build_display_name_map(files: List[str], file_names: List[str] | None) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not file_names:
        return mapping

    for i, s3_url in enumerate(files):
        if i >= len(file_names):
            break
        original = (file_names[i] or "").strip()
        if not original:
            continue
        s3_name = _s3_display_name_from_url(s3_url)
        mapping[_strip_pdf_suffix(s3_name)] = _strip_pdf_suffix(Path(original).name)
    return mapping


def _replace_display_names_in_results(result_json: Dict[str, Any], display_name_map: Dict[str, str]) -> Dict[str, Any]:
    if not display_name_map:
        return result_json

    for item in result_json.get("results", []) or []:
        file_name = item.get("file_name")
        if not isinstance(file_name, str):
            continue

        stem = _strip_pdf_suffix(file_name)
        if stem in display_name_map:
            item["file_name"] = display_name_map[stem] + ".pdf"

    return result_json


def _replace_display_names_in_period_mapping(period_mapping: List[Dict[str, Any]], display_name_map: Dict[str, str]) -> List[Dict[str, Any]]:
    if not display_name_map:
        return period_mapping

    for item in period_mapping or []:
        file_name = item.get("file_name")
        if not isinstance(file_name, str):
            continue

        stem = _strip_pdf_suffix(file_name)
        if stem in display_name_map:
            item["file_name"] = display_name_map[stem] + ".pdf"

    return period_mapping


def _replace_display_names_in_logs(logs: List[Dict[str, str]], display_name_map: Dict[str, str]) -> List[Dict[str, str]]:
    if not display_name_map:
        return logs

    items = sorted(display_name_map.items(), key=lambda kv: len(kv[0]), reverse=True)

    for log in logs or []:
        msg = log.get("msg")
        if not isinstance(msg, str):
            continue
        for s3_name, original_name in items:
            msg = msg.replace(s3_name + ".pdf", original_name + ".pdf")
            msg = msg.replace(s3_name, original_name)
        log["msg"] = msg
    return logs


def _replace_display_names_in_apimessages(apimessages: List[str], display_name_map: Dict[str, str]) -> List[str]:
    if not display_name_map:
        return apimessages

    items = sorted(display_name_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    out: List[str] = []
    for msg in apimessages or []:
        if not isinstance(msg, str):
            continue
        replaced = msg
        for s3_name, original_name in items:
            replaced = replaced.replace(s3_name + ".pdf", original_name + ".pdf")
            replaced = replaced.replace(s3_name, original_name)
        out.append(replaced)
    return out


# ────────────────────────────────
# result_json の補助生成
# ────────────────────────────────
def _normalize_years_field(year_value: Any) -> List[str]:
    if year_value is None:
        return []
    if isinstance(year_value, list):
        return [str(x).strip() for x in year_value if str(x).strip()]
    s = str(year_value).strip()
    return [s] if s else []


def _normalize_labels_field(label_value: Any) -> List[str]:
    if label_value is None:
        return []
    if isinstance(label_value, list):
        return [str(x).strip() for x in label_value if str(x).strip()]
    s = str(label_value).strip()
    return [s] if s else []


def _extract_latest_year_int(item: Dict[str, Any]) -> int | None:
    import re

    years_raw = item.get("年度", "")
    candidates = years_raw if isinstance(years_raw, list) else [years_raw]

    nums = []
    for y in candidates:
        if not y:
            continue
        s = str(y)
        found = re.findall(r"\d+", s)
        for n in found:
            try:
                nums.append(int(n))
            except Exception:
                pass

    return max(nums) if nums else None


def _apply_two_file_gap_rule(result_json: Dict[str, Any]) -> None:
    """
    2ファイルのみで、最新年度差が2年の場合は古い側を1段古く補正する。
      前期   -> 前々期
      前々期 -> 前々期の前期
    """
    results = result_json.get("results", [])
    if len(results) != 2:
        return

    year1 = _extract_latest_year_int(results[0])
    year2 = _extract_latest_year_int(results[1])

    if year1 is None or year2 is None:
        return

    if abs(year1 - year2) != 2:
        return

    older_item = results[0] if year1 < year2 else results[1]
    labels = _normalize_labels_field(older_item.get("labels", []))

    replaced_labels = []
    for label in labels:
        if label == "前期":
            replaced_labels.append("前々期")
        elif label == "前々期":
            replaced_labels.append("前々期の前期")
        else:
            replaced_labels.append(label)

    older_item["labels"] = replaced_labels

    reason = str(older_item.get("reason", "") or "").strip()
    extra = "2ファイル構成で最新年度差が2年のため、古いPDF側のラベルを1段古い期へ補正"
    older_item["reason"] = f"{reason} / {extra}" if reason else extra


def build_period_mapping_from_result(result_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    旧 getpdfinfo11.py の戻り値に含まれていた period_mapping 互換の簡易版。
    """
    period_mapping: List[Dict[str, Any]] = []

    for item in result_json.get("results", []) or []:
        file_name = item.get("file_name", "")
        labels = _normalize_labels_field(item.get("labels", []))
        years = _normalize_years_field(item.get("年度", []))
        reason = str(item.get("reason", "") or "")

        for i, label in enumerate(labels):
            year_text = years[i] if i < len(years) else (years[0] if years else "")
            period_mapping.append({
                "label": label,
                "fiscal_end_date": "",
                "start_date": "",
                "end_date": "",
                "fiscal_period_name": year_text,
                "file_name": file_name,
                "column_header": "",
                "detection_basis": "gemini_total_judgement",
                "reason": reason,
            })

    return period_mapping


# ────────────────────────────────
# メイン
# ────────────────────────────────
def run_getpdfinfo(files: List[str], file_names: List[str] | None = None) -> Dict[str, Any]:
    """
    旧 getpdfinfo11.py 互換の戻り値:
    {
        "result_json": ...,
        "display_text": ...,
        "logs": ...,
        "apimessage": ...,
        "company_warning": None,
        "position_warnings": [],
        "period_mapping": ...
    }
    """
    api_key = str(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("Gemini APIキーが未指定です。環境変数 GEMINI_API_KEY を指定してください。")

    client = genai.Client(api_key=api_key)

    run_dir = Path(tempfile.mkdtemp(prefix="zlite_getpdfinfo_new_", dir="/tmp"))
    in_dir = run_dir / "input"
    out_dir = run_dir / "output" / "json"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    logs: List[Dict[str, str]] = []
    apimessages: List[str] = []
    position_warnings: List[str] = []

    def log(msg: str, t: str = "info"):
        logs.append({"msg": msg, "type": t})
        apimessages.append(_format_apimessage(msg))
        print(f"[{t.upper()}] {msg}")
    def _apply_single_file_two_year_labels_rule(result_json: Dict[str, Any]) -> None:
        """
        アップロードファイルが1件だけで、そのPDFに年度が2要素ある場合は
        labels を ["今期", "前期"] に補正する。
        """
        results = result_json.get("results", [])
        if len(results) != 1:
            return

        item = results[0]
        years = _normalize_years_field(item.get("年度", []))

        if len(years) != 2:
            return

        item["labels"] = ["今期", "前期"]

        reason = str(item.get("reason", "") or "").strip()
        extra = "アップロードファイルが1件のみで、同一PDF内に年度が2要素あるため、labels を『今期』『前期』に補正"
        item["reason"] = f"{reason} / {extra}" if reason else extra
    for url in files:
        display_name = _s3_display_name_from_url(url)
        try:
            bucket, key = _parse_s3_url(url)
            s3_client = boto3.client(
                "s3",
                region_name=str(os.environ.get("S3_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-1").strip(),
                aws_access_key_id=str(os.environ.get("S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or "").strip(),
                aws_secret_access_key=str(os.environ.get("S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip(),
                config=Config(signature_version="s3v4"),
            )
            head = s3_client.head_object(Bucket=bucket, Key=key)
            size_mb = (head.get("ContentLength", 0) or 0) / 1024 / 1024
            log(f"📄 {display_name} を転送中... ({size_mb:.1f}MB)")
        except Exception:
            log(f"📄 {display_name} を転送中...")

    pdf_paths = download_s3_to_dir(files, in_dir)
    display_name_map = _build_display_name_map(files, file_names)

    actual_file_names: List[str] = []
    for p in pdf_paths:
        normalize_pdf_inplace(p)
        chunks = max(1, (p.stat().st_size + (512 * 1024) - 1) // (512 * 1024)) if p.exists() else 1
        log(f"✅ {p.name} 転送完了 ({chunks}チャンク)", "ok")
        actual_file_names.append(p.name)

    # file_names が指定されていれば優先、なければ実ファイル名
    send_file_names = [
        Path(file_names[i]).name if file_names and i < len(file_names) and file_names[i] else actual_file_names[i]
        for i in range(len(actual_file_names))
    ]

    log("🚀 解析開始...")
    log("📨 Geminiへ全PDFを一括送信します")

    result_json = analyze_multiple_pdfs_with_gemini(client, [str(p) for p in pdf_paths], send_file_names)

    # フィールド正規化
    for item in result_json.get("results", []) or []:
        item["labels"] = _normalize_labels_field(item.get("labels", []))
        item["年度"] = _normalize_years_field(item.get("年度", []))
        item["reason"] = str(item.get("reason", "") or "").strip()

    _apply_two_file_gap_rule(result_json)
    _apply_single_file_two_year_labels_rule(result_json)
    result_json = _replace_display_names_in_results(result_json, display_name_map)
    display_text = build_display_text(result_json)
    period_mapping = build_period_mapping_from_result(result_json)
    period_mapping = _replace_display_names_in_period_mapping(period_mapping, display_name_map)

    json_path = out_dir / "period_result.json"
    txt_path = out_dir / "period_result.txt"

    json_path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(display_text, encoding="utf-8")

    log("✅ 解析完了！", "ok")

    logs = _replace_display_names_in_logs(logs, display_name_map)
    apimessages = _replace_display_names_in_apimessages(apimessages, display_name_map)

    return {
        "result_json": result_json,
        "display_text": display_text,
        "logs": logs,
        "apimessage": apimessages,
        "company_warning": None,
        "position_warnings": position_warnings,
        "period_mapping": period_mapping,
    }