# flowork

无头（headless）、可扩展的审批流程引擎。以 Token 令牌模型为内核，命名对齐 BPMN 词汇；
先建引擎（定义/执行/持久化/查询/事件），UI、消息推送、HTTP 服务后续在内核上加。

## 快速开始

```bash
uv sync                  # 安装依赖（Python >= 3.11）
uv run pytest            # 运行测试
uv run python examples/ai_review_demo.py   # 自定义节点 demo（AI 预审 → 路由/升级人工）
```

## 里程碑

- **M0（当前）**：定义模型（节点/连线/条件）、JSON 模板与 Python DSL 等价、设计时校验、
  节点扩展机制（NodeRegistry + NodeBehavior）、ai_review demo。
- M1：执行内核（Command 管线、SQLite 持久化、发起→审批→结束主干、事件 outbox）。
- M2–M6：网关/会签/驳回、任务操作全集、参与者体系、子流程/超时/异常、查询/CLI/打包。
  详见《本期建设边界》（设计文档索引见 llms.txt）。

## 扩展一种节点类型（三步，不改引擎核心）

```python
from flowork.definitions.node import FlowNode
from flowork.runtime.behavior import NodeBehavior

class MySpec(FlowNode):
    kind: str = "my_node"
    my_field: str = ""

class MyBehavior(NodeBehavior):
    def on_enter(self, ctx):
        ctx.variables["my_result"] = "done"

node_registry.register("my_node", MySpec, MyBehavior)
```

## 更多

- 构建约定与包结构：`AGENTS.md`
- 文档索引：`llms.txt`

## 流程设计器(Web UI)

`designer/` 提供 React Flow 可视化设计器(草稿 → 校验 → 发布生命周期、schema 驱动属性表单、
registry 驱动调色板、Spec JSON 直编辑),后端为 `flowork/web/` 的薄 FastAPI:

```bash
FLOWORK_DB=/tmp/flowork-designer.db uv run uvicorn flowork.web.app:app --port 8901  # 引擎 API
cd designer && npm install && npm run dev                                            # http://localhost:5173
```

详见 `designer/README.md`。
