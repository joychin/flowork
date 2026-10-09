# 模拟试跑设计稿（A10，v2.3 第三批——待动工）

| | |
|---|---|
| 状态 | 设计稿（半页）——**待并行「等待节点」批落地后动工**（A10 主落点 `tasks/behaviors.py` 正被其占用） |
| 依据 | [对标明细详设.md A10](./对标明细详设.md) + [delta-industry-benchmark.md §10](./delta-industry-benchmark.md)（三物种分层是行业对拍结论） |

## 两种模拟，两个层

**第一层 · 令牌模拟（零副作用，dry_run 的延伸）**：不调用 handler、不写 outbox/事件、不建实例；条件边**默认不求值**（用显式选择，等价 bpmn-js-token-simulation 的 activeOutgoing——**此层不能当验证器**，业界明确令牌模拟验证不了表达式）；输出＝全路径枚举 + 逐边走向（参考 jBPM "find all possible paths"、SpiffWorkflow 的 PREDICTED 预测）。落点：`definitions/dry_run.py` 扩展。

**第二层 · 打桩真执行（simulated 实例）**：
- `ProcessInstanceRow.simulated: bool = False`（§14 迁移一列）+ `RuntimeService.start_process(..., simulated=True)` 或独立 `engine.simulate(definition, variables) -> SimulationReport`；
- 行为规则：user_task 进入即自动完成（outcome="simulated"、actor=system，**不走 auto_pass 票数语义**）；service_task **必须 stub**（行业共识：模拟绝不执行真实副作用——这是与 对标产品 灰区的关键分野），记 node_stubbed 事件；cc_task 自动已阅；call_activity 递归模拟；**不建 deadline job**；定时器可跳过（Clock 注入，Temporal time skipping 同款）；
- 条件走向由「初始变量+路由条件」真求值（与第一层相反）。

## 隔离与报告（两处行业空白）

- **模拟实例一等公民**：查询面默认过滤 simulated + **硬性禁写**（不投 outbox、不写真实分区变量历史）+ 生产护栏（可配置直接拒绝——C8 Test mode 真跑真数据只能警告，flowork 引擎层根治）；幂等键加 `sim:` 前缀豁免；清理挂 CLI（`purge --simulated --before N天`）。
- **报告复用 route_audit**：模拟跑完 `wf_route_decision` 天然是逐步路由轨迹（F3 资产协同）；SimulationReport＝终态+token 时间线+**stub 节点清单**（覆盖度诚实可见）+卡点（all 汇聚不放行即停——本身就是发现）。

## 边界（写进文档防误解）

模拟不验证表达式（要验证走第二层注入变量）；script 注入（`{node_code: {输出变量: 值}}`）为二期可选。可选后置：JSON test case 资产格式（C8 CPT 形态，可版本化可 AI 生成）。

**验收**：52 节点大图模拟跑通并产出轨迹；simulated 实例不出现在默认查询；service_task 无真实 handler 调用（以 stub 事件计数）。
