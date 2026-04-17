"""
sync_audit.py - 比對程式碼與文件的差異，產生審計報告

輸出:
  - dac/diff_report.md    人類可讀的差異報告（含受影響時序圖）
  - dac/.diff_data.json   機器可讀的差異資料（供 excel_sync.py 使用）
"""

from __future__ import annotations

import ast
import csv
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

# ─────────────────────────── 路徑設定 ────────────────────────────

def find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "Backend").exists() and (parent / "dac").exists():
            return parent
    raise RuntimeError("找不到專案根目錄，請確認 Backend/ 和 dac/ 資料夾存在。")

ROOT      = find_project_root()
APPS_DIR  = ROOT / "Backend" / "main" / "apps"
DAC_DIR   = ROOT / "dac"
EXCEL_DIR = DAC_DIR / "excel"

# Django field type → SQL type 對應
FIELD_TYPE_MAP = {
    "AutoField":       "INT",
    "IntegerField":    "INT",
    "SmallIntegerField": "INT",
    "BigAutoField":    "BIGINT",
    "BigIntegerField": "BIGINT",
    "UUIDField":       "UUID(36)",
    "DateTimeField":   "DATETIME",
    "DateField":       "DATE",
    "TextField":       "TEXT",
    "BooleanField":    "BOOLEAN",
    "FloatField":      "FLOAT",
    "ForeignKey":      "ForeignKey",
    "OneToOneField":   "OneToOneField",
    "ManyToManyField": "ManyToManyField",
}

# ─────────────────────────── Django Models 解析 ───────────────────

def extract_models_from_code() -> dict:
    """
    掃描 metadata_mgt/models/*.py（只有 metadata_mgt 有真實 Models）
    回傳 {table_name: {field_name: sql_type}}
    """
    models_dir = APPS_DIR / "metadata_mgt" / "models"
    result: dict = {}
    for py_file in sorted(models_dir.glob("*.py")):
        if py_file.name in ("__init__.py", "models.py"):
            continue
        tables = _parse_model_file(py_file)
        result.update(tables)
    return result


def _parse_model_file(py_file: Path) -> dict:
    """用 ast 解析單一 Model 檔，回傳 {table_name: {field: sql_type}}"""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"  [WARN] 無法解析 {py_file.name}: {e}", file=sys.stderr)
        return {}

    tables: dict = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # 只處理繼承自 models.Model 的類別
        if not any(
            isinstance(b, ast.Attribute) and b.attr == "Model"
            for b in node.bases
        ):
            continue

        table_name: str | None = None
        fields: dict = {}

        for item in node.body:
            # --- Meta.db_table ---
            if isinstance(item, ast.ClassDef) and item.name == "Meta":
                for meta_item in item.body:
                    if isinstance(meta_item, ast.Assign):
                        for tgt in meta_item.targets:
                            if isinstance(tgt, ast.Name) and tgt.id == "db_table":
                                if isinstance(meta_item.value, ast.Constant):
                                    table_name = meta_item.value.value

            # --- 欄位賦值 ---
            if isinstance(item, ast.Assign):
                for tgt in item.targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    field_name = tgt.id
                    if field_name.startswith("_"):
                        continue
                    if not isinstance(item.value, ast.Call):
                        continue
                    func = item.value.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    django_type = func.attr  # e.g. "CharField", "ForeignKey"
                    sql_type = _django_to_sql(django_type, item.value)
                    fields[field_name] = sql_type

        if table_name:
            tables[table_name] = fields
    return tables


def _django_to_sql(django_type: str, call_node: ast.Call) -> str:
    """將 Django field type + kwargs 轉為 SQL 型別字串"""
    if django_type == "CharField":
        for kw in call_node.keywords:
            if kw.arg == "max_length" and isinstance(kw.value, ast.Constant):
                return f"VARCHAR({kw.value.value})"
        return "VARCHAR(?)"
    return FIELD_TYPE_MAP.get(django_type, django_type)

# ─────────────────────────── CSV 解析：DB Schema ─────────────────

def find_db_schema_csv() -> Path | None:
    """找出 data schema*.csv（以第一欄包含 'module' 的列作為識別）"""
    for f in EXCEL_DIR.glob("*.csv"):
        try:
            rows = _read_csv(f)
            if any(r and r[0].lower() == "module" for r in rows):
                return f
        except Exception:
            continue
    return None


def extract_db_schema_from_csv(csv_path: Path) -> dict:
    """
    解析 data schema CSV（直式格式）
    回傳 {table_name: {field_name: sql_type}}
    """
    rows = _read_csv(csv_path)
    result: dict = {}

    i = 0
    while i < len(rows):
        row = rows[i]
        if not row or row[0].lower() != "module":
            i += 1
            continue

        # 找到一個 block
        block: dict = {}
        while i < len(rows) and not (row and row[0].lower() == "module" and block):
            key = row[0].lower().strip() if row else ""
            if key in ("module", "table", "column", "type", "remark", "example", "from api"):
                block[key] = [c.strip() for c in row[1:]]
            i += 1
            if i < len(rows):
                row = rows[i]

        table_name = (block.get("table") or [""])[0]
        columns    = block.get("column", [])
        types      = block.get("type", [])

        if table_name and columns:
            fields: dict = {}
            for col, typ in zip(columns, types):
                col = col.strip()
                typ = typ.strip()
                if col:
                    fields[col] = typ
            result[table_name] = fields

    return result

# ─────────────────────────── CSV 解析：元件一覽表 ────────────────

def find_component_csv() -> Path | None:
    """找出主要元件一覽表 CSV（header 含 '組件 Actor'）"""
    for f in EXCEL_DIR.glob("*.csv"):
        try:
            rows = _read_csv(f)
            if rows and any("組件" in (c or "") for c in rows[0]):
                return f
        except Exception:
            continue
    return None


KNOWN_APPS = {"metadata_mgt", "conversation_mgt", "topic_mgt", "workflow_mgt"}

def extract_actors_from_csv(csv_path: Path) -> dict:
    """
    解析元件一覽表 CSV
    回傳 {app_name: {actor_name: [func_name, ...]}}
    """
    rows = _read_csv(csv_path)
    result: dict = {}

    for row in rows:
        if len(row) < 7:
            continue
        module   = row[4].strip()
        actor    = row[5].strip()
        # 函式名稱可能因換行被合併到同一格，逐一拆分處理
        raw_func = row[6].strip()
        functions = [f.strip() for f in re.split(r'[\n\r]+', raw_func) if f.strip()]
        if module not in KNOWN_APPS or not actor or not functions:
            continue
        result.setdefault(module, {}).setdefault(actor, [])
        for function in functions:
            if function not in result[module][actor]:
                result[module][actor].append(function)

    return result


def extract_actors_from_code() -> dict:
    """
    掃描 Backend/main/apps/*/actors/*.py
    回傳 {app_name: {actor_name: [func_name, ...]}}
    """
    result: dict = {}
    for app_dir in sorted(APPS_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        actors_dir = app_dir / "actors"
        if not actors_dir.exists():
            continue
        app_name = app_dir.name
        result[app_name] = {}
        for py_file in sorted(actors_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            actor_name = py_file.stem
            result[app_name][actor_name] = _extract_public_methods(py_file)
    return result


def _extract_public_methods(py_file: Path) -> list:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    methods: list = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                methods.append(node.name)
    return methods

# ─────────────────────────── Diff 計算 ───────────────────────────

def compute_db_diff(code_tables: dict, csv_tables: dict) -> dict:
    """
    比較 Django Models 與 CSV
    回傳 {table: {added:[...], removed:[...], changed:[{field, code_type, csv_type}], csv_table_name_mismatch: bool}}
    """
    diff: dict = {}

    # 以 code 中的 table 名稱為主
    all_tables = set(code_tables.keys()) | set(csv_tables.keys())
    for table in sorted(all_tables):
        code_fields = code_tables.get(table, {})
        csv_fields  = csv_tables.get(table, {})

        added   = [f for f in code_fields if f not in csv_fields]
        removed = [f for f in csv_fields  if f not in code_fields]
        changed = []
        for f in code_fields:
            if f in csv_fields and code_fields[f] != csv_fields[f]:
                changed.append({
                    "field":     f,
                    "code_type": code_fields[f],
                    "csv_type":  csv_fields[f],
                })

        if added or removed or changed or table not in csv_tables:
            diff[table] = {
                "added":   added,
                "removed": removed,
                "changed": changed,
                "in_code": table in code_tables,
                "in_csv":  table in csv_tables,
            }

    return diff


def compute_actor_diff(code_actors: dict, csv_actors: dict) -> dict:
    """
    比較 actors/*.py 與 元件一覽表 CSV
    回傳 {app: {actor: {added:[...], removed:[...]}}}
    """
    diff: dict = {}
    all_apps = set(code_actors.keys()) | set(csv_actors.keys())

    for app in sorted(all_apps):
        code_app = code_actors.get(app, {})
        csv_app  = csv_actors.get(app, {})
        all_actors = set(code_app.keys()) | set(csv_app.keys())

        for actor in sorted(all_actors):
            code_funcs = set(code_app.get(actor, []))
            csv_funcs  = set(csv_app.get(actor, []))
            added   = sorted(code_funcs - csv_funcs)
            removed = sorted(csv_funcs  - code_funcs)
            if added or removed:
                diff.setdefault(app, {})[actor] = {"added": added, "removed": removed}

    return diff


def compute_diagram_impact(actor_diff: dict, docs_map: dict) -> list:
    """
    根據 actor diff 與 docs_map，找出受影響的時序圖
    回傳 [{diagram, reason, functions}]
    """
    if not docs_map:
        return []

    diagrams = docs_map.get("diagrams", {})
    impacts: list = []

    for app, actors in actor_diff.items():
        for actor, changes in actors.items():
            for status, funcs in [("ADDED", changes["added"]), ("REMOVED", changes["removed"])]:
                for func in funcs:
                    affected = _find_diagrams_referencing(func, actor, app, diagrams)
                    for diag in affected:
                        impacts.append({
                            "diagram":  diag,
                            "function": func,
                            "actor":    f"{app}.{actor}",
                            "status":   status,
                        })

    # 去重
    seen: set = set()
    unique: list = []
    for item in impacts:
        key = (item["diagram"], item["function"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return sorted(unique, key=lambda x: x["diagram"])


def _find_diagrams_referencing(func: str, actor: str, app: str, diagrams: dict) -> list:
    """找出所有引用了該 function 或 actor 的時序圖名稱"""
    matched: list = []
    for diag_name, data in diagrams.items():
        # 方式 1：函式名稱出現在 functions 清單
        if func in (data.get("functions") or []):
            matched.append(diag_name)
            continue
        # 方式 2：對應的 actor 出現在 actors 清單
        for a in data.get("actors") or []:
            if a.get("name") == actor and a.get("module") == app:
                matched.append(diag_name)
                break
    return matched

# ─────────────────────────── Git Diff ───────────────────────────

def compute_git_diff(docs_map: dict) -> dict:
    """
    列出當前 branch（相對於 main）所有已提交的變更，
    分類為後端 actor/model、後端其他、前端，並對應時序圖。
    回傳:
    {
      "base_branch":      str,
      "commits":          [{hash, subject, files:[path,...]}],
      "backend_files":    [{path, file_type, app, name}],
      "backend_other":    [path, ...],
      "frontend_files":   [path, ...],
      "function_changes": {"app.Actor": {added:[...], removed:[...]}},
      "diagram_impacts":  [{diagram, actor, reason, changed_functions:[...]}],
    }
    """
    if not _is_git_available():
        return {"error": "git 不可用或非 git 專案"}

    base_branch = _detect_base_branch()

    # ── 取得 branch 上的 commit 清單（含每筆變更檔案）──
    commits = _git_branch_commits(base_branch)

    # ── 取得整個 branch 的累計變更檔案集合 ──
    all_files = _git_branch_changed_files(base_branch)

    # ── 直接掃 submodule 內部取累計變更檔案 ──
    backend_raw  = _git_submodule_changed_files("Backend",   base_branch)
    frontend_raw = _git_submodule_changed_files("Dashboard", base_branch)

    # ── 分類後端檔案 ──
    backend_actor_model: list = []
    backend_other: list       = []

    def _classify_backend(rel_path: str) -> None:
        p     = Path(rel_path)
        parts = p.parts
        try:
            apps_idx = parts.index("apps")
        except ValueError:
            backend_other.append(f"Backend/{rel_path}")
            return
        if apps_idx + 2 >= len(parts):
            backend_other.append(f"Backend/{rel_path}")
            return
        app_name  = parts[apps_idx + 1]
        sub_dir   = parts[apps_idx + 2]
        file_name = parts[-1]
        if not file_name.endswith(".py") or file_name == "__init__.py":
            backend_other.append(f"Backend/{rel_path}")
            return
        if sub_dir == "actors":
            backend_actor_model.append({
                "path":      f"Backend/{rel_path}",
                "file_type": "actor",
                "app":       app_name,
                "name":      Path(file_name).stem,
                "_rel":      rel_path,
            })
        elif sub_dir == "models" and file_name != "models.py":
            backend_actor_model.append({
                "path":      f"Backend/{rel_path}",
                "file_type": "model",
                "app":       app_name,
                "name":      Path(file_name).stem,
                "_rel":      rel_path,
            })
        else:
            backend_other.append(f"Backend/{rel_path}")

    for fp in backend_raw:
        _classify_backend(fp)

    # 父 repo 裡含 Backend/ 開頭的路徑（直接存取，非 submodule 指標）
    for fp in all_files:
        p = Path(fp)
        if p.parts[0] == "Backend":
            rel = str(Path(*p.parts[1:]))
            _classify_backend(rel)

    frontend_files = [f"Dashboard/{fp}" for fp in frontend_raw]
    # 父 repo 裡含 Dashboard/ 開頭的路徑
    for fp in all_files:
        if Path(fp).parts[0] == "Dashboard":
            if fp not in frontend_files:
                frontend_files.append(fp)

    # ── 函式層級差異（actor 檔，比對 branch base 前後）──
    function_changes: dict = {}
    backend_dir = ROOT / "Backend"
    for info in backend_actor_model:
        if info["file_type"] != "actor":
            continue
        key = f"{info['app']}.{info['name']}"
        added, removed = _diff_actor_between_branches(
            rel_path=info["_rel"],
            repo_dir=backend_dir,
            base_branch=base_branch,
        )
        if added or removed:
            function_changes[key] = {"added": added, "removed": removed}

    # ── 時序圖影響 ──
    diagram_impacts = _git_diagram_impacts(
        backend_actor_model, function_changes, docs_map,
        has_frontend=bool(frontend_files),
    )

    # 清除內部欄位
    for f in backend_actor_model:
        f.pop("_rel", None)

    # ── Submodule commit 清單（直接掃 submodule 內部）──
    backend_commits  = _git_submodule_commits("Backend",   base_branch)
    frontend_commits = _git_submodule_commits("Dashboard", base_branch)

    # ── 未提交變更（含 diff 片段）──
    uncommitted = _git_uncommitted_changes()

    return {
        "base_branch":       base_branch,
        "commits":           commits,
        "backend_commits":   backend_commits,
        "frontend_commits":  frontend_commits,
        "backend_files":     backend_actor_model,
        "backend_other":     backend_other,
        "frontend_files":    frontend_files,
        "function_changes":  function_changes,
        "diagram_impacts":   diagram_impacts,
        "uncommitted":       uncommitted,
    }


def _is_git_available() -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=ROOT
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _detect_base_branch() -> str:
    """偵測基準分支（main 或 master）"""
    for candidate in ("main", "master"):
        r = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            capture_output=True, cwd=ROOT
        )
        if r.returncode == 0:
            return candidate
    return "main"


def _git_run(args: list, cwd: Path) -> str:
    """執行 git 指令，回傳 stdout；失敗回傳空字串"""
    r = subprocess.run(args, capture_output=True, text=True, cwd=str(cwd))
    return r.stdout if r.returncode == 0 else ""


def _git_branch_commits(base_branch: str) -> list:
    """
    列出當前 branch 相對於 base_branch 的所有 commits（由新到舊）。
    回傳 [{hash, subject, files:[...]}]
    """
    # 取 commit hash + subject
    log_out = _git_run(
        ["git", "log", f"{base_branch}..HEAD", "--format=%H\t%s"],
        ROOT,
    )
    commits: list = []
    for line in log_out.splitlines():
        if "\t" not in line:
            continue
        h, subject = line.split("\t", 1)
        # 取該 commit 的變更檔案
        files_out = _git_run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", h],
            ROOT,
        )
        files = [f.strip() for f in files_out.splitlines() if f.strip()]
        commits.append({"hash": h[:8], "subject": subject, "files": files})
    return commits


def _git_branch_changed_files(base_branch: str) -> list:
    """
    取得整個 branch 累計的所有變更檔案（父 repo 層級）。
    """
    out = _git_run(
        ["git", "diff", "--name-only", f"{base_branch}...HEAD"],
        ROOT,
    )
    return [f.strip() for f in out.splitlines() if f.strip()]


def _git_submodule_commits(submodule: str, base_branch: str) -> list:
    """
    直接進入 submodule 取得其 branch 上的 commits（vs base_branch）。
    回傳 [{hash, subject, files:[...]}]
    """
    sub_dir = ROOT / submodule
    if not sub_dir.exists():
        return []

    # 確認 base_branch 在 submodule 內存在
    check = subprocess.run(
        ["git", "rev-parse", "--verify", base_branch],
        capture_output=True, cwd=str(sub_dir)
    )
    ref = base_branch if check.returncode == 0 else "HEAD~20"

    log_out = _git_run(
        ["git", "log", f"{ref}..HEAD", "--format=%H\t%s"],
        sub_dir,
    )
    commits: list = []
    for line in log_out.splitlines():
        if "\t" not in line:
            continue
        h, subject = line.split("\t", 1)
        files_out = _git_run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", h],
            sub_dir,
        )
        files = [f.strip() for f in files_out.splitlines() if f.strip()]
        commits.append({"hash": h[:8], "subject": subject, "files": files})
    return commits


def _git_submodule_changed_files(submodule: str, base_branch: str) -> list:
    """
    取得 submodule 在此 branch 上的累計變更檔案清單（去重）。
    直接掃 submodule 內部的 commits，不依賴父 repo 指標是否更新。
    """
    commits = _git_submodule_commits(submodule, base_branch)
    seen: set = set()
    files: list = []
    for c in commits:
        for f in c["files"]:
            if f not in seen:
                seen.add(f)
                files.append(f)
    return files


def _git_uncommitted_changes() -> dict:
    """
    掃描父 repo 及兩個 submodule 的未提交變更（staged + unstaged）。
    回傳:
    {
      "parent":   [{path, status, diff}],
      "backend":  [{path, status, diff}],
      "frontend": [{path, status, diff}],
    }
    每筆 diff 為 git diff HEAD 的片段（限 50 行，避免過長）。
    """
    def _scan(repo_dir: Path, prefix: str) -> list:
        # 取變更檔案清單
        status_out = _git_run(["git", "status", "--porcelain"], repo_dir)
        entries: list = []
        seen: set = set()
        for line in status_out.splitlines():
            if len(line) < 4:
                continue
            xy   = line[:2]
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ")[-1].strip()
            if path in seen:
                continue
            seen.add(path)
            status_char = xy[0].strip() or xy[1].strip() or "M"
            status_label = {
                "M": "Modified", "A": "Added", "D": "Deleted",
                "R": "Renamed",  "?": "Untracked",
            }.get(status_char, status_char)

            # 取 diff 片段（限 60 行）
            if status_char == "?":
                # untracked：讀檔頭部
                full = repo_dir / path
                try:
                    content = full.read_text(encoding="utf-8", errors="replace")
                    diff_lines = content.splitlines()[:30]
                    diff = "\n".join(f"+ {l}" for l in diff_lines)
                    if len(content.splitlines()) > 30:
                        diff += "\n... (truncated)"
                except Exception:
                    diff = ""
            else:
                diff_out = _git_run(
                    ["git", "diff", "HEAD", "--", path],
                    repo_dir,
                )
                if not diff_out:  # staged only
                    diff_out = _git_run(
                        ["git", "diff", "--cached", "--", path],
                        repo_dir,
                    )
                lines = diff_out.splitlines()
                if len(lines) > 60:
                    lines = lines[:60] + ["... (truncated)"]
                diff = "\n".join(lines)

            entries.append({
                "path":   f"{prefix}/{path}" if prefix else path,
                "status": status_label,
                "diff":   diff,
            })
        return entries

    # 取得 submodule 名稱清單，排除父 repo 掃描結果中的 submodule 指標
    submodule_names: set = set()
    gitmodules = ROOT / ".gitmodules"
    if gitmodules.exists():
        for line in gitmodules.read_text().splitlines():
            if line.strip().startswith("path"):
                _, _, val = line.partition("=")
                submodule_names.add(val.strip())

    parent_entries = _scan(ROOT, "")
    parent_entries = [e for e in parent_entries if e["path"] not in submodule_names]

    return {
        "parent":   parent_entries,
        "backend":  _scan(ROOT / "Backend",   "Backend"),
        "frontend": _scan(ROOT / "Dashboard", "Dashboard"),
    }


def _diff_actor_between_branches(rel_path: str, repo_dir: Path, base_branch: str) -> tuple:
    """
    比較 actor 檔在 base_branch 與現在（HEAD）的函式差異。
    回傳 (added_funcs, removed_funcs)
    """
    current_file = repo_dir / rel_path

    # 取 base_branch 版本（在 submodule 的 repo 內）
    # 先取父 repo 的 base_branch 對應的 submodule commit
    sub_name = repo_dir.name
    old_sub_hash = _git_run(
        ["git", "rev-parse", f"{base_branch}:{sub_name}"],
        ROOT,
    ).strip()

    old_src: str | None = None
    if old_sub_hash:
        old_src = _git_run(
            ["git", "show", f"{old_sub_hash}:{rel_path}"],
            repo_dir,
        ) or None
    else:
        # submodule 指標未追蹤到 base_branch，直接比 HEAD
        old_src = _git_run(
            ["git", "show", f"HEAD:{rel_path}"],
            repo_dir,
        ) or None

    old_funcs = set(_extract_methods_from_source(old_src)) if old_src else set()

    try:
        new_funcs = set(_extract_public_methods(current_file))
    except Exception:
        new_funcs = set()

    return sorted(new_funcs - old_funcs), sorted(old_funcs - new_funcs)


def _extract_methods_from_source(source: str) -> list:
    """從原始碼字串提取 public 函式/方法名稱"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    methods: list = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                methods.append(node.name)
    return methods


def _git_diagram_impacts(
    changed_files: list,
    function_changes: dict,
    docs_map: dict,
    has_frontend: bool = False,
) -> list:
    """
    依據變更的 actor/model 檔案，找出受影響的時序圖。
    has_frontend=True 時，所有含 FE 的圖都會被標記。
    回傳 [{diagram, actor, reason, changed_functions}]
    """
    if not docs_map:
        return []

    diagrams = docs_map.get("diagrams", {})
    impacts: list = []
    seen: set = set()

    # ── 後端 actor/model 影響 ──
    for info in changed_files:
        app  = info["app"]
        name = info["name"]
        key  = f"{app}.{name}"

        func_changes  = function_changes.get(key, {})
        changed_funcs = func_changes.get("added", []) + func_changes.get("removed", [])

        for diag_name, data in diagrams.items():
            matched = False
            for a in data.get("actors") or []:
                if a.get("name") == name and a.get("module") == app:
                    matched = True
                    break
            if not matched and changed_funcs:
                if any(f in (data.get("functions") or []) for f in changed_funcs):
                    matched = True

            if matched:
                pair = (diag_name, key)
                if pair not in seen:
                    seen.add(pair)
                    reason = f"{key} 檔案變更"
                    if info["file_type"] == "model":
                        reason = f"{key} (model) 變更，同 app Actor 可能受影響"
                    impacts.append({
                        "diagram":           diag_name,
                        "actor":             key,
                        "reason":            reason,
                        "changed_functions": changed_funcs,
                    })

    # ── 前端影響：標記所有含 FE Actor 的圖 ──
    if has_frontend:
        for diag_name, data in diagrams.items():
            has_fe = any(
                a.get("module") is None and "front" in (a.get("name") or "").lower()
                for a in (data.get("actors") or [])
            )
            if has_fe:
                pair = (diag_name, "Dashboard.FrontEnd")
                if pair not in seen:
                    seen.add(pair)
                    impacts.append({
                        "diagram":           diag_name,
                        "actor":             "Dashboard.FrontEnd",
                        "reason":            "前端程式碼變更，FE 互動邏輯可能受影響",
                        "changed_functions": [],
                    })

    return sorted(impacts, key=lambda x: x["diagram"])

# ─────────────────────────── 架構圖影響分析 ──────────────────────

def compute_architecture_impact(actor_diff: dict, git_diff: dict, docs_map: dict) -> dict:
    """
    檢測架構圖 (draw.io XML) 是否可能受影響。
    觸發條件：
      1. actor_diff 中有新增/移除的 App 或 Actor（結構性變更）
      2. git diff 中有任何涉及 "dify" 的檔案路徑
    回傳:
    {
      "triggered": bool,
      "triggers": [str, ...],            # 觸發原因列表
      "diagrams": {
        xml_filename: {
          "matched_labels": [str, ...],  # 模糊匹配到的標籤
          "all_labels":     [str, ...],  # 所有標籤（供參考）
        }
      }
    }
    """
    arch_diagrams = docs_map.get("architecture_diagrams", {})
    label_kw_map  = docs_map.get("label_keyword_map", {})

    if not arch_diagrams:
        return {"triggered": False, "triggers": [], "diagrams": {}}

    triggers: list         = []
    changed_entities: list = []   # 變更的 app / actor 名稱

    # Trigger 1：結構性變更（actor_diff 有任何條目）
    for app, actors in actor_diff.items():
        for actor in actors:
            changed_entities.append(app)
            changed_entities.append(actor)
            triggers.append(f"Actor 結構變更：{app}.{actor}")

    # Trigger 2：git commits 涉及 dify 相關路徑
    all_commit_files: list = []
    for c in git_diff.get("commits", []):
        all_commit_files.extend(c.get("files", []))
    for c in git_diff.get("backend_commits", []):
        all_commit_files.extend(c.get("files", []))
    dify_files = sorted({fp for fp in all_commit_files if "dify" in fp.lower()})
    if dify_files:
        triggers.append(f"Dify 相關檔案變更：{', '.join(dify_files)}")
        changed_entities.append("dify")

    if not triggers:
        return {"triggered": False, "triggers": [], "diagrams": {}}

    # 對每張架構圖做模糊關鍵字匹配
    result_diagrams: dict = {}
    for xml_name, diag_data in arch_diagrams.items():
        all_labels: list    = diag_data.get("labels", [])
        matched_labels: list = []

        for entity in changed_entities:
            keywords = label_kw_map.get(entity, [])
            for label in all_labels:
                label_lower = label.lower()
                for kw in keywords:
                    if kw.lower() in label_lower:
                        if label not in matched_labels:
                            matched_labels.append(label)
                        break

        result_diagrams[xml_name] = {
            "matched_labels": matched_labels,
            "all_labels":     all_labels,
        }

    return {
        "triggered": True,
        "triggers":  triggers,
        "diagrams":  result_diagrams,
    }

# ─────────────────────────── 報告輸出 ────────────────────────────

def write_diff_report(
    db_diff: dict, actor_diff: dict, diagram_impact: list,
    git_diff: dict, arch_impact: dict,
) -> None:
    """寫出 dac/diff_report.md"""
    today = date.today().isoformat()
    lines = [
        "# Diff Report\n",
        f"_Generated: {today}_\n\n",
        "---\n\n",
    ]

    # ── DB Diff ──
    lines.append("## [DB Diff] Django Models vs Data Schema CSV\n\n")
    if not db_diff:
        lines.append("_無差異。_\n\n")
    else:
        for table, info in db_diff.items():
            lines.append(f"### Table: `{table}`\n\n")

            if not info["in_csv"]:
                lines.append(f"> ⚠️  此資料表在 CSV 中找不到，可能尚未建立文件或 table 名稱有誤。\n\n")

            if info["added"]:
                lines.append("**ADDED（程式碼有，CSV 無）**\n\n")
                lines.append("| 欄位 | 程式碼型別 |\n|------|------------|\n")
                for f in info["added"]:
                    lines.append(f"| `{f}` | — |\n")
                lines.append("\n")

            if info["removed"]:
                lines.append("**REMOVED（CSV 有，程式碼無）**\n\n")
                lines.append("| 欄位 | CSV 型別 |\n|------|----------|\n")
                for f in info["removed"]:
                    lines.append(f"| `{f}` | — |\n")
                lines.append("\n")

            if info["changed"]:
                lines.append("**CHANGED（型別不一致）**\n\n")
                lines.append("| 欄位 | CSV 型別 | 程式碼型別 |\n|------|----------|------------|\n")
                for c in info["changed"]:
                    lines.append(f"| `{c['field']}` | `{c['csv_type']}` | `{c['code_type']}` |\n")
                lines.append("\n")

    # ── Actor Diff ──
    lines.append("---\n\n## [Actor Diff] Code Actors vs 元件一覽表 CSV\n\n")
    if not actor_diff:
        lines.append("_無差異。_\n\n")
    else:
        for app, actors in actor_diff.items():
            lines.append(f"### {app}\n\n")
            for actor, changes in actors.items():
                lines.append(f"#### {actor}\n\n")
                if changes["added"]:
                    lines.append("| 功能 | 狀態 |\n|------|------|\n")
                    for f in changes["added"]:
                        lines.append(f"| `{f}` | **ADDED** |\n")
                if changes["removed"]:
                    if not changes["added"]:
                        lines.append("| 功能 | 狀態 |\n|------|------|\n")
                    for f in changes["removed"]:
                        lines.append(f"| `{f}` | **REMOVED** |\n")
                lines.append("\n")

    # ── Diagram Impact (from CSV diff) ──
    lines.append("---\n\n## [Diagram Impact] 受影響的時序圖（CSV 差異）\n\n")
    if not diagram_impact:
        lines.append("_無受影響的時序圖。_\n\n")
    else:
        by_func: dict = {}
        for item in diagram_impact:
            key = (item["function"], item["actor"], item["status"])
            by_func.setdefault(key, []).append(item["diagram"])

        for (func, actor, status), diagrams in sorted(by_func.items()):
            lines.append(f"### `{func}` — **{status}** in `{actor}`\n\n")
            for d in sorted(diagrams):
                lines.append(f"- {d}\n")
            lines.append("\n")

    # ── Git Diff ──
    lines.append("---\n\n## [Git Diff] 當前分支已提交的變更\n\n")

    if git_diff.get("error"):
        lines.append(f"_⚠️  {git_diff['error']}_\n\n")
    else:
        base_branch    = git_diff.get("base_branch", "main")
        commits        = git_diff.get("commits", [])
        backend_files  = git_diff.get("backend_files", [])
        backend_other  = git_diff.get("backend_other", [])
        frontend_files = git_diff.get("frontend_files", [])
        func_changes   = git_diff.get("function_changes", {})
        git_impacts    = git_diff.get("diagram_impacts", [])

        lines.append(f"_比較基準：`{base_branch}`_\n\n")

        backend_commits  = git_diff.get("backend_commits", [])
        frontend_commits = git_diff.get("frontend_commits", [])

        # ── 父 repo Commit 清單 ──
        if not commits:
            lines.append("_此 branch 與 main 無差異（父 repo）。_\n\n")
        else:
            lines.append(f"### Commits — 父 repo（共 {len(commits)} 筆）\n\n")
            for c in commits:
                lines.append(f"#### `{c['hash']}` {c['subject']}\n\n")
                if c["files"]:
                    for fp in c["files"]:
                        lines.append(f"- `{fp}`\n")
                else:
                    lines.append("_（無檔案變更）_\n")
                lines.append("\n")

        # ── Backend submodule Commit 清單 ──
        if not backend_commits:
            lines.append("### Commits — Backend submodule\n\n_與 main 無差異。_\n\n")
        else:
            lines.append(f"### Commits — Backend submodule（共 {len(backend_commits)} 筆）\n\n")
            for c in backend_commits:
                lines.append(f"#### `{c['hash']}` {c['subject']}\n\n")
                if c["files"]:
                    for fp in c["files"]:
                        lines.append(f"- `{fp}`\n")
                else:
                    lines.append("_（無檔案變更）_\n")
                lines.append("\n")

        # ── Dashboard submodule Commit 清單 ──
        if not frontend_commits:
            lines.append("### Commits — Dashboard submodule\n\n_與 main 無差異。_\n\n")
        else:
            lines.append(f"### Commits — Dashboard submodule（共 {len(frontend_commits)} 筆）\n\n")
            for c in frontend_commits:
                lines.append(f"#### `{c['hash']}` {c['subject']}\n\n")
                if c["files"]:
                    for fp in c["files"]:
                        lines.append(f"- `{fp}`\n")
                else:
                    lines.append("_（無檔案變更）_\n")
                lines.append("\n")

        # ── 後端 actor/model 變更摘要 ──
        if backend_files:
            lines.append("### 後端 Actor / Model 變更\n\n")
            lines.append("| 檔案 | 類型 |\n|------|------|\n")
            for f in backend_files:
                lines.append(f"| `{f['path']}` | {f['file_type']} |\n")
            lines.append("\n")

        # ── 後端其他變更 ──
        if backend_other:
            lines.append("### 後端其他檔案\n\n")
            for fp in backend_other:
                lines.append(f"- `{fp}`\n")
            lines.append("\n")

        # ── 前端變更 ──
        if frontend_files:
            lines.append("### 前端變更（Dashboard）\n\n")
            for fp in frontend_files:
                lines.append(f"- `{fp}`\n")
            lines.append("\n")

        # ── 函式層級變更 ──
        if func_changes:
            lines.append("### 函式層級變更\n\n")
            for actor_key, changes in sorted(func_changes.items()):
                lines.append(f"#### `{actor_key}`\n\n")
                lines.append("| 函式 | 狀態 |\n|------|------|\n")
                for fn in changes.get("added", []):
                    lines.append(f"| `{fn}` | **ADDED** |\n")
                for fn in changes.get("removed", []):
                    lines.append(f"| `{fn}` | **REMOVED** |\n")
                lines.append("\n")

        # ── 受影響時序圖 ──
        lines.append("### 需要檢視的時序圖\n\n")
        if not git_impacts:
            lines.append("_此 branch 的變更與現有時序圖無直接對應。_\n\n")
        else:
            lines.append("| 時序圖 | 相關 Actor | 函式變更 | 原因 |\n|--------|------------|----------|------|\n")
            for item in git_impacts:
                funcs = ", ".join(f"`{f}`" for f in item["changed_functions"]) if item["changed_functions"] else "—"
                lines.append(f"| `{item['diagram']}` | `{item['actor']}` | {funcs} | {item['reason']} |\n")
            lines.append("\n")

    # ── 未提交變更 ──
    uncommitted = git_diff.get("uncommitted", {})
    _parent_uc   = uncommitted.get("parent", [])
    _backend_uc  = uncommitted.get("backend", [])
    _frontend_uc = uncommitted.get("frontend", [])
    has_uc = _parent_uc or _backend_uc or _frontend_uc

    lines.append("---\n\n## [Uncommitted] 未提交的變更\n\n")
    if not has_uc:
        lines.append("_目前無未提交的變更。_\n\n")
    else:
        def _render_uc_section(title: str, entries: list) -> None:
            if not entries:
                return
            lines.append(f"### {title}\n\n")
            for e in entries:
                lines.append(f"#### `{e['path']}` — {e['status']}\n\n")
                if e["diff"]:
                    lines.append("```diff\n")
                    lines.append(e["diff"] + "\n")
                    lines.append("```\n\n")
                else:
                    lines.append("_（無法取得 diff）_\n\n")

        _render_uc_section("父 repo", _parent_uc)
        _render_uc_section("Backend submodule", _backend_uc)
        _render_uc_section("Dashboard submodule", _frontend_uc)

    # ── Architecture Diagram Impact ──
    lines.append("---\n\n## [Architecture Diagram Impact] 架構圖受影響評估\n\n")
    if not arch_impact.get("triggered"):
        lines.append("_本次變更未觸發架構圖審查條件（無 Actor 結構變更、無 Dify 相關提交）。_\n\n")
    else:
        lines.append("> ⚠️  **請檢查架構圖** — 以下變更可能影響系統架構示意圖，建議人工確認是否需更新。\n\n")

        lines.append("**觸發原因：**\n\n")
        for t in arch_impact.get("triggers", []):
            lines.append(f"- {t}\n")
        lines.append("\n")

        for xml_name, diag_data in arch_impact.get("diagrams", {}).items():
            lines.append(f"### `{xml_name}`\n\n")
            matched = diag_data.get("matched_labels", [])
            all_lbs = diag_data.get("all_labels", [])

            if matched:
                lines.append("**可能受影響的標籤（模糊匹配）：**\n\n")
                for lbl in matched:
                    lines.append(f"- `{lbl}`\n")
                lines.append("\n")
            else:
                lines.append("_（關鍵字未命中任何標籤，但仍建議確認整體結構）_\n\n")

            lines.append(f"<details><summary>所有標籤（{len(all_lbs)} 個，展開參考）</summary>\n\n")
            for lbl in all_lbs:
                lines.append(f"- `{lbl}`\n")
            lines.append("\n</details>\n\n")

    out_path = DAC_DIR / "diff_report.md"
    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"  [OK] {out_path.relative_to(ROOT)}")


def write_diff_data(
    db_diff: dict, actor_diff: dict, diagram_impact: list,
    git_diff: dict, arch_impact: dict,
) -> None:
    """寫出 dac/.diff_data.json（供 excel_sync.py 讀取）"""
    data = {
        "db_diff":        db_diff,
        "actor_diff":     actor_diff,
        "diagram_impact": diagram_impact,
        "git_diff":       git_diff,
        "arch_impact":    arch_impact,
    }
    out_path = DAC_DIR / ".diff_data.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [OK] {out_path.relative_to(ROOT)} (供 excel_sync.py 使用)")

# ─────────────────────────── 工具函式 ────────────────────────────

def _read_csv(path: Path) -> list:
    """讀取 CSV，自動處理 BOM 與空白行"""
    rows: list = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append([c.strip() for c in row])
    return rows


def load_docs_map() -> dict:
    """載入 docs_map.yaml；若不存在則回傳空 dict 並提示"""
    p = DAC_DIR / "docs_map.yaml"
    if not p.exists():
        print("  [WARN] 找不到 docs_map.yaml，請先執行 extract_summary.py。"
              " Diagram Impact 區塊將略過。", file=sys.stderr)
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

# ─────────────────────────── 主程式 ──────────────────────────────

def main() -> None:
    print("=== sync_audit.py ===")

    # ── DB Diff ──
    print("解析 Django Models ...")
    code_tables = extract_models_from_code()
    print(f"  找到 {len(code_tables)} 張資料表: {', '.join(sorted(code_tables))}")

    db_schema_csv = find_db_schema_csv()
    if db_schema_csv:
        print(f"  讀取 CSV: {db_schema_csv.name}")
        csv_tables = extract_db_schema_from_csv(db_schema_csv)
        print(f"  CSV 中有 {len(csv_tables)} 張資料表: {', '.join(sorted(csv_tables))}")
    else:
        print("  [WARN] 找不到 data schema CSV，略過 DB Diff。", file=sys.stderr)
        csv_tables = {}

    db_diff = compute_db_diff(code_tables, csv_tables)

    # ── Actor Diff ──
    print("解析 Actor Functions ...")
    code_actors = extract_actors_from_code()

    component_csv = find_component_csv()
    if component_csv:
        print(f"  讀取 CSV: {component_csv.name}")
        csv_actors = extract_actors_from_csv(component_csv)
    else:
        print("  [WARN] 找不到元件一覽表 CSV，略過 Actor Diff。", file=sys.stderr)
        csv_actors = {}

    actor_diff = compute_actor_diff(code_actors, csv_actors)

    # ── Diagram Impact (CSV-based) ──
    print("計算受影響時序圖（CSV 差異）...")
    docs_map = load_docs_map()
    diagram_impact = compute_diagram_impact(actor_diff, docs_map)

    # ── Git Diff ──
    print("分析 Git 變更 ...")
    git_diff = compute_git_diff(docs_map)
    git_commits      = len(git_diff.get("commits", []))
    git_be           = len(git_diff.get("backend_files", [])) + len(git_diff.get("backend_other", []))
    git_fe           = len(git_diff.get("frontend_files", []))
    git_impact_count = len(git_diff.get("diagram_impacts", []))
    print(f"  Commits：{git_commits} 筆  |  後端：{git_be} 檔  |  前端：{git_fe} 檔  |  受影響時序圖：{git_impact_count} 筆")

    # ── Architecture Diagram Impact ──
    print("評估架構圖影響 ...")
    arch_impact = compute_architecture_impact(actor_diff, git_diff, docs_map)
    if arch_impact.get("triggered"):
        print(f"  ⚠️  架構圖審查觸發（{len(arch_impact['triggers'])} 個原因）")
    else:
        print("  架構圖無需審查。")

    # ── 輸出 ──
    print("寫出檔案 ...")
    write_diff_report(db_diff, actor_diff, diagram_impact, git_diff, arch_impact)
    write_diff_data(db_diff, actor_diff, diagram_impact, git_diff, arch_impact)

    # ── 摘要 ──
    db_count    = sum(
        len(v["added"]) + len(v["removed"]) + len(v["changed"])
        for v in db_diff.values()
    )
    actor_count = sum(
        len(c["added"]) + len(c["removed"])
        for actors in actor_diff.values()
        for c in actors.values()
    )
    print(f"\n  DB 差異：{db_count} 項  |  Actor 差異：{actor_count} 項  |  受影響圖（CSV）：{len(diagram_impact)} 筆  |  受影響圖（Git）：{git_impact_count} 筆")
    print("完成。\n")


if __name__ == "__main__":
    main()
