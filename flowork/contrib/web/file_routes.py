"""文件事实源面：开发形态下「定义目录即事实源」，列目录 / 读文件 / 写文件。

浏览器的沙箱拿不到本地路径，而开发者的设计器后端进程就跑在他自己机器上——
``--definitions-dir`` 把该目录授权给这三个端点，于是「打开 JSON 看流程 /
画完另存为 JSON」在浏览器里成立，且产物直接落在 git 工作区。

三条纪律（与 deploy 管线的语义对齐）：
- 写入前先过 ``load_definition`` 与设计期校验——带病的图不允许落盘；
- 文件名与定义 ``code`` 必须一致（``drift`` 按文件名反查，允许两者不一致会让
  对账静默跑偏）；
- 落盘内容是规范化 JSON 且剥掉 ``major/revision/status``——版本号由 deploy
  判定，手填不算数，写进文件只会在 git diff 里制造噪音。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from flowork.api import (
    DefinitionError,
    IssueLevel,
    ProcessEngine,
    load_definition,
)
from flowork.contrib.web._helpers import issues_payload, parse_spec
from flowork.contrib.web.json_files import (
    FILE_SUFFIX,
    read_json,
    resolve_json_path,
    root_of,
    write_json_atomic,
)

__all__ = ["FILE_SUFFIX", "register_file_routes", "register_runtime_routes"]

# 版本号与生命周期字段由 deploy 判定（见 definitions/definition.py 版本注释）；
# version 是已废弃的旧字段（load_definition 直接忽略），不该写进新文件
_NON_SEMANTIC_KEYS = ("major", "revision", "status", "version")


def _is_flow_definition(data: Any) -> bool:
    """目录里可能混着场景脚本/演练剧本——它们没有 code 与 nodes，不是流程定义。

    这类文件按约定跳过而非报「需要修复」：把它们列成坏文件会在打开对话框里
    刷一屏 pydantic 报错，而它们本来就不该被当成流程打开。
    """
    return isinstance(data, dict) and "code" in data and isinstance(data.get("nodes"), list)


def _file_summary(root: Path, path: Path, engine: ProcessEngine) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "file": path.name,
        "code": None,
        "name": "",
        "valid": False,
        "skipped": False,
        "error": "",
        "updated_at": None,
    }
    try:
        stat = path.stat()
        summary["updated_at"] = _iso(stat.st_mtime)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # 列表层自己解析：read_json 抛的是 HTTPException（给单文件端点用），
        # 在这里会把一个坏文件升级成整个清单 422——坏文件要如实列出而不是炸列表
        summary["error"] = str(exc)[:300]
        return summary
    if not _is_flow_definition(data):
        summary["skipped"] = True  # 场景脚本/剧本等非定义文件：不列不报
        return summary
    try:
        definition = load_definition(data, engine.config.node_registry)
    except DefinitionError as exc:
        summary["error"] = str(exc)[:300]
        return summary
    # 文件名不要求等于 code：仓里既有 04_expense_claim.json 这类带序号的命名，
    # 强求一致等于废掉现有目录约定。真正的歧义是「同一 code 有两份文件」——
    # 那份由清单层标记（_mark_duplicates），因为它要看全目录才知道。
    summary.update(code=definition.code, name=definition.name, valid=True)
    return summary


def _mark_duplicates(files: list[dict[str, Any]]) -> None:
    """同一 code 出现在多个文件里＝改哪个都不确定，两份都标为需修复。"""
    by_code: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        if item["code"]:
            by_code.setdefault(item["code"], []).append(item)
    for code, group in by_code.items():
        if len(group) == 1:
            continue
        names = "、".join(item["file"] for item in group)
        for item in group:
            item["valid"] = False
            item["error"] = f"code {code} 在多个文件里出现（{names}）——请只保留一份"


def _iso(timestamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None).isoformat()


def _source_shape(engine: ProcessEngine, spec: dict[str, Any]) -> dict[str, Any]:
    """写盘内容＝作者写的形状，只剥版本字段。

    不展开默认值：文件是源码，一次空转保存（打开就存）必须零 diff——全量
    展开会把每个可选字段都写出来，git 上全是噪音。规范化留给 deploy（引擎
    落库前本就会 round-trip 一次），文件层不重复做。
    """
    content = {key: value for key, value in spec.items() if key not in _NON_SEMANTIC_KEYS}
    parse_spec(engine, content)  # 写盘前最后一道装载校验（未知 kind / 结构错）
    return content


def register_runtime_routes(
    app: FastAPI,
    definitions_dir: str | Path | None,
    session_db: str = ":memory:",
    vocabulary_source: str = "",
    vocabulary_functions: list[str] | None = None,
) -> None:
    """下发运行形态：前端据此决定列表/保存走文件还是数据库（无需构建期配置）。

    ``session_db`` 是设计器自己那份**会话临时库**（缺省内存）——它只装模拟/场景的
    执行痕迹，与用户的运行时数据库无关。会话态在前端据此提示「关掉就没了」。

    ``vocabulary_*``：当前进程装配的表达式函数来源与函数名。**必须可见**——
    用户自定义的取人函数若没带进设计器进程，模拟会报未声明函数；界面要能
    一眼看出「这台设计器认不认我的函数」。
    """
    root = root_of(definitions_dir) if definitions_dir else None
    ephemeral = session_db in (":memory:", "sqlite:///:memory:") or (
        session_db.startswith("sqlite") and "mode=memory" in session_db
    )
    names = list(vocabulary_functions or [])

    @app.get("/api/runtime")
    def runtime() -> dict[str, Any]:
        return {
            "source": "files" if root is not None else "db",
            "definitions_dir": str(root) if root is not None else None,
            # 设计器不碰用户库：会话痕迹默认内存态，进程退出即消失
            "session_ephemeral": ephemeral,
            "vocabulary_source": vocabulary_source,
            "vocabulary_functions": names,
        }


def register_file_routes(app: FastAPI, engine: ProcessEngine, definitions_dir: str | Path) -> None:
    """把定义目录的三个端点挂到应用上（未配置目录时不注册——面不存在）。"""
    root = root_of(definitions_dir)

    @app.get("/api/files")
    def list_files() -> dict[str, Any]:
        """目录清单（含每份文件的装载结论；坏文件不隐藏，如实报错）。

        场景脚本/演练剧本按约定跳过（``skipped``）——它们不是流程定义。
        """
        files = [
            summary
            for summary in (
                _file_summary(root, path, engine) for path in sorted(root.glob(f"*{FILE_SUFFIX}"))
            )
            if not summary["skipped"]
        ]
        _mark_duplicates(files)
        return {"dir": str(root), "files": files}

    @app.get("/api/files/{file_name}")
    def get_file(file_name: str) -> dict[str, Any]:
        path = resolve_json_path(root, file_name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在：{file_name}")
        try:
            spec = read_json(path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"JSON 解析失败：{exc}") from exc
        parse_spec(engine, spec)  # 未知 kind / 结构错 → 422，不进画布
        return {"file": file_name, "spec": spec, "dir": str(root)}

    @app.put("/api/files/{file_name}")
    def save_file(file_name: str, body: dict[str, Any]) -> dict[str, Any]:
        """保存到文件（新建与覆盖同一操作）。校验不过不落盘。

        覆盖既有文件时 code 不可就地改名（改名＝新增一份 + 删除旧文件，工具不做
        两件事的原子组合）；新建文件按约定命名为 ``<code>.json``。
        """
        path = resolve_json_path(root, file_name)
        definition = parse_spec(engine, body)
        # 同一 code 只能有一份文件：判断哪个文件装的是它，比文件名更可靠
        # （目录里可能有 04_expense_claim.json 这类带序号的命名）。
        siblings = [
            item["file"]
            for item in (
                _file_summary(root, other, engine) for other in root.glob(f"*{FILE_SUFFIX}")
            )
            if item["code"] == definition.code and item["file"] != path.name
        ]
        if siblings:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"code {definition.code} 已存在于 {'、'.join(siblings)}"
                    "——同一 code 只能有一份文件"
                ),
            )
        if path.is_file():
            try:
                existing = load_definition(read_json(path), engine.config.node_registry)
            except (OSError, ValueError, DefinitionError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"目标文件当前无法解析，先修复：{exc}"
                ) from exc
            if existing.code != definition.code:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"不支持就地改 code（文件里是 {existing.code}，提交的是 {definition.code}）"
                        "——请另存为新文件"
                    ),
                )
        elif definition.code != path.stem:
            raise HTTPException(
                status_code=422,
                detail=f"新建文件须命名为 <code>.json（当前 {path.name} / code {definition.code}）",
            )
        issues = engine.repository.validate(definition)
        errors = [issue for issue in issues if issue.level == IssueLevel.ERROR]
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "校验未通过，禁止保存", "issues": issues_payload(errors)},
            )
        write_json_atomic(path, _source_shape(engine, body))
        return {"file": file_name, "code": definition.code, "issues": issues_payload(issues)}
