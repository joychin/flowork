# P0-1 CEL PoC 换绑决策记录

| | |
|---|---|
| 对应 spec | [spec.md](./spec.md) §4.3（CEL 求值语义）、§16 P0-1 |
| 日期 | 2026-10-07 |
| 复现脚本 | `uv run python examples/cel_poc.py`（PASS 4 / WARN 4 / FAIL 0） |
| 决策 | **采用 cel-python==0.5.0（模块名 `celpy`），不换绑** |

## 验证结果（2026-10-07，macOS arm64，CPython 3.12）

| 维度 | 结果 | 证据 |
|---|---|---|
| 自定义函数注册 | ✅ PASS | `manager_of(one_user)→one_user`、`users_with_role(string)→users` 以 `functions={name: fn}` 注入，取人词汇形态可用 |
| 编译期语法拦截 | ✅ PASS | 语法错误在 `Environment.compile` 抛 `CELParseError` |
| 静态类型检查 | ⚠️ 缺口 | 未声明变量、未知函数**编译期放行**，运行期才报 `CELEvalError`；`annotations` 参数不做静态约束 |
| 健壮性 | ⚠️ 两处 | ①链式表达式递归悬崖：350 项（1747B）链式加法触发 `RecursionError`；②数据侧病态：`exists` 遍历 10k/100k 元素 = 333ms/3.4s，线性于数据规模，无进程内硬超时 |
| 性能 | ✅ PASS | 典型路由条件 81µs/次（asteval 现状 31µs，2.6×）；编译+程序化 0.07ms/次。路由决策频次下绝对量级可忽略 |
| 值类型适配 | ✅（记录） | 求值返回 `celtypes.BoolType`（int 子类非 bool）、入参须 `json_to_cel` 转换（裸 dict 不支持字段选择）——适配层职责 |

## 备选对照

- **cel-expr-python==0.1.3**：存在于 PyPI 但版本过早期（0.1.x），成熟度远低于 cel-python 0.5.0。留作 fallback，不采用。
- PyPI 上同名 `celpy` 包为 0.0.1 占位，**勿装**——正确入口是 `cel-python`。

## 落地缓解（进 `expr/cel.py` 的实现约束，P0-2/P0-4 联动）

1. **静态检查自建薄层**：`celpy` 暴露 lark AST（`Environment.compile` 返回 `Expression`），
   自建「标识符/函数名收集器」对照 input JSON Schema 声明 + `ExpressionFunctions` 端口
   注册表（含签名：参数个数 + 类型词汇 one_user/users/groups/mixed/bool），设计期报
   「引用未声明变量/未注册函数/参数个数不符」。类型级细化（表达式中间类型推断）首版
   不做，运行期 `CELEvalError` 兜底——与 spec §5.5「检查强度诚实声明」一致。
2. **递归悬崖**：求值入口 `except RecursionError` 归一为 `FormulaError`；设计期长度上限
   2KB（spec §4.3）保留，另加「二元运算链项数 ≤ 256」护栏（350 项才崩，256 留余量，
   正常条件式远达不到）。
3. **硬超时缺口（诚实声明）**：celpy 解释器无法在进程内安全中断。缓解组合 =
   2KB 限长 + 非图灵完备（无循环/递归调用）+ 变量序列化 1MB 上限（间接约束
   comprehension 可遍历的集合规模，1MB 纯 int 列表 ≈ 15 万元素 ≈ 5s 上界）+
   全异常捕获转 `FormulaError`。若未来出现真实超时需求，升级路径为子进程隔离开销
   ~10–30ms/次，接口不变（`evaluate(formula, variables, timeout=…)`）。
4. **适配层归一化**：入参经 `json_to_cel`、结果 `bool(...)` 收敛为 Python bool；
   `initiator` 作为顶层标识符注入激活。

## 对后续开工项的接口影响

- P0-2（SPI 定稿）：`ExpressionFunctions.functions()` 返回的注册表**必须带签名**，
  否则静态薄层无从对照——端口形状按 spec §9，实现侧补 `FunctionSignature` 数据类。
- 换绑本身（`expr/cel.py` 替换 `expr/formula.py` 的 asteval 一支）**不在 P0-1 交付**，
  与 P0-2 端口一并落地（2026-10-08 完成）；原「`groups` 结构化条件不动」已被
  v2.3 单轨化决策取代（groups 退役，见 spec §4.1）。
