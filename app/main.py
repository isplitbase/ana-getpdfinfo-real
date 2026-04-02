from fastapi import FastAPI, Body, HTTPException
from typing import Any, Dict
import traceback

from app.pipeline.runner import run_getpdfinfo as run_getpdfinfo_pipeline

app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


def _is_getpdfinfo_payload(payload: Dict[str, Any]) -> bool:
    files = payload.get("files") or payload.get("file")
    if isinstance(files, str):
        return files.startswith("s3://")
    if isinstance(files, list) and len(files) > 0:
        return all(isinstance(x, str) and x.startswith("s3://") for x in files)
    return False


@app.post("/v1/pipeline")
def pipeline(payload: Dict[str, Any] = Body(...)):
    """
    zlite-getpdfinfo 用の統一エンドポイント。

    主用途:
      {
        "files": [
          "s3://zlite/xxx-1.pdf",
          "s3://zlite/xxx-2.pdf",
          "s3://zlite/xxx-3.pdf"
        ]
      }

    互換:
      {"file": "s3://zlite/xxx.pdf"}
    """
    try:
        if _is_getpdfinfo_payload(payload):
            return run_getpdfinfo_pipeline(payload)

        raise HTTPException(
            status_code=400,
            detail={
                "message": "payload.files に s3://... のPDF配列を指定してください。",
                "example": {
                    "files": [
                        "s3://zlite/sample-1.pdf",
                        "s3://zlite/sample-2.pdf"
                    ]
                }
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print("=== /v1/pipeline ERROR START ===")
        print(tb)
        print("payload =", payload)
        print("=== /v1/pipeline ERROR END ===")
        raise HTTPException(
            status_code=500,
            detail={
                "message": str(e),
                "traceback_tail": tb.splitlines()[-20:]
            }
        )


@app.post("/v1/zlite-getpdfinfo")
def zlite_getpdfinfo(payload: Dict[str, Any] = Body(...)):
    """
    /v1/pipeline と同じく getpdfinfo11 を実行する専用エンドポイント。
    """
    try:
        return run_getpdfinfo_pipeline(payload)
    except Exception as e:
        tb = traceback.format_exc()
        print("=== /v1/zlite-getpdfinfo ERROR START ===")
        print(tb)
        print("payload =", payload)
        print("=== /v1/zlite-getpdfinfo ERROR END ===")
        raise HTTPException(
            status_code=500,
            detail={
                "message": str(e),
                "traceback_tail": tb.splitlines()[-20:]
            }
        )
