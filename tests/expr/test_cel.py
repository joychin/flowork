"""expr/cel.py：CEL 公式求值与静态引用收集（kernel-v2 §4.3，P0-1 决策落地）。

护栏四件套（2KB / 链项 ≤256 / 变量 ≤1MB / 异常归一）与
collect_references（标识符 + 函数名→参数个数）的单元覆盖。
"""

from __future__ import annotations

import pytest

from flowork.expr.cel import (
    MAX_BINARY_OPS,
    FormulaError,
    collect_references,
    evaluate_formula,
    evaluate_formula_value,
    root_call,
)

# —— 基本语法与语义 ——


@pytest.mark.parametrize(
    ("formula", "variables", "expected"),
    [
        ("amount > 100", {"amount": 2000}, True),
        ("amount > 100", {"amount": 50}, False),
        ('amount >= 1000 && level == "L2"', {"amount": 1000, "level": "L2"}, True),
        ('amount >= 1000 && level == "L2"', {"amount": 999, "level": "L2"}, False),
        ("a in [1, 2, 3]", {"a": 2}, True),
        ("a in [1, 2, 3]", {"a": 9}, False),
        ("items.size() > 1", {"items": [1, 2]}, True),
        ("!urgent || amount < 10", {"urgent": True, "amount": 5}, True),
        ("flag == true", {"flag": True}, True),
        ("x == null", {"x": None}, True),
        (
            'input.amount > 300 && input.urgency == "high"',
            {"input": {"amount": 500, "urgency": "high"}},
            True,
        ),
    ],
)
def test_cel_syntax_and_semantics(formula, variables, expected):
    assert evaluate_formula(formula, variables) is expected


def test_result_normalized_to_python_bool():
    """celtypes.BoolType（int 子类）必须收敛为 Python bool。"""
    result = evaluate_formula("amount > 1", {"amount": 2})
    assert type(result) is bool


def test_non_boolean_result_converged():
    """非布尔结果按决策记录 bool(...) 收敛（类型级静态检查属 validator 层）。"""
    assert evaluate_formula("amount", {"amount": 2000}) is True


def test_custom_functions_injection():
    def manager_of(user: str) -> str:
        return "user:li" if user == "user:zhang" else "user:wang"

    assert evaluate_formula(
        'manager_of(initiator) == "user:li"',
        {"initiator": "user:zhang"},
        functions={"manager_of": manager_of},
    )
    assert not evaluate_formula(
        'manager_of(initiator) == "user:li"',
        {"initiator": "user:other"},
        functions={"manager_of": manager_of},
    )


# —— 异常归一（全部 → FormulaError）——


@pytest.mark.parametrize(
    ("formula", "variables"),
    [
        ("amount >", {"amount": 1}),  # 语法错误（CELParseError）
        ("nosuchvar > 1", {}),  # 未声明变量（CELEvalError）
        ("nosuchfn(1) == 2", {}),  # 未注册函数（CELEvalError）
        ("amount / zero > 1", {"amount": 1, "zero": 0}),  # 求值错（除零）
        ("input.amount > 1", {"input": 3}),  # 非法字段选择（int 无字段）
    ],
)
def test_errors_normalized_to_formula_error(formula, variables):
    with pytest.raises(FormulaError):
        evaluate_formula(formula, variables)


def test_recursion_error_normalized(monkeypatch):
    """RecursionError 归一 FormulaError（链项护栏之前的兜底）。"""
    import flowork.expr.cel as cel_mod

    def _boom(*args: object, **kwargs: object) -> object:
        raise RecursionError("deep")

    monkeypatch.setattr(cel_mod.celpy.Environment, "compile", _boom)
    with pytest.raises(FormulaError):
        evaluate_formula("amount > 1", {"amount": 1})


def test_empty_formula_rejected():
    with pytest.raises(FormulaError):
        evaluate_formula("   ", {})


# —— 护栏（P0-1 决策四件套）——


def test_guard_formula_length_2kb():
    from flowork.definitions.flow import MAX_FORMULA_LENGTH

    assert MAX_FORMULA_LENGTH == 2048
    long_formula = "1 + " * 700 + "1"  # ~2800B > 2048
    with pytest.raises(FormulaError, match="长度"):
        evaluate_formula(long_formula, {})


def test_guard_binary_chain_256():
    """二元运算链 ≤256 项（PoC 实测 350 项触发 RecursionError，256 留余量）。"""
    terms = MAX_BINARY_OPS + 2  # 257 项 → 256 次运算 > 上限
    chain = " + ".join([str(i % 10) for i in range(terms)])
    assert len(chain) < 2048  # 确认未先触发长度护栏
    with pytest.raises(FormulaError, match="链"):
        evaluate_formula(chain, {})


def test_guard_binary_chain_boundary_ok():
    """恰好 256 项（255 次运算）放行（求值预算由 cel.py 统一保证）。"""
    terms = MAX_BINARY_OPS
    chain = " + ".join(["1"] * terms)
    assert evaluate_formula(chain, {}) is True


def test_guard_variables_1mb():
    with pytest.raises(FormulaError, match="变量"):
        evaluate_formula("amount > 1", {"blob": "x" * 1_100_000})


# —— collect_references（静态检查薄层的原料）——


def test_collect_identifiers_and_root_only():
    idents, functions = collect_references('amount > 100 && level == "L2"')
    assert idents == {"amount", "level"}
    assert functions == {}


def test_collect_field_access_takes_root_only():
    idents, _ = collect_references("input.amount > 300")
    assert idents == {"input"}


def test_collect_function_calls_and_methods():
    idents, functions = collect_references(
        'manager_of(initiator) == "user:li" && users_with_role("f").size() >= 2'
    )
    assert idents == {"initiator"}
    assert functions == {"manager_of": {1}, "users_with_role": {1}}  # size() 是方法不计


def test_collect_zero_arg_and_multi_arg_calls():
    _, functions = collect_references("now() && clamp(a, 1, 9) > 3")
    assert functions == {"now": {0}, "clamp": {3}}


def test_collect_comprehension_binding_excluded():
    """宏绑定变量（exists 的 x）不算标识符引用。"""
    idents, functions = collect_references("big.exists(x, x > 1) || a.exists(y, y > 2)")
    assert idents == {"big", "a"}
    assert functions == {}  # 方法形态的宏不计全局函数


def test_collect_has_macro():
    idents, functions = collect_references("has(a.b)")
    assert idents == {"a"}
    assert functions == {"has": {1}}


def test_collect_parse_error_raises():
    with pytest.raises(FormulaError):
        collect_references("amount >")


# —— evaluate_formula_value（值位点：映射/取人/试算，不收敛 bool）——


@pytest.mark.parametrize(
    ("formula", "variables", "expected"),
    [
        ("'u_manager'", {}, "u_manager"),  # 字符串字面量（取人位字面 id 形态）
        ("amount", {"amount": 2000}, 2000),  # 标识符直取（映射位同名直传）
        ("amount * 2", {"amount": 21}, 42),  # 运算表达式
        ("['u_a', 'u_b']", {}, ["u_a", "u_b"]),  # 列表字面量（取人位多解）
        ("x", {"x": None}, None),  # null 原样返回
    ],
)
def test_value_formula_returns_raw(formula, variables, expected):
    assert evaluate_formula_value(formula, variables) == expected


def test_value_formula_custom_function_list_passthrough():
    """注入函数返回列表原样透出（取人位 users 词汇的运行形态）。"""
    result = evaluate_formula_value(
        'users_with_role("finance")', {}, functions={"users_with_role": lambda r: ["u_a", "u_b"]}
    )
    assert result == ["u_a", "u_b"]


def test_value_and_condition_diverge_on_non_bool():
    """同一公式：条件位 bool 收敛，值位返回原值（两变体的本质差异）。"""
    assert evaluate_formula("amount", {"amount": 2000}) is True
    assert evaluate_formula_value("amount", {"amount": 2000}) == 2000


def test_value_formula_shares_guardrails():
    with pytest.raises(FormulaError, match="长度"):
        evaluate_formula_value("1 + " * 700 + "1", {})
    with pytest.raises(FormulaError, match="变量"):
        evaluate_formula_value("amount", {"blob": "x" * 1_100_000})
    with pytest.raises(FormulaError):
        evaluate_formula_value("nosuchvar", {})


def test_non_ascii_variable_names_do_not_poison_evaluation():
    """中文变量名不可被 CEL 引用，但也不得牵连同上下文的其他求值（celpy Invalid name 规避）。"""
    assert evaluate_formula("amount > 100", {"amount": 200, "金额": 1200})
    assert evaluate_formula_value("'张三'", {"金额": 1200}) == "张三"


# —— root_call（取人位根调用类型检查的原料）——


def test_root_call_detects_global_call():
    name, arity = root_call('users_with_role("f")')  # type: ignore[misc]
    assert (name, arity) == ("users_with_role", 1)
    assert root_call("manager_of(initiator)") == ("manager_of", 1)
    assert root_call("no_args()") == ("no_args", 0)
    assert root_call("has_role(a, b)") == ("has_role", 2)


def test_root_call_none_for_non_call_root():
    assert root_call("'u_a'") is None
    assert root_call("initiator") is None
    assert root_call("manager_of(initiator) + extra") is None
    assert root_call("amount > 1 && ok") is None


def test_value_formula_bool_is_native_bool():
    """BoolType（int 子类）收敛为原生 bool——JSON 序列化不能变 1。"""
    result = evaluate_formula_value("amount > 1", {"amount": 2})
    assert type(result) is bool and result is True
