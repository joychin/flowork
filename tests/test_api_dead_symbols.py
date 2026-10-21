"""公开面零消费守卫：flowork.api 每个符号都必须有真实消费方。

背景（2026-10-12 死符号收口批）：盘点发现 Clock / EventSink / NodeLifecycle
三个公开面符号全仓零消费、InstanceState 零引用（内核全用字符串字面量）——
它们被快照冻结在公开面里，名字却是给宿主的承诺，摆着不用会误导（与既有
「死端口收口」Notifier/ResourceProvider/WorkCalendar/VariableStore 同一类问题）。
本测试把盘点规则固化：新增公开面符号后若全仓无人引用，此处红灯。

判定（AST 绑定感知，避免同名不同物误判）：符号「被消费」＝ 存在一处 Load/属性
访问，其名字绑定到该符号的来源模块或 flowork.api 门面（含 `from flowork.api
import X`、`from <来源模块> import X`、`api.X`、`flowork.api.X` 四种形态）。
仅来源模块内部引用（构造返回模型/子类化基类，无需 import）不算外部消费——
这类符号走显式豁免清单（附理由），清单与实测必须严格互等（消费方出现后
豁免条目即陈旧，测试会逼你摘除）。
"""

from __future__ import annotations

import ast
from pathlib import Path

API_PATH = Path(__file__).resolve().parent.parent / "flowork" / "api.py"
REPO_ROOT = API_PATH.parent.parent
SKIP_DIRS = {
    ".venv",
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".v2c",
    ".zcode",
    "docs_research",
    "out",
    "dist",
    "dist-lib",
}

# 豁免：仓内无外部消费方属设计使然，但必须有明确理由登记。
# NotFoundError 曾有「仅 errors.py 自消费」形态，contrib/rest（并行在途）接线后
# 已有外部消费方——陈旧条目无害，保留登记（其作为基类被 isinstance 消费）。
# 判据方向为「实测 ⊆ 登记」：新出现未登记的零外部引用符号红灯（必须说明用途）。
_INTERNAL_CONSTRUCTED = {
    "FunctionEntry": (
        "engine_functions()/identifier_entries() 的返回模型（消费方只读 entry.name/label）"
    ),
    "JobClaim": "JobService 认领面返回模型（消费方只读 claim.job_id/lease 等字段）",
    "NotFoundError": "异常层级基类（消费方按 InstanceNotFoundError 等子类捕获）",
    "SimulationReport": (
        "RuntimeService.simulate 的返回报告模型（消费方按字段读 visited/decisions/notes，"
        "不构造实例）"
    ),
}

# 门面自定义符号（来源即 api.py）：消费方在设计上位于仓外（宿主/第三方插件），
# 快照已守卫其存在。本字典是「用途说明」清单，非豁免——未登记即红灯。
_FACADE_CONSTANTS = {
    "API_VERSION": (
        "门面契约版本常量（openface-plugin-design.md §4）：插件包以 "
        "REQUIRES_FLOWORK_API 声明依赖版本、兼容性断言在插件侧——仓内暂零消费方，"
        "属契约而非实现符号"
    ),
}

_EXEMPT = {**_INTERNAL_CONSTRUCTED, **_FACADE_CONSTANTS}


def _surface_origins() -> tuple[list[str], dict[str, tuple[str, str]]]:
    """解析 api.py：__all__ 符号清单 + 每个符号的来源（模块, 原名）。"""
    tree = ast.parse(API_PATH.read_text(encoding="utf-8"))
    surface: list[str] = []
    origins: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "__all__" for t in node.targets
        ):
            surface = [ast.literal_eval(e) for e in node.value.elts]
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("flowork"):
            for alias in node.names:
                origins[alias.asname or alias.name] = (node.module, alias.name)
    return surface, origins


def _scan_file(path: Path, surface: list[str], origins: dict[str, tuple[str, str]]):
    """扫一个文件：返回 {符号: [引用种类]}；引用种类 self=来源模块内、其余=外部消费。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return {}
    bindings: dict[str, tuple[str, str]] = {}  # local -> (module, orig)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bindings[alias.asname or alias.name] = (node.module, alias.name)
            if node.module == "flowork":
                for alias in node.names:  # from flowork import api（门面别名）
                    if alias.name == "api":
                        bindings[alias.asname or "api"] = ("flowork.api", "api")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "flowork.api":
                    bindings[alias.asname or "flowork"] = ("flowork.api", "api")

    rel = path.relative_to(REPO_ROOT)
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        base = None
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            base, symbol = node.id, node.id
        elif isinstance(node, ast.Attribute) and node.attr in surface:
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            symbol = node.attr
            if not isinstance(base, ast.Name):
                continue
        else:
            continue
        if symbol not in surface:
            continue
        origin = origins.get(symbol)
        if origin is None:
            continue  # 门面自身定义的常量（如 API_VERSION）：消费方在仓外，快照已守卫
        module, _ = origin
        origin_path = Path(*module.split(".")).with_suffix(".py")
        bound = bindings.get(base)
        if isinstance(node, ast.Name) and bound is None:
            kind = "self" if rel == origin_path else None  # 模块内直用（定义/构造）
        elif bound is not None and bound[0] in (module, "flowork.api"):
            kind = "ext"
        else:
            kind = None
        if kind:
            found.setdefault(symbol, []).append(kind)
    return found


def _repo_references(surface: list[str], origins: dict[str, tuple[str, str]]):
    """全仓扫描（排除 api.py 与本测试）：{符号: {external: n, self: n}}。"""
    counts = {s: {"ext": 0, "self": 0} for s in surface}
    this_file = Path(__file__).resolve()
    for path in REPO_ROOT.rglob("*.py"):
        if set(path.parts) & SKIP_DIRS or path.resolve() in (API_PATH.resolve(), this_file):
            continue
        for symbol, kinds in _scan_file(path, surface, origins).items():
            for kind in kinds:
                counts[symbol][kind] += 1
    return counts


def test_no_dead_public_symbols() -> None:
    """零消费符号红灯：删/改名（破坏性，走 major）或接线后重跑。"""
    surface, origins = _surface_origins()
    counts = _repo_references(surface, origins)

    # 门面自定义符号（来源＝api.py）：仓外消费，走 _FACADE_CONSTANTS 登记；
    # 方向为「实测 ⊆ 登记」——新出现未登记的门面符号红灯（性质是"必须说明用途"）；
    # 登记条目在仓内无消费方属常态（契约符号），过期条目不作红灯。
    facade_defined = {s for s in surface if origins.get(s) is None}
    assert facade_defined <= set(_FACADE_CONSTANTS), (
        "门面自定义符号未登记（在 api.py 定义且非 re-export 的符号须说明用途）：\n"
        f"  {sorted(facade_defined - set(_FACADE_CONSTANTS))}"
    )

    imported = [s for s in surface if origins.get(s) is not None]
    dead = [s for s in imported if counts[s]["ext"] == 0 and counts[s]["self"] == 0]
    self_only = {s for s in imported if counts[s]["ext"] == 0 and counts[s]["self"] > 0}

    assert not dead, (
        "公开面存在全仓零引用符号（死符号，与既有死端口收口同口径处理）：\n"
        + "\n".join(f"  {s}（来源 {origins.get(s)}）" for s in dead)
        + "\n处置：接线消费方，或从 __all__ 与快照移除（破坏性变更，记 AGENTS.md）。"
    )
    assert self_only <= set(_INTERNAL_CONSTRUCTED), (
        "仅来源模块自消费的符号未登记豁免（须附理由，说明为何无需外部引用）：\n"
        f"  {sorted(self_only - set(_INTERNAL_CONSTRUCTED))}\n"
        "处置：接线外部消费方，或登记进 _INTERNAL_CONSTRUCTED；两者皆非＝从公开面移除。\n"
        "（豁免条目在出现外部消费方后成为陈旧条目，无害、可随手摘除——不设红灯，"
        "避免对并行在途改动过敏。）"
    )
