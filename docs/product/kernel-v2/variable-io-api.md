# 变量读写接口设计（variable I/O）

| | |
|---|---|
| 对应 spec | [spec.md](./spec.md) §10（variables_updated 事件）、§11（实例组三方法）、§12（/v2 端点登记）、§13（MCP 工具）、§14（wf_variable_history） |
| 日期 | 2026-10-08 |
| 状态 | 设计定稿（2026-10-08 批准实施：docs → 留痕基建 → 读写命令 → 死端口清理 → CLI → 黑盒验收，六批） |
| 决策 | **本体全部在内核 Python API；REST 随 /v2 批、MCP 随 M7（本批仅规格登记）；本批出 CLI 投影** |

## 1. 定位与动机

独立部署引擎的买家是别的系统（ERP/OA/RPA/集成商），不是人。典型场景：审批等待中，
外部风控系统刷新客户信用分，后续分支按新值走。引擎此前只有写路径（发起/任务提交/
消息/映射），**读面与受控写面双双缺位**——本设计一次补齐，读写一起开。

分层原则（评审定稿）：内核原生脸只有 Python API、领域事件、SPI 端口三张；
REST/MCP/CLI 是同一能力的薄投影。本批本体 = RuntimeService 三方法 + 一张留痕表 +
一个事件；投影顺序 CLI 先行，REST 随 /v2 批（错误体/分页/鉴权姿势整批定），
MCP 随 M7。

## 2. 领域语义（写命令）

- **合并写**：`update_variables(instance_id, updates, *, actor, as_agent=False, reason="")`
  按 PATCH 语义只改给定键，不提供删键。为什么不整表替换：任务提交/钩子并发也在写
  变量，整表替换会覆盖邻键。
- **状态门槛**：非终态（running / paused / suspended）可写——外部改值是数据维护不是
  流程动作，暂停中改值恰是刚需（恢复后按新值路由）；终态（completed / terminated /
  cancelled / failed）拒绝，循 pause 惯例抛 `InstanceNotFoundError`（/v2 批再评估
  409 语义）。
- **生效时机**：不触发任何路由重算，只影响**后续**求值位点；已走过的分支不倒带。
  此句写进 REST/MCP 接口文档。
- **并发**：走命令管线，实例行 version_id_col 乐观锁 + execute_command 重试天然覆盖；
  同 body 重放收敛（幂等）。

## 3. 三道保险

1. **留痕**：`wf_variable_history` 追加只写表（instance_id, name, old_value, new_value,
   source, acted_by, acted_as_agent, created_at），**全写方覆盖**——任务提交、钩子
   原地写、子流程映射、外部命令都进（命令尾 diff 产生，值相等跳过）；归因
   source=external_update / task_output / task_reject / engine。
2. **校验**：三门槛。① 白名单：键 ∈ `variables[]` 声明 ∪ input schema 属性 ∪ 实例
   既有键（任务运行期写入的键可改），其余拒（`InputContractError`）；保留标识符
   `initiator` / `task` 一律拒。② 类型对位：声明类型的键按 string/number/boolean/list
   校验；合并结果复用 `validate_input_contract(boundary="external_update")`。
   ③ 尺寸：updates 序列化 ≤ 256KB（实例变量 1MB 总量约束的写入侧配套）。
3. **权限**：写需 scope `instances:variables`（engine-v1 02-权限模型已登记；默认只授
   集成凭证，不进 member 包）；读沿用实例可见性（发起人∪历史参与人∪instances:read），
   越权 404 优先。凭证体系（M-A）落地前，actor 显式传参（CLI `--as` 即现状先例），
   web/MCP 接线时换成凭证→principal。

## 4. 事件策略（修订 spec §10 原条款）

原条款「变量值变化不发事件（防刷屏）」修订为「**引擎内部写变量不发事件**」：
任务提交/钩子/映射的变量变更走留痕表（血缘查询可得），不进事件流；外部变量更新
命令**例外发 `variables_updated`**（低频高关注，订阅方需要对外部数据注入做出反应），
payload：`{instance_id, actor, as_agent, reason?, changes: [{name, old, new}]}`。

## 5. 读面

- `variables_of(instance_id) -> dict`：值全集（含运行期键），只读直查不进命令管线。
- `variable_history(instance_id, *, name=None, limit, before=None)`：游标分页
  （仿 paged_tasks），血缘查询——「这个值谁在什么时候改的」一查便知。

## 6. 实现挂点（开工者地图）

- 快照捕获：`CommandContext` 惰性 deepcopy before 快照，咽喉点两处——`touch()` 与
  `Kernel.record_event`（钩子路径不经过 touch，必须双覆盖）；开销受 1MB 变量上限约束。
- diff 挂点：`ops._run_once` 在 `_sync_variable_index` 与 `_flush_history` 之间加
  `_sync_variable_history`（顺序保证事件能进 outbox）。
- 命令实现：`engine/variable_actions.py` mixin 并入 RuntimeService，仿 pause 四步套路；
  复用 `_load` / `CommandContext.touch` / `validate_input_contract` / `input_schema_of`。
- 建表：`orm_v2.py` 新 Row 挂既有 Base，`create_all` 幂等补建（Alembic 基线前的常态）。

## 7. 开放问题（不阻塞本批）

- 历史表无清理策略——实例数据保留策略本身未定，挂 v2。
- 事件 payload 含全量旧/新值——敏感字段脱敏挂字段级权限矩阵（engine-v1 已挂 v2）。
- `variables[]` / `input` / business_object 三契约合一——另案规格工作。
- REST `/v2` 实施时若引入 `StateConflictError(409)`，终态拒错误迁移随之。
