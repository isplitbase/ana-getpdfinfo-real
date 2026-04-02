import os
import tempfile
import shutil
import traceback
from pipeline.originals.colab142 import main as colab142_main

KEEP_WORK_DIR = os.getenv("KEEP_WORK_DIR", "0") == "1"


def run(ai_case_id: str, input_file_path: str):
    """
    runner142
    ・リクエスト専用の一時ディレクトリを作成
    ・処理完了後に完全削除
    ・並行実行安全
    """

    run_dir = tempfile.mkdtemp(prefix="cashai03_142_", dir="/tmp")
    work_dir = os.path.join(run_dir, "work")
    os.makedirs(work_dir, exist_ok=True)

    try:
        # 入力ファイルを専用workディレクトリへコピー
        local_input_path = os.path.join(work_dir, os.path.basename(input_file_path))
        shutil.copy2(input_file_path, local_input_path)

        # colab処理呼び出し
        result = colab142_main(
            ai_case_id=ai_case_id,
            input_file_path=local_input_path,
            work_dir=work_dir
        )

        return result

    except Exception as e:
        print("runner142 error:", str(e))
        traceback.print_exc()
        raise

    finally:
        # ゴミ完全削除（デバッグ時のみ保持可能）
        if not KEEP_WORK_DIR:
            try:
                shutil.rmtree(run_dir, ignore_errors=True)
            except Exception as cleanup_error:
                print("runner142 cleanup error:", cleanup_error)
