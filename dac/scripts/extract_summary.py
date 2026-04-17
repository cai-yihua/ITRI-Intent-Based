"""
extract_summary.py - 提取專案摘要供 AI 閱讀

輸出:
  - dac/summary.md       人類/AI 可讀的精簡上下文
  - dac/docs_map.yaml    機器可讀的 Actor/Function/Diagram 對應表 (供 sync_audit.py 使用)
"""

from __future__ import annotations

import ast
import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import yaml

# ─────────────────────────── 路徑設定 ────────────────────────────

def find_project_root() -> Path:
    """由腳本位置往上找到包含 Backend/ 和 dac/ 的專案根目錄"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "Backend").exists() and (parent / "dac").exists():
            return parent
    raise RuntimeError("找不到專案根目錄，請確認 Backend/ 和 dac/ 資料夾存在。")

ROOT      = find_project_root()
APPS_DIR  = ROOT / "Backend" / "main" / "apps"
PUML_DIR  = ROOT / "dac" / "puml"
DAC_DIR   = ROOT / "dac"

# puml 的 box 標籤 → Django App 名稱
MODULE_LABEL_MAP = {
    "metadata mgt module":     "metadata_mgt",
    "conversation mgt module": "conversation_mgt",
    "topic mgt module":        "topic_mgt",
    "workflow mgt module":     "workflow_mgt",
}

# ─────────────────────────── Code 解析 ───────────────────────────

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
            functions = _extract_public_methods(py_file)
            result[app_name][actor_name] = functions
    return result


def _extract_public_methods(py_file: Path) -> list:
    """用 ast 提取檔案內所有 public 方法/函式名稱（不含 _ 開頭）"""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"  [WARN] 無法解析 {py_file.name}: {e}", file=sys.stderr)
        return []

    methods: list = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                methods.append(node.name)
    return methods

# ─────────────────────────── PlantUML 解析 ────────────────────────

def extract_puml_mapping() -> dict:
    """
    解析 dac/puml/*.puml
    回傳 {filename: {title, actors: [{alias, name, module}], functions: [...]}}
    """
    result: dict = {}
    for puml_file in sorted(PUML_DIR.glob("*.puml")):
        if "copy" in puml_file.name.lower():
            continue  # 跳過重複檔
        data = _parse_puml(puml_file)
        result[puml_file.name] = data
    return result


def _parse_puml(puml_file: Path) -> dict:
    """解析單一 .puml 檔，提取 title、actors、functions"""
    content = puml_file.read_text(encoding="utf-8")

    title = ""
    actors: dict = {}          # alias → {name, module}
    functions: list = []
    current_module: str | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()

        # title
        title_m = re.match(r'^title\s+(.+)$', line, re.IGNORECASE)
        if title_m:
            title = title_m.group(1).strip()

        # box 開始 → 紀錄當前 module（去除 [Module] 等後綴再比對）
        box_m = re.match(r'^box\s+"([^"]+)"', line, re.IGNORECASE)
        if box_m:
            label = re.sub(r'\s*\[.*?\]', '', box_m.group(1)).lower().strip()
            current_module = MODULE_LABEL_MAP.get(label)

        # end box → 重置 module
        if re.match(r'^end\s+box', line, re.IGNORECASE):
            current_module = None

        # participant 宣告
        part_m = re.match(r'^(?:actor|participant)\s+(\w+)\s+as\s+"([^"]+)"', line, re.IGNORECASE)
        if part_m:
            alias       = part_m.group(1)
            display     = part_m.group(2)
            actor_name  = re.sub(r'\s*\[.*?\]', '', display).strip()
            actors[alias] = {"name": actor_name, "module": current_module}

        # 訊息行 (箭頭語法)：只擷取 /function_name 或純英文識別字
        msg_m = re.match(r'^\w+\s*-+>+\s*\w+\s*:\s*(.+)$', line)
        if msg_m:
            label = msg_m.group(1).strip()
            func = _extract_function_name(label)
            if func and func not in functions:
                functions.append(func)

    return {
        "title": title,
        "actors": [
            {"alias": alias, "name": info["name"], "module": info["module"]}
            for alias, info in actors.items()
        ],
        "functions": functions,
    }


def _extract_function_name(message: str) -> str | None:
    """從 puml 訊息標籤中提取函式名稱"""
    message = message.strip()
    # /function_name 格式 (REST endpoint)
    rest_m = re.match(r'^/([a-zA-Z_]\w*)$', message)
    if rest_m:
        return rest_m.group(1)
    # 純英文 snake_case / camelCase 識別字
    ident_m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)(\(.*\))?$', message)
    if ident_m:
        return ident_m.group(1)
    return None

# ─────────────────────────── 架構圖（draw.io XML）解析 ───────────

# 預設的 code 實體 → 架構圖關鍵字對應表
# key: app 名稱 或 actor 名稱（或 "dify" 等外部系統）
# value: 可能出現在架構圖標籤中的關鍵字清單（中英文皆可）
DEFAULT_LABEL_KEYWORD_MAP: dict = {
    "conversation_mgt":  ["對話"],
    "metadata_mgt":      ["使用者", "身份認證"],
    "topic_mgt":         ["訂閱", "消息佇列", "Topic"],
    "workflow_mgt":      ["工作流"],
    "ConversationManager": ["對話紀錄管理", "對話"],
    "WorkflowManager":     ["工作流管理", "工作流"],
    "UserManager":         ["使用者管理", "使用者"],
    "Broker":              ["對話通道連接", "訂閱", "Topic"],
    "Producer":            ["對話發布"],
    "Consumer":            ["訂閱"],
    "TopicManager":        ["對話發布訂閱"],
    "AudioManager":        ["音頻", "audio"],
    "ImageManager":        ["圖片", "image"],
    "AgentManager":        ["AI agent", "意圖"],
    "dify":                ["AI agent", "意圖", "情境工作流", "意圖工作流"],
}


def extract_architecture_diagrams() -> dict:
    """
    掃描 dac/puml/*.xml（draw.io 格式）
    回傳 {filename: {labels: [text, ...]}}
    """
    result: dict = {}
    for xml_file in sorted(PUML_DIR.glob("*.xml")):
        labels = _parse_drawio_labels(xml_file)
        result[xml_file.name] = {"labels": labels}
    return result


def _parse_drawio_labels(xml_file: Path) -> list:
    """從 draw.io XML 提取所有非空文字標籤（去除 HTML 標籤與空白）"""
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as e:
        print(f"  [WARN] 無法解析 {xml_file.name}: {e}", file=sys.stderr)
        return []

    labels: list = []
    for cell in tree.getroot().iter("mxCell"):
        raw = cell.get("value", "").strip()
        if not raw:
            continue
        clean = re.sub(r"<[^>]+>", "", raw)       # 去除 HTML 標籤
        clean = html.unescape(clean).strip()        # 解碼 HTML 實體
        clean = re.sub(r"\s+", " ", clean)          # 壓縮空白
        if clean and clean not in labels:
            labels.append(clean)
    return labels

# ─────────────────────────── 輸出寫入 ────────────────────────────

def write_summary(code_actors: dict, puml_mapping: dict, arch_diagrams: dict) -> None:
    """寫出 dac/summary.md"""
    today = date.today().isoformat()
    lines = [
        f"# Project Summary\n",
        f"_Generated: {today}_\n\n",
        "---\n\n",

        "## Modules & Actors\n\n",
    ]

    for app, actors in code_actors.items():
        actor_list = ", ".join(actors.keys()) if actors else "(none)"
        lines.append(f"- **{app}**: {actor_list}\n")

    lines.append("\n## Actor Functions\n\n")
    for app, actors in code_actors.items():
        lines.append(f"### {app}\n\n")
        for actor, funcs in actors.items():
            func_str = ", ".join(funcs) if funcs else "(none)"
            lines.append(f"- **{actor}**: {func_str}\n")
        lines.append("\n")

    lines.append("## Sequence Diagrams\n\n")
    for puml_name, data in puml_mapping.items():
        title_str = f" _{data['title']}_" if data["title"] else ""
        actor_parts = []
        for a in data["actors"]:
            mod = a["module"] or "external"
            actor_parts.append(f"{mod}.{a['name']}")
        actors_str = ", ".join(actor_parts) if actor_parts else "(none)"
        lines.append(f"- **{puml_name}**{title_str}\n")
        lines.append(f"  - Actors: {actors_str}\n")
        if data["functions"]:
            lines.append(f"  - Functions: {', '.join(data['functions'])}\n")
        lines.append("\n")

    lines.append("## Architecture Diagrams (draw.io)\n\n")
    if arch_diagrams:
        for xml_name, data in arch_diagrams.items():
            lines.append(f"- **{xml_name}** ({len(data['labels'])} labels)\n")
    else:
        lines.append("_(none found)_\n")
    lines.append("\n")

    lines.append("## Excel Sheets (本地備份)\n\n")
    excel_dir = DAC_DIR / "excel"
    for csv_file in sorted(excel_dir.glob("*.csv")):
        lines.append(f"- {csv_file.name}\n")

    out_path = DAC_DIR / "summary.md"
    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"  [OK] {out_path.relative_to(ROOT)}")


def write_docs_map(code_actors: dict, puml_mapping: dict, arch_diagrams: dict) -> None:
    """寫出 dac/docs_map.yaml"""
    data = {
        "modules": {
            app: {
                "actors": {
                    actor: {"functions": funcs}
                    for actor, funcs in actors.items()
                }
            }
            for app, actors in code_actors.items()
        },
        "diagrams": puml_mapping,
        "architecture_diagrams": arch_diagrams,
        "label_keyword_map": DEFAULT_LABEL_KEYWORD_MAP,
    }

    out_path = DAC_DIR / "docs_map.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"  [OK] {out_path.relative_to(ROOT)}")

# ─────────────────────────── 主程式 ──────────────────────────────

def main() -> None:
    print("=== extract_summary.py ===")
    print("掃描 actors ...")
    code_actors = extract_actors_from_code()
    total_actors = sum(len(v) for v in code_actors.values())
    total_funcs  = sum(len(f) for actors in code_actors.values() for f in actors.values())
    print(f"  找到 {len(code_actors)} 個 App，{total_actors} 個 Actor，{total_funcs} 個 Function")

    print("解析 PlantUML ...")
    puml_mapping = extract_puml_mapping()
    print(f"  找到 {len(puml_mapping)} 張時序圖")

    print("解析架構圖 (draw.io XML) ...")
    arch_diagrams = extract_architecture_diagrams()
    total_labels = sum(len(d["labels"]) for d in arch_diagrams.values())
    print(f"  找到 {len(arch_diagrams)} 張架構圖，共 {total_labels} 個標籤")

    print("寫出檔案 ...")
    write_summary(code_actors, puml_mapping, arch_diagrams)
    write_docs_map(code_actors, puml_mapping, arch_diagrams)
    print("完成。\n")


if __name__ == "__main__":
    main()
