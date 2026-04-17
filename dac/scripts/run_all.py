"""
run_all.py - 一鍵執行唯讀流程

依序執行：
  1. extract_summary.py  →  dac/summary.md + dac/docs_map.yaml
  2. sync_audit.py       →  dac/diff_report.md + dac/.diff_data.json

注意：excel_sync.py 需人工確認 diff_report.md 後，另行手動執行。
"""

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

STEPS = [
    ("extract_summary.py", "提取專案摘要"),
    ("sync_audit.py",      "比對程式碼與文件差異"),
]


def run_script(script_name: str, label: str) -> bool:
    script_path = SCRIPTS_DIR / script_name
    print(f"\n{'─' * 50}")
    print(f"▶  {label} ({script_name})")
    print(f"{'─' * 50}")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(SCRIPTS_DIR),
    )

    if result.returncode != 0:
        print(f"\n[ERROR] {script_name} 執行失敗（return code {result.returncode}）", file=sys.stderr)
        return False
    return True


def main() -> None:
    print("=" * 50)
    print("  DaC run_all.py — 唯讀流程")
    print("=" * 50)

    for script_name, label in STEPS:
        ok = run_script(script_name, label)
        if not ok:
            print("\n流程中斷，請修正錯誤後重新執行。", file=sys.stderr)
            sys.exit(1)

    print(f"\n{'=' * 50}")
    print("  全部完成！")
    print(f"{'=' * 50}")
    print()
    print("  產出檔案：")
    print("    dac/summary.md       ← 貼給 AI 的精簡上下文")
    print("    dac/docs_map.yaml    ← Actor/Diagram 對應表")
    print("    dac/diff_report.md   ← 程式碼與文件差異報告")
    print()
    print("  後續步驟：")
    print("    1. 閱讀 dac/diff_report.md，確認差異正確")
    print("    2. 確認無誤後執行：python dac/scripts/excel_sync.py")
    print()


if __name__ == "__main__":
    main()
