# flowork Designer(流程设计器)

React + TypeScript + React Flow 实现的 flowork 流程设计器 UI。
对应《Python流程引擎实施规划》的「后续 UI 独立项目」落地:UI 只是 **Spec(模板 JSON)的编辑视图**,与 CLI / API / AI 写路径等价。

## 架构原则

```
┌──────────────┐  PUT/POST(JSON)   ┌───────────────────┐   四服务    ┌────────────┐
│  designer/   │ ────────────────→ │  flowork/web       │ ─────────→ │  引擎内核   │
│  React Flow  │ ←──────────────── │  (薄 FastAPI 传输层)│            │ repository  │
│  纯视图+编辑  │  JSON(Spec/Issues)│  无业务逻辑         │            │ validate…   │
└──────────────┘                   └───────────────────┘            └────────────┘
```

- **Spec 唯一事实源**:画布、属性表单、Spec JSON Tab 全部读写同一份定义;`layout` 仅为展示坐标,semantic diff 应忽略。
- **schema 驱动表单**:属性面板由各 `*Spec` 的 pydantic JSON Schema(`/api/node-kinds`)递归生成;引擎新增字段/节点类型,前端零代码跟进。
- **registry 驱动调色板**:节点类型元数据(name/icon/category/palette)来自 `NodeRegistry.register(..., ui={...})`。
- **校验闭环**:编辑后防抖调用 `POST /api/validate`(引擎侧 `repository.validate`),错误定位到节点/连线,ERROR 级问题阻塞发布。
- **生命周期**:草稿(可变)→ 发布(不可变,`assert_valid` 强制校验);运行中实例绑版本,编辑已发布定义自动产生新草稿版本。

## 启动(开发模式)

```bash
# 1. 后端 API(仓库根目录;FLOWORK_DB 指定 SQLite 文件,空库自动灌入 examples/)
FLOWORK_DB=/tmp/flowork-designer.db uv run uvicorn flowork.web.app:app --port 8901

# 2. 前端(本目录)
npm install
npm run dev        # http://localhost:5173,/api 代理到 8901
```

## 画布交互

- **加节点**:把左侧调色板项**拖进画布**(以光标为中心落下,画布出现可放置光晕),或直接点击(放在图最右侧)
- **连线**(两种等价方式):从节点右侧**出线口拖到**目标节点(**拖到节点身上即可**,无需瞄准端口;连接线为品牌色流动虚线);或先点出线口、再点目标节点;**Esc 取消**拉线
- **连线容错**:非法连接实时禁止(end 不可出线 / start 不可入线 / 自环),重复连线给出提示而非静默
- **边改道**:悬停/选中连线后两端出现改道手柄,**拖动端点到新节点身上**即改接(替换而非新增;重复/非法目标被拦截)
- **调整位置**:直接拖动节点(拖拽期间自动校验/保存不会使节点回弹),松手自动保存
- **编辑**:点节点/连线在右侧面板编辑(属性由 Spec 的 JSON Schema 生成);点连线选中后出现 ✕ 删除按钮
- **撤销/重做**:顶栏按钮或 Ctrl+Z / Ctrl+Shift+Z;**删除**:选中后 Delete
- 校验编辑后自动运行;「校验」页可点击问题条目定位元素;ERROR 级问题阻塞发布

## API 一览(均与 RepositoryService 一一映射)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/node-kinds` | 节点类型元数据 + 各 Spec 的 JSON Schema |
| GET  | `/api/definitions` | 定义汇总(草稿/已发布版本号) |
| GET  | `/api/definitions/{code}` | 定义详情(含最新草稿 Spec) |
| PUT  | `/api/definitions/{code}` | 保存草稿(就地更新或新建草稿版本) |
| POST | `/api/definitions/{code}/publish` | 发布(校验失败 → 422 + issues) |
| POST | `/api/validate` | 设计时校验(不落库) |

## 目录

```
designer/
├── src/
│   ├── api.ts               # fetch 封装
│   ├── store.ts             # zustand:Spec 状态 + 历史(undo/redo)+ 自动保存/校验防抖
│   ├── adapter.ts           # Spec → React Flow 元素派生 + 条件摘要
│   ├── types.ts             # 与 pydantic 模型同构的类型
│   └── components/
│       ├── DefinitionList.tsx   # 定义列表 / 新建
│       ├── Canvas.tsx           # 画布(受控渲染)
│       ├── FlowNodes.tsx        # 自定义节点(矩形/圆/菱形)+ 连线(点击连线/条件标签)
│       ├── Palette.tsx          # registry 驱动调色板
│       ├── RightPanel.tsx       # 属性 / Spec JSON / 校验 三 Tab
│       ├── SchemaForm.tsx       # JSON Schema 递归表单
│       ├── Topbar.tsx           # 生命周期状态 + 发布入口
│       └── PublishDialog.tsx    # 发布确认(错误拦截/警告提示)
```
