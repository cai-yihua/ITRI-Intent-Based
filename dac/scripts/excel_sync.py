"""
excel_sync.py - 將差異標記寫入 CSV 備份

輸入:  dac/.diff_data.json（由 sync_audit.py 產生）
輸出:  dac/excel/*.csv（在原檔新增 _status 欄/列）

⚠️  注意：請先閱讀 dac/diff_report.md 確認差異正確後，再執行此腳本。
         此腳本會直接修改 CSV 備份檔案。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

# ─────────────────────────── 路徑設定 ────────────────────────────

def find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "Backend").exists() and (parent / "dac").exists():
            return parent
    raise RuntimeError("找不到專案根目錄，請確認 Backend/ 和 dac/ 資料夾存在。")

ROOT      = find_project_root()
DAC_DIR   = ROOT / "dac"
EXCEL_DIR = DAC_DIR / "excel"

KNOWN_APPS = {"metadata_mgt", "conversation_mgt", "topic_mgt", "workflow_mgt"}

STATUS_ADDED   = "ADDED"
STATUS_REMOVED = "REMOVED"
STATUS_CHANGED = "CHANGED"

# ─────────────────────────── 讀取 diff 資料 ──────────────────────

def load_diff_data() -> dict:
    diff_path = DAC_DIR / ".diff_data.json"
    if not diff_path.exists():
        print("錯誤：找不到 .diff_data.json，請先執行 sync_audit.py。", file=sys.stderr)
        sys.exit(1)
    with open(diff_path, encoding="utf-8") as f:
        return json.load(f)

# ─────────────────────────── CSV 工具 ────────────────────────────

def _read_csv(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def _find_csv_by_content(marker_check) -> Path | None:
    """用自訂函式識別目標 CSV"""
    for f in EXCEL_DIR.glob("*.csv"):
        try:
            rows = _read_csv(f)
            if marker_check(rows):
                return f
        except Exception:
            continue
    return None


def _is_db_schema_csv(rows: list) -> bool:
    return any(r and r[0].lower() == "module" for r in rows)


def _is_component_csv(rows: list) -> bool:
    return bool(rows) and any("組件" in (c or "") for c in rows[0])

# ─────────────────────────── DB Schema CSV 標記 ──────────────────

def mark_db_schema(db_diff: dict) -> None:
    """在 data schema CSV 的每個 block 後方加入 _status 列"""
    csv_path = _find_csv_by_content(_is_db_schema_csv)
    if not csv_path:
        print("  [SKIP] 找不到 data schema CSV。", file=sys.stderr)
        return

    rows = _read_csv(csv_path)
    # 先移除舊的 _status 列（避免重複標記）
    rows = [r for r in rows if not (r and r[0].lower() == "_status")]

    # 建立 table → {field → status} 的快速查詢表
    field_status: dict = {}  # {table: {field: STATUS}}
    for table, info in db_diff.items():
        entry: dict = {}
        for f in info.get("added", []):
            entry[f] = STATUS_ADDED
        for f in info.get("removed", []):
            entry[f] = STATUS_REMOVED
        for c in info.get("changed", []):
            entry[c["field"]] = STATUS_CHANGED
        if entry:
            field_status[table] = entry

    # 掃描 rows，在每個 block 的 remark 列後插入 _status 列
    new_rows: list = []
    i = 0
    while i < len(rows):
        row = rows[i]
        new_rows.append(row)

        if row and row[0].lower() == "remark":
            # 往前找對應的 table 名稱和 column 列
            table_name, columns = _find_block_context(new_rows)
            if table_name and table_name in field_status:
                status_row = ["_status"]
                for col in columns:
                    status_row.append(field_status[table_name].get(col, ""))
                new_rows.append(status_row)
        i += 1

    _write_csv(csv_path, new_rows)
    print(f"  [OK] 已更新 {csv_path.name}")


def _find_block_context(rows_so_far: list) -> tuple:
    """從目前已處理的 rows 往回找最近的 table 和 column 列"""
    table_name = None
    columns: list = []
    for row in reversed(rows_so_far):
        if not row:
            continue
        key = row[0].lower()
        if key == "column" and not columns:
            columns = [c.strip() for c in row[1:] if c.strip()]
        if key == "table" and not table_name:
            table_name = row[1].strip() if len(row) > 1 else None
        if table_name and columns:
            break
    return table_name, columns

# ─────────────────────────── 元件一覽表 CSV 標記 ─────────────────

def mark_component_csv(actor_diff: dict) -> None:
    """在元件一覽表 CSV 的每個 function 行加入 _status 欄"""
    csv_path = _find_csv_by_content(_is_component_csv)
    if not csv_path:
        print("  [SKIP] 找不到元件一覽表 CSV。", file=sys.stderr)
        return

    rows = _read_csv(csv_path)

    # 決定 _status 欄的位置（固定在最後一欄，或找現有的）
    header = rows[0] if rows else []
    if "_status" in header:
        status_col_idx = header.index("_status")
    else:
        status_col_idx = None  # 待決定（插在所有資料欄之後）

    # 建立 (module, actor, function) → status 快速查詢表
    func_status: dict = {}
    for app, actors in actor_diff.items():
        for actor, changes in actors.items():
            for f in changes.get("added", []):
                func_status[(app, actor, f)] = STATUS_ADDED
            for f in changes.get("removed", []):
                func_status[(app, actor, f)] = STATUS_REMOVED

    # 找出所有 rows 的最大欄數，確保 _status 欄位置一致
    if status_col_idx is None:
        max_cols = max((len(r) for r in rows), default=0)
        status_col_idx = max_cols  # 加在末尾

    new_rows: list = []
    for idx, row in enumerate(rows):
        # 補齊至 status_col_idx
        while len(row) < status_col_idx:
            row.append("")
        if len(row) == status_col_idx:
            row.append("")  # 加 _status 欄

        if idx == 0:
            # header 行
            row[status_col_idx] = "_status"
        else:
            # 資料行：檢查是否有差異
            if len(row) > 6:
                module   = row[4].strip()
                actor    = row[5].strip()
                function = row[6].strip()
                if module in KNOWN_APPS and actor and function:
                    key = (module, actor, function)
                    row[status_col_idx] = func_status.get(key, "")

        new_rows.append(row)

    _write_csv(csv_path, new_rows)
    print(f"  [OK] 已更新 {csv_path.name}")

# ─────────────────────────── 主程式 ──────────────────────────────

def main() -> None:
    print("=== excel_sync.py ===")
    print("⚠️  此腳本將直接修改 dac/excel/ 內的 CSV 備份檔案。")
    print("   請確認已閱讀 dac/diff_report.md 並確認差異正確無誤。\n")

    diff_data    = load_diff_data()
    db_diff      = diff_data.get("db_diff", {})
    actor_diff   = diff_data.get("actor_diff", {})

    # 確認有差異才執行
    has_db_diff    = any(
        v.get("added") or v.get("removed") or v.get("changed")
        for v in db_diff.values()
    )
    has_actor_diff = bool(actor_diff)

    if not has_db_diff and not has_actor_diff:
        print("  沒有需要標記的差異，結束。")
        return

    if has_db_diff:
        print("標記 DB Schema CSV ...")
        mark_db_schema(db_diff)

    if has_actor_diff:
        print("標記元件一覽表 CSV ...")
        mark_component_csv(actor_diff)

    print("\n完成。請用試算表工具開啟 CSV 確認標記結果。\n")


if __name__ == "__main__":
    main()
