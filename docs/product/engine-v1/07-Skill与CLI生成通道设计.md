# flowork 引擎 v1 · Skill 与 CLI 生成通道设计（配置即代码）

> 要求②的落点："可视化配置之外，支持 coding agent 直接通过 skill 生成"。设计依据两条已有结论：①2026-10 中间件调研（记忆 ai-appgen-middleware-direction）——**Skill 承载程序性知识、validator 闭环是复杂配置的杠杆、golden examples + anti-patterns 优于 props 文档**；②05 §1 三面同构——生成物必须走同一校验发布管线，绝不给 agent 开旁门。

## 1. 配置即代码（Assets as Code）

**什么能成为文件，什么不能**：

| 资产 | 文件化 | 理由 |
|---|---|---|
| 流程定义 | ✅ YAML（人写）/ JSON（机器写） | 本就版本不可变，天然 IaC |
| 目录种子 | ✅ YAML（v1 建库引导；日常运维走 Console，CSV 导入并存） | 环境重建/GitOps 初始化需要 |
| webhook/通知渠道 | ✅ YAML | 同上 |
| 凭证 | ❌ 永不文件化 | 密钥不进 git |
| 运行态（实例/任务） | ❌ | 是数据不是配置 |

**项目结构**（`flowork init` 生成）：

```
my-approval/
├── flowork.yaml            # 项目清单：目标服务地址/命名空间
├── business_objects/       # 业务对象 schema（数据契约，见 10-数据能力决策）
│   └── expense.yaml        # 报销单：字段/类型/枚举/约束（流程引用 bo@version）
├── flows/
│   ├── expense.yaml        # 报销流程定义（引用 business_objects/expense）
│   └── device-purchase.yaml
├── directory.yaml          # 部门树/用户/角色授权种子（可选）
├── webhooks.yaml
└── .flowork/
    └── state.yaml          # apply 的远端版本指纹（本地状态，gitignore 争议→提交，见 §4）
```

YAML 与既有 JSON 定义一一对应（`assets` 模块做双向转换）；目录 YAML 是**种子**语义：apply 采用 upsert-by-key，Console 后续手工改动不被覆盖（字段级以远端为准的策略记录在 apply 报告里）。

## 2. CLI 扩展（typer，既有 5 命令之上）

```
flowork init [dir]                    生成项目骨架 + 示例流程
flowork validate [paths]              本地校验（结构/语义/参与者可达性），零网络可用
flowork dry-run <flow> --vars x.json  路由预演（连服务或本地均可）
flowork diff [--server URL]           本地文件 vs 线上版本的结构化 diff
flowork apply [--yes]                 validate→diff→（确认）→publish，幂等（内容哈希相同则跳过）
flowork audit verify [--window]       审计 hash chain 校验（08 §3）
flowork tick / demo-full ...          # 既有命令不动
```

`apply` 是 skill 通道的可执行锚点：**agent 只需要学会"写文件 + 跑 apply"**，不需要理解 REST 细节——这同时是 MCP 之外的第二个 agent 面（MCP 服务运行时，CLI 服务交付时）。

## 3. SKILL.md 设计（官方分发）

位置与分发：仓库 `.flowork/skills/flowork-author/`（版本随引擎走）+ 发布到 skill 市场（悟空兼容开源 Skill 体系）+ llms.txt 索引（antd 的 both 模式：skill+文档双载体）。

**内容骨架**（渐进披露三段式）：

```
SKILL.md
├─ 何时用本 skill（触发场景：建/改审批流、定义业务对象、初始化目录）
├─ 快速路径（10 行内）：init → 写 business_objects/*.yaml（如无）→ 写 flows/*.yaml
│                              → validate → apply
├─ 参考（折叠）：YAML schema 要点·节点类型·参与者函数表·条件表达式
│                ·业务对象类型系统（10 §4：amount 带量纲/ref 只存引用+展示快照/json 是逃生舱慎用）
├─ Golden Examples ×3：报销（业务对象+条件分支+会签）/ 请假（主管升级）/ 采购（多级+驳回回起点）
└─ Anti-patterns（高频错误，来自真实校验日志沉淀）：
   · 条件分支无默认兜底边 → validate 报错但 agent 常忽略：写明"每个分支节点必须有一条无条件或 default 出边"
   · 参与者函数拼写（manager_of vs manager）与参数语义
   · 会签出口 all/any/>=N 配置在节点而非边上
   · 变量未在业务对象声明即引用（路由条件必须落在已声明字段上，金额比较需用 amount 类型）
   · 把客户/供应商档案复制进业务对象（主数据只允许 ref 引用，不建字段抄录）
```

**强制验证闭环**（写进 skill 的 completion criteria，不是建议）：生成后必须 `flowork validate` 零错误 + `dry-run` 至少一组样例变量通过，才算任务完成——这是 Lovable 式"违规检测自动重试"范式的最小实现，也呼应仓库 AGENTS.md 的 AI 友好设计。

**skill 与三面的关系**：skill 产出的文件走 `apply` = 走 05 §1 配置管线（鉴权用 CLI 登录的 user token 或独立 CI 凭证）。因此**skill 生成的发布与 Console 手工发布产出同一版本体系**，审计里可区分来源（ acted_via=cli|console|api ）。

## 4. 状态与幂等

- `apply` 幂等键 = 文件内容哈希 + 目标命名空间；与线上已发布版本内容一致则跳过并报"already up-to-date"。
- `.flowork/state.yaml` 记录各文件对应的远端版本号，**提交进 git**：使 CI（GitHub Actions 里 `flowork apply --yes`）与本地开发不漂移——GitOps 的最小实现；冲突（远端被 Console 先改）由 `diff` 显式暴露，不静默覆盖。

## 5. 验收（DoD）

1. `flowork init && flowork apply` 十分钟内从零建好"报销（含业务对象）+目录"并在任务页跑通一笔审批（quickstart 文档据此录制）。
2. 一句自然语言让 Claude（装 skill）生成新流程 → validate 通过 → apply 发布 → dry-run 预演正确——**skill 通道的标志性演示**。
3. skill 反例测试：故意生成无兜底分支的流程，agent 依 skill 的 anti-patterns 自纠或 validate 拦截；**schema 反例**：路由条件引用未声明字段/主数据被复制成普通字段，validate 必须拦截。
4. Console 改动 → `flowork diff` 可见；CLI 发布 → Console 版本页可见——三面同构的双向验证。

## 6. 边界与后续

v1 skill 只覆盖"流程定义+目录种子+webhook"；v2 扩授权层策略（policy YAML 同管线）。agent 直接经 MCP 修改定义**刻意不做**（05 §1 推论 1 的例外：定义变更永远需要人侧确认——CLI apply 的确认交互或 CI 评审承担此角色）。
