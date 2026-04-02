# ============================================================
# Cell 4: コアロジック定義
# ============================================================

import os
import json
import base64
from pathlib import Path
from google import genai
from google.genai import types as genai_types

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
MODEL = "gemini-2.0-flash"

# PDFごとの識別を安定させるため、番号とファイル名を明示してJSON出力させる
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
- ラベルは必ず次の4種類のみを使ってください: ["今期", "前期", "前々期", "前々期の前期",]
- 順番は、送信されたPDF順（PDF1, PDF2, PDF3, ...）で返してください。

出力JSON形式:
{{
  "results": [
    {{
      "pdf_index": 1,
      "file_name": "A.pdf",
      "labels": ["今期"],
      "reason": "理由",
      "年度": "年度名"
    }},
    {{
      "pdf_index": 2,
      "file_name": "B.pdf",
      "labels": ["前期"],
      "reason": "理由",
      "年度": "年度名"
    }},
    {{
      "pdf_index": 3,
      "file_name": "C.pdf",
      "labels": ["前々期"],
      "reason": "理由",
      "年度": "年度名"
    }}
  ]
}}
""".strip()


def _extract_json_text(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def _call_gemini_json(contents: list, max_tokens: int = 4000) -> dict:
    import time

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
                    response_mime_type="application/json"
                )
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
            time.sleep(wait)

    raise RuntimeError(f"Gemini API {MAX_RETRIES}回失敗: {last_err}")


def analyze_multiple_pdfs_with_gemini(pdf_paths: list, file_names: list) -> dict:
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
            "file_name": file_name
        })

    prompt = build_meta_prompt(pdf_infos)

    contents = [prompt]

    for i, pdf_path in enumerate(pdf_paths, start=1):
        file_name = file_names[i - 1]

        # PDFの直前に識別情報をテキストで明示する
        contents.append(f"以下が PDF{i} / ファイル名: {file_name} です。")
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        contents.append(
            genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type="application/pdf",
                    data=pdf_bytes
                )
            )
        )

    result = _call_gemini_json(contents)

    # 念のため並び順をpdf_indexでソート
    results = result.get("results", [])
    results = sorted(results, key=lambda x: x.get("pdf_index", 9999))
    result["results"] = results

    return result


def build_display_text(result_json: dict) -> str:
    """
    画面表示用テキストに整形
    """
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


print("✅ コアロジック定義完了")


# ============================================================
# Cell 5: HTMLアップロード画面 + コールバック
# ============================================================

import google.colab.output
from IPython.display import HTML, display

os.makedirs("/content/input", exist_ok=True)
os.makedirs("/content/output", exist_ok=True)

HTML_UI = '''
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>決算書PDF分析ツール</title>
<style>
  body {
    font-family: Arial, sans-serif;
    padding: 20px;
    background: #f7f7f7;
  }
  h2 {
    margin-bottom: 16px;
  }
  .row {
    margin-bottom: 12px;
  }
  button {
    margin-right: 8px;
    padding: 8px 16px;
    cursor: pointer;
  }
  #result {
    white-space: pre-wrap;
    background: #fff;
    border: 1px solid #ccc;
    padding: 16px;
    margin-top: 16px;
    min-height: 160px;
  }
  #log {
    white-space: pre-wrap;
    background: #fff;
    border: 1px solid #ccc;
    padding: 12px;
    margin-top: 16px;
    min-height: 100px;
    color: #333;
  }
  .hint {
    color: #666;
    font-size: 13px;
    margin-bottom: 8px;
  }
</style>
</head>
<body>

<h2>決算書PDF分析ツール</h2>

<div class="row hint">
PDFを複数選択すると、すべてのPDFをまとめて1回でGeminiに送信します。
</div>

<div class="row">
  <input type="file" id="fileInput" multiple accept=".pdf">
</div>

<div class="row">
  <button onclick="startAnalysis()">分析</button>
  <button onclick="clearAll()">クリア</button>
  <button onclick="printResults()">結果をプリント</button>
</div>

<div id="log">Ready.</div>
<div id="result"></div>

<script>
let selectedFiles = [];

document.getElementById("fileInput").addEventListener("change", e => {
  selectedFiles = [...e.target.files];
  addLog("選択ファイル数: " + selectedFiles.length);
  selectedFiles.forEach((f, i) => {
    addLog((i + 1) + "件目: " + f.name);
  });
});

function addLog(msg) {
  const el = document.getElementById("log");
  el.textContent += "\\n" + msg;
}

function clearAll() {
  selectedFiles = [];
  document.getElementById("fileInput").value = "";
  document.getElementById("result").textContent = "";
  document.getElementById("log").textContent = "Ready.";
}

function printResults() {
  const resultText = document.getElementById("result").textContent.trim();
  if (!resultText) {
    alert("プリントする結果がありません。先に分析してください。");
    return;
  }
  window.print();
}

async function startAnalysis() {
  if (selectedFiles.length === 0) {
    alert("PDFを選択してください");
    return;
  }

  document.getElementById("result").textContent = "";
  document.getElementById("log").textContent = "処理開始...";

  const CHUNK = 512 * 1024;
  const fileNames = [];

  for (const f of selectedFiles) {
    addLog("アップロード中: " + f.name);

    const b64 = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result.split(",")[1]);
      r.onerror = () => rej(new Error("ファイル読込失敗: " + f.name));
      r.readAsDataURL(f);
    });

    const total = Math.ceil(b64.length / CHUNK);

    for (let i = 0; i < total; i++) {
      const chunk = b64.slice(i * CHUNK, (i + 1) * CHUNK);

      await google.colab.kernel.invokeFunction(
        "upload_chunk_callback",
        [{ name: f.name, chunk: chunk, index: i, total: total }],
        {}
      );
    }

    fileNames.push(f.name);
    addLog("アップロード完了: " + f.name);
  }

  window.__cbDone = false;
  window.__cbResult = null;

  addLog("Geminiへ一括送信中...");

  await google.colab.kernel.invokeFunction(
    "analyze_pdfs_callback",
    [fileNames],
    {}
  );

  let waited = 0;
  while (!window.__cbDone && waited < 300000) {
    await new Promise(r => setTimeout(r, 200));
    waited += 200;
  }

  if (!window.__cbResult) {
    document.getElementById("result").textContent = "エラー: 結果を取得できませんでした";
    addLog("エラー: タイムアウトまたは結果取得失敗");
    return;
  }

  const data = JSON.parse(window.__cbResult);

  if (data.error) {
    document.getElementById("result").textContent = "エラー: " + data.error;
    addLog("エラー: " + data.error);
    return;
  }

  if (data.logs) {
    data.logs.forEach(x => addLog(x.msg));
  }

  document.getElementById("result").textContent = data.display_text || "";
  addLog("完了");
}
</script>

</body>
</html>
'''


def upload_chunk_callback(chunk_info):
    import base64 as _b64
    import shutil as _sh

    fname = chunk_info["name"]
    chunk = chunk_info["chunk"]
    idx = chunk_info["index"]
    total = chunk_info["total"]

    os.makedirs("/content/input", exist_ok=True)

    tmp_path = f"/content/input/{fname}.parts"
    pdf_path = f"/content/input/{fname}"

    pad = (4 - len(chunk) % 4) % 4
    raw_chunk = _b64.b64decode(chunk + "=" * pad)

    mode = "ab" if idx > 0 else "wb"
    with open(tmp_path, mode) as f:
        f.write(raw_chunk)

    if idx == total - 1:
        _sh.move(tmp_path, pdf_path)
        size_mb = os.path.getsize(pdf_path) / 1024 / 1024
        print(f"[UPLOAD] {fname} {size_mb:.1f}MB")


google.colab.output.register_callback("upload_chunk_callback", upload_chunk_callback)


def analyze_pdfs_callback(file_names):
    logs = []

    def log(msg, t="info"):
        logs.append({"msg": msg, "type": t})
        print(f"[{t.upper()}] {msg}")

    try:
        pdf_paths = []

        for fname in file_names:
            pdf_path = f"/content/input/{fname}"

            if not os.path.exists(pdf_path):
                raise FileNotFoundError(f"{fname} が /content/input/ に見つかりません")

            pdf_paths.append(pdf_path)
            log(f"読み込み: {fname}")

        log("Geminiへ全PDFを一括送信します")

        result_json = analyze_multiple_pdfs_with_gemini(pdf_paths, file_names)
        # --------------------------------------------------
        # 追加ロジック:
        # ファイルが2個しかない、かつ2個ファイルの最新年度の差が2年の場合、
        # 年度が古いファイルの labels 内で
        #   「前期」   → 「前々期」
        #   「前々期」 → 「前々期の前期」
        # に置換する
        # --------------------------------------------------
        if len(result_json.get("results", [])) == 2:
            results = result_json.get("results", [])

            def extract_latest_year(item):
                """
                item["年度"] から最新年度を整数で返す
                例:
                  "令和6年度" -> 6
                  "2024年度" -> 2024
                  ["令和5年度", "令和6年度"] -> 6
                  ["2023年度", "2024年度"] -> 2024
                取れない場合は None
                """
                import re

                years_raw = item.get("年度", "")
                candidates = years_raw if isinstance(years_raw, list) else [years_raw]

                nums = []
                for y in candidates:
                    if not y:
                        continue
                    s = str(y)

                    # 数字を全部拾う
                    found = re.findall(r"\d+", s)
                    for n in found:
                        try:
                            nums.append(int(n))
                        except Exception:
                            pass

                return max(nums) if nums else None

            year1 = extract_latest_year(results[0])
            year2 = extract_latest_year(results[1])

            if year1 is not None and year2 is not None and abs(year1 - year2) == 2:
                # 年度が古いファイルを特定
                older_item = results[0] if year1 < year2 else results[1]

                labels = older_item.get("labels", [])
                if not isinstance(labels, list):
                    labels = [str(labels)]

                replaced_labels = []
                for label in labels:
                    if label == "前期":
                        replaced_labels.append("前々期")
                    elif label == "前々期":
                        replaced_labels.append("前々期の前期")
                    else:
                        replaced_labels.append(label)

                older_item["labels"] = replaced_labels
        # --------------------------------------------------
        display_text = build_display_text(result_json)

        # 保存
        json_path = "/content/output/period_result.json"
        txt_path = "/content/output/period_result.txt"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result_json, f, ensure_ascii=False, indent=2)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(display_text)

        log(f"JSON保存: {json_path}", "ok")
        log(f"TEXT保存: {txt_path}", "ok")
        log("解析完了", "ok")

        payload = {
            "logs": logs,
            "result_json": result_json,
            "display_text": display_text
        }

        payload_json = json.dumps(payload, ensure_ascii=False)
        b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")

        google.colab.output.eval_js(f"""
        (function(){{
            var b = atob("{b64}");
            var u = new Uint8Array(b.length);
            for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
            window.__cbResult = new TextDecoder("utf-8").decode(u);
            window.__cbDone = true;
        }})()
        """)

    except Exception as e:
        err_payload = json.dumps({
            "error": str(e),
            "logs": logs
        }, ensure_ascii=False)

        b64 = base64.b64encode(err_payload.encode("utf-8")).decode("ascii")

        google.colab.output.eval_js(f"""
        (function(){{
            var b = atob("{b64}");
            var u = new Uint8Array(b.length);
            for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
            window.__cbResult = new TextDecoder("utf-8").decode(u);
            window.__cbDone = true;
        }})()
        """)


google.colab.output.register_callback("analyze_pdfs_callback", analyze_pdfs_callback)

display(HTML(HTML_UI))
print("✅ UIを表示しました")