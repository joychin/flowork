# 部署校验分层设计稿（A11，v2.3 第三批——待动工）

| | |
|---|---|
| 状态 | 设计稿（半页）——**待并行「等待节点」批落地后动工**（A11 主落点 `definitions/validator*.py` 正被其占用） |
| 依据 | [对标明细详设.md A11](./对标明细详设.md) + [delta-industry-benchmark.md §11](./delta-industry-benchmark.md)（组织实体引用全行业不校验＝部署期；Zeebe 部署期编译 FEEL＋校验同单元引用＝两条可抄的硬规则） |

## 四层与严重级别

| 层 | 内容 | 级别 | 现状 |
|---|---|---|---|
| L1 结构 | 节点/连线/起点/连通性 | ERROR | 已有 |
| L2 表达式语法 | CEL 静态检查 | ERROR（硬门禁，对齐 Zeebe hasValidExpression） | 已有（比 Flowable 强） |
| **L3 定义内引用** | `call_activity` 目标在**同一发布单元**；`service_task` handler 已注册 | **ERROR（新增）** | 缺 |
| **L4 外部实体引用** | 取人位字面量（人/角色/部门）存在性 | **默认 WARNING + strict 可选 ERROR** | 缺 |

L3 抄 Zeebe `ZeebeCalledElementDeploymentBindingValidator`（"Expected to find process with id '%s' in current deployment, but not found"）；建议引入 `bindingType` 语义：deployment 绑定＝强校验、latest 绑定＝只警告（定义可独立部署的配套）。handler 注册查 `HandlerRegistry`（在册即过）。

## L4 插口与形态

- `validate_definition(..., subject_exists: Callable[[str], bool | None] | None = None)`（沿用 org_configured/expression_functions 的「能力上下文」第三参数先例）；`False`→WARNING（strict 模式升 ERROR），`None`→静默跳过。
- 来源：deploy 路径 duck-typing 探测 `config.org.subject_exists`（OrgDirectoryLike **丰富面**加可选方法，不动 SPI 四方法定稿）；**做成部署期扩展点而非内核逻辑**——公开面暴露 validator 端口（输入定义+目录读取，输出 `findings[] {severity, code, position, message}`），默认注册内建目录校验器，宿主可注入自有校验（满足内核不 import contrib 铁律）。
- 字面量提取：`expr/cel.py` 新增 `string_literals(expression) -> list[str]`（lark AST，formula_outline 先例）；覆盖位＝`iter_expression_positions` 同源（assignee/assignees/candidate_users/recipients/reassign_to）。
- **明确不做**：变量运行期存在性（行业一致放弃；现状 FormulaError→挂起+故障单与 C8 incident 同构，写成 spec 对照说明）。

## 结果形态与发布语义

抄 Flowable `ValidationError`：分级（isWarning）+ 精确定位（JSON DSL 用节点/连线/位点路径，无 XML 行号）；warning 不拦发布（与 validator_graph 全 WARNING 一致）。`deploy` **始终跑 L1–L3**，校验报告作为发布产物（`flowork deploy` + designer 校验闭环已有位置）——「跳过校验」永远不是隐式默认（Flowable redeploy 默认跳两级校验是反面教训）。

**验收**：call_activity 指向未发布定义→deploy 拒绝并报缺哪个码；字面人不存在→WARNING（strict 下 ERROR）；None 钩子零噪音。
