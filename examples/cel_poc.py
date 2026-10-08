"""P0-1 CEL PoC（kernel-v2 spec §4.3 / §16）：cel-python 可用性验证与性能基线。

运行：``uv run python examples/cel_poc.py``。逐项验证 cel-python（模块名 ``celpy``）
的四类能力并输出 PASS/FAIL/WARN 报告，结论与缓解措施记录在
``docs/product/kernel-v2/p0-1-cel-poc.md``（换绑决策记录）。

- 函数注册：宿主注册取人词汇（manager_of / users_with_role 形态）；
- 编译期拦截：语法错误在 compile 报 CELParseError；
- 静态检查现状：未声明变量/未知函数**只在运行期**报 CELEvalError（缺口，需自建薄层）；
- 健壮性：链式表达式递归悬崖（RecursionError）与数据侧 comprehension 病态；
- 性能：PoC 时与当时的 asteval 沙箱同条件对比（asteval 基线已随换绑退役，数字仅作历史记录）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import celpy

Results = list[tuple[str, str, str]]  # (verdict, item, note)


def activation(payload: dict) -> dict:
    """构造激活对象：值须转 celtypes 才支持字段选择（input.amount）。"""
    return {key: celpy.json_to_cel(value) for key, value in payload.items()}


def check(results: Results, item: str, fn: Callable[[], str]) -> None:
    """执行一项验证：fn 返回备注（前缀 ``WARN：`` 记警告）；异常即 FAIL。"""
    try:
        note = fn()
        verdict = "WARN" if note.startswith("WARN：") else "PASS"
        results.append((verdict, item, note))
    except Exception as exc:
        results.append(("FAIL", item, f"{type(exc).__name__}: {exc}"))


def main() -> int:
    results: Results = []
    env = celpy.Environment()

    # —— 1. 基本求值与命名空间（spec C.4：input.* / vars.* / initiator）——
    def _basic() -> str:
        program = env.program(env.compile('input.amount > 300 && input.urgency == "high"'))
        hit = program.evaluate(activation({"input": {"amount": 500, "urgency": "high"}}))
        miss = program.evaluate(activation({"input": {"amount": 100, "urgency": "high"}}))
        assert bool(hit) is True and bool(miss) is False, f"{hit}/{miss} 非布尔语义"
        return (
            "input.* 字段选择、&& 短路、布尔结果正确；"
            "注意返回 celtypes.BoolType（int 子类非 bool），适配层须归一化"
        )

    check(results, "基本求值/命名空间", _basic)

    # —— 2. 自定义函数注册（取人词汇形态，spec 5.5）——
    def _functions() -> str:
        org = {"user:zhang": "user:li"}

        def manager_of(user: str) -> str | None:
            return org.get(user)

        def users_with_role(role: str) -> list[str]:
            return [f"user:{role}-1", f"user:{role}-2"] if role else []

        program = env.program(
            env.compile('manager_of(initiator) == "user:li"'),
            functions={"manager_of": manager_of},
        )
        assert bool(program.evaluate(activation({"initiator": "user:zhang"})))
        program2 = env.program(
            env.compile('users_with_role("finance").size() >= 2'),
            functions={"users_with_role": users_with_role},
        )
        assert bool(program2.evaluate(activation({})))
        return "manager_of(one_user)→one_user、users_with_role(string)→users 注册与调用通过"

    check(results, "函数注册（取人词汇）", _functions)

    # —— 3. 编译期语法拦截 ——
    def _syntax() -> str:
        try:
            env.compile("input.amount >&! 300")
        except celpy.CELParseError:
            return "语法错误在 compile 期抛 CELParseError"
        raise AssertionError("语法错误未被编译期拦截")

    check(results, "编译期语法拦截", _syntax)

    # —— 4. 静态检查现状：未声明变量 / 未知函数 ——
    def _undeclared() -> str:
        program = env.program(env.compile("undeclared_var > 1"))  # 编译通过
        try:
            program.evaluate(activation({}))
        except celpy.CELEvalError:
            return "WARN：编译期放行，运行期报 CELEvalError——需自建 AST 标识符薄层（见决策记录）"

    check(results, "未声明变量（静态检查）", _undeclared)

    def _unknown_fn() -> str:
        env.program(env.compile("nosuchfn(x)"))  # 编译通过
        try:
            env.program(env.compile("nosuchfn(x)")).evaluate(activation({"x": 1}))
        except celpy.CELEvalError:
            return "WARN：未知函数同为运行期报错——静态薄层须同时覆盖函数名表"

    check(results, "未知函数（静态检查）", _unknown_fn)

    # —— 5. 健壮性：递归悬崖（2KB 限长内的链式表达式）——
    def _recursion() -> str:
        values = {f"v{i}": i for i in range(10)}
        crashed_at = None
        for terms in range(50, 401, 50):
            expr = " + ".join(f"v{i % 10}" for i in range(terms))
            try:
                env.program(env.compile(expr)).evaluate(activation(values))
            except RecursionError:
                crashed_at = (terms, len(expr))
                break
        if crashed_at is None:
            return "50–400 项链式表达式均未触发递归悬崖"
        terms, size = crashed_at
        return (
            f"WARN：{terms} 项（{size}B）链式加法触发 RecursionError——"
            "求值须捕获并转 FormulaError；长度上限 2KB 建议配合项数护栏（见决策记录）"
        )

    check(results, "递归悬崖（2KB 内链式）", _recursion)

    # —— 6. 数据侧病态：大集合 comprehension ——
    def _data_size() -> str:
        program = env.program(env.compile("big_list.exists(x, x > 1000000)"))
        elapsed: list[float] = []
        for size in (10_000, 100_000):
            start = time.perf_counter()
            program.evaluate(activation({"big_list": list(range(size))}))
            elapsed.append((time.perf_counter() - start) * 1e3)
        return (
            f"WARN：exists over 10k/100k 元素 = {elapsed[0]:.0f}/{elapsed[1]:.0f} ms，"
            "线性于数据规模且无进程内硬超时——靠变量 1MB 上限间接约束，硬超时列已知缺口"
        )

    check(results, "数据侧 comprehension 病态", _data_size)

    # —— 7. 性能对比：celpy 裸 program vs 正式 evaluate_formula（护栏+compile 开销）——
    def _perf() -> str:
        from flowork.expr.cel import evaluate_formula

        program = env.program(env.compile('input.amount > 300 && input.urgency == "high"'))
        rounds = 1_000
        start = time.perf_counter()
        for i in range(rounds):
            program.evaluate(activation({"input": {"amount": i, "urgency": "high"}}))
        cel_us = (time.perf_counter() - start) / rounds * 1e6

        start = time.perf_counter()
        for i in range(rounds):
            evaluate_formula('amount > 300 && urgency == "high"', {"amount": i, "urgency": "high"})
        wrapped_us = (time.perf_counter() - start) / rounds * 1e6
        return (
            f"celpy {cel_us:.1f} µs/次 vs evaluate_formula（含护栏+compile）{wrapped_us:.1f} µs/次"
            f"（{wrapped_us / cel_us:.1f}×；asteval 基线已随换绑退役）"
        )

    check(results, "性能（裸 celpy vs 正式入口）", _perf)

    # —— 报告 ——
    print(f"cel-python PoC（celpy {getattr(celpy, '__version__', '0.5.x')}）\n")
    for verdict, item, note in results:
        print(f"[{verdict:<4}] {item}\n       {note}")
    failed = [r for r in results if r[0] == "FAIL"]
    warned = [r for r in results if r[0] == "WARN"]
    print(
        f"\n结论：PASS {len(results) - len(failed) - len(warned)} / "
        f"WARN {len(warned)} / FAIL {len(failed)}"
    )
    print("换绑决策与缓解措施：docs/product/kernel-v2/p0-1-cel-poc.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
