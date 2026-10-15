# flowork

可独立部署的审批流程引擎。以 Token 令牌模型为内核，CEL 表达式单轨；
Python API / 事件 / SPI 端口为原生脸，web（薄 FastAPI）与流程设计器（React Flow）
已可用，REST /v2 与 MCP 开放面按 kernel-v2 spec 推进中。

分支相关只有三个用户词汇（不暴露 BPMN「网关」）：

| 用户词汇 | 配置位置 | 说明 |
|---|---|---|
| **条件分支**（kind=`branch`） | 出边挂条件 | `route_mode=first_match` 命中即走（按顺序取第一条）/ `all_match` 命中的都走 |
| **同时进行** | 多条无条件出边 | 无需专门节点，引擎默认全部激活 |
| **开始条件**（`advance_policy`） | 多入边节点 | `any` 任一前置分支到达即开始（缺省，竞速并取消兄弟挂起分支）/ `all` 等全部到齐 |

## 快速开始

```bash
uv sync                  # 安装依赖（Python >= 3.11）
uv run pytest            # 运行测试
uv run python examples/ai_review_demo.py   # 自定义节点 demo（AI 预审 → 路由/升级人工）
```

## 里程碑

- **M0–M6（完成）**：headless 内核完结——定义模型/校验、执行内核（Command 管线 +
  SQLite 持久化 + 事件 outbox）、条件分支/会签/驳回、任务操作全集、参与者体系、
  子流程/超时/异常、查询/CLI；单实例强化（乐观锁全覆盖、Job CAS 原子认领、
  统一时钟 naive UTC）。
- **内核 v2（P0/P1 完成）**：CEL 换绑与全位点单轨、SPI 定稿、CloudEvents outbox、
  major/revision 版本判定、启动幂等三策略、消息/信号/实例时限、错误二分
  （resolve 五动作）、变量读写面（update_variables/wf_variable_history）、
  表达式数据面（now/prev_task/node() 判断面词汇 + wf_route_decision 路由审计）。
  范围权威 `docs/product/kernel-v2/spec.md`。
- **产品 v1（进行中）**：独立部署流程引擎——REST+MCP 双开放、目录与权限两支柱、
  三面同构（Console/REST+MCP/Skill+CLI）、business_object 数据契约、任务页+CLI+docker。
  范围权威 `docs/product/engine-v1/`。

## 扩展一种节点类型（三步，不改引擎核心）

```python
from flowork.api import FlowNode, NodeBehavior

class MySpec(FlowNode):
    kind: str = "my_node"
    my_field: str = ""

class MyBehavior(NodeBehavior):
    def on_enter(self, ctx):
        ctx.variables["my_result"] = "done"

node_registry.register("my_node", MySpec, MyBehavior)
```

## 流程设计器(Web UI)

`designer/` 提供 React Flow 可视化设计器(草稿 → 校验 → 发布生命周期、schema 驱动属性表单、
registry 驱动调色板、Spec JSON 直编辑),后端为 `flowork/contrib/web/`（官方插件位）的薄 FastAPI:

```bash
FLOWORK_DB=/tmp/flowork-designer.db uv run uvicorn flowork.contrib.web.app:app --port 8901  # 引擎 API
cd designer && npm install && npm run dev                                            # http://localhost:5173
```

详见 `designer/README.md`。

## 更多

- **内核手册**：`docs/manual/`（使用者视角，从五分钟跑通到部署与 FAQ，12 章）
- 构建约定与包结构：`AGENTS.md`（改代码必读）
- Python API 参考：`docs/API.md`；集成部署（表结构/超时调度/并发模型）：`docs/integration.md`
- 文档索引：`llms.txt`
