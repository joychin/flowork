# AGENTS.md — 给编码代理（与人类协作者）的工程说明

> 本文件是仓库级约定入口。改代码前先读完本文件；与设计文档冲突时，以《本期建设边界》为准。

## 构建与测试命令

```bash
uv sync                                   # 安装依赖（Python >= 3.11，uv 管理）
uv run pytest                             # 全量测试（必须全绿才能提交）
uv run pytest --cov=flowork --cov-report=term-missing   # 覆盖率（内核包目标 >= 90%）
uv run ruff check flowork tests examples  # 静态检查
uv run ruff format flowork tests examples # 格式化（提交前）
uv run mypy flowork                       # 类型检查（strict）
uv run python examples/ai_review_demo.py  # 自定义节点 demo
```

提交规范：conventional commits（`feat:` / `fix:` / `test:` / `docs:` / `chore:`），每任务一提交。
**四道门全绿才允许提交：pytest + ruff check + ruff format --check + mypy。**

## 包结构地图（一屏）

```
flowork/
├── definitions/   定义层：node(FlowNode基类) flow(连线/条件) builtin(内置Spec)
│                  definition(模型+JSON编解码) validator(设计时校验) builder(Python DSL)
├── runtime/       运行时：behavior(NodeBehavior+NodeContext) registry(注册表) lifecycle(状态枚举)
├── expr/          条件求值：conditions(结构化条件组；M2 增公式沙箱)
├── spi/           端口协议：Clock/WorkCalendar/OrgDirectory/VariableStore/Notifier
├── events/        领域事件类型（M1 起随 Command 管线落 outbox）
├── tasks/ participants/ persistence/ engine/ jobs/ cli/   # M1+ 各里程碑填充
├── errors.py      异常体系
tests/             definitions/(模型/校验/DSL等价) runtime/(注册表) + 顶层条件/演示测试
examples/          3 个模板 JSON + ai_review_demo.py
```

## 硬约束（违反即打回）

1. **命名**：遵循《命名规范》（benchmark_refs/out/命名规范.md）。禁用 对标产品 名
   （Biz*/分词命名/错拼Service/*Impl 等）；标识符一律英文；类禁 Impl/Factory/Manager/Util 后缀。
2. **引擎核心不得出现节点 kind 的 if/else 分支**——节点语义只经 NodeRegistry → NodeBehavior。
3. **钩子/行为只观察与写变量，不得改变路由**；路由决策只属于引擎 + 条件求值。
4. **TaskHistory（已办）只追加**，驳回复活 = 新任务 + cause_token_id。
5. **所有操作带 actor 归因**（acted_by / acted_as_agent）。
6. 单文件 ≤ 300 行；测试先行于实现（会签/驳回/汇聚等难块）。
7. 范围变化一律记入《本期建设边界》backlog 附录，不直接扩期。

## 设计文档（benchmark_refs/out/，只读）

边界（范围权威）→ 实施规划 → 命名规范 → 节点抽象与扩展设计 → AI友好设计 → 主流流程引擎借鉴分析。
语义参考：benchmark_refs/out/基准材料/（对标产品 行为基准源码，clean-room：只对齐行为，不搬代码/命名）。

## 当前里程碑

M0–M6 全部完成（headless 内核完结）：153 个测试、mypy strict 零错误、CLI demo-full 通过最终 DoD。
后续（二期）：FastAPI 服务层、pyflow-mcp、AI 行为包、UI、实例迁移（M7）。
