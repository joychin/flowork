# flowork Designer(流程设计器)

React + TypeScript + React Flow 实现的 flowork 流程设计器 UI。
对应《Python流程引擎实施规划》的「后续 UI 独立项目」落地:UI 只是 **Spec(模板 JSON)的编辑视图**,与 CLI / API / AI 写路径等价。

## 架构原则

```
┌──────────────┐  PUT/POST(JSON)   ┌───────────────────┐   四服务    ┌────────────┐
│  designer/   │ ────────────────→ │ flowork/contrib/web │ ─────────→ │  引擎内核   │
│  React Flow  │ ←──────────────── │  (薄 FastAPI 传输层)│            │ repository  │
│  纯视图+编辑  │  JSON(Spec/Issues)│  无业务逻辑         │            │ validate…   │
└──────────────┘                   └───────────────────┘            └────────────┘
```

- **Spec 唯一事实源**:画布、属性表单、Spec JSON Tab 全部读写同一份定义;布局坐标与
  语义内容分离存储(草稿表独立 layout 列,发布内容仍携带画布坐标)
- **schema 驱动表单**:属性面板由各 `*Spec` 的 pydantic JSON Schema(`/api/node-kinds`)递归生成;引擎新增字段/节点类型,前端零代码跟进。取人与动作字段
  挂 datalist 建议(handler 名录/目录用户与组引用,`/api/handlers` + `/api/directory/summary`)。
- **registry 驱动调色板**:节点类型元数据(name/icon/category/palette)来自 `NodeRegistry.register(..., ui={...})`。
- **校验闭环**:编辑后防抖调用 `POST /api/validate`(引擎侧 `repository.validate`),错误定位到节点/连线,ERROR 级问题阻塞发布。
- **生命周期**:草稿(可变)→ 发布(不可变,`assert_valid` 强制校验);运行中实例绑版本,编辑已发布定义自动产生新草稿版本。

## 启动(开发模式)

```bash
# 1. 后端 API(仓库根目录;FLOWORK_DB 指定 SQLite 文件,空库自动灌入 examples/)
FLOWORK_DB=/tmp/flowork-designer.db uv run uvicorn flowork.contrib.web.app:app --port 8901

# 2. 前端(本目录)
npm install
npm run dev        # http://localhost:5173,/api 代理到 8901
```

## 画布交互

- **加节点**:把左侧调色板项**拖进画布**(以光标为中心落下,画布出现可放置光晕),或直接点击(放在图最右侧)
- **插节点到线中间**:把左侧调色板的节点**拖到连线上**(视觉上落在线上即可,悬停时该线高亮),松手拆线重连(原线一分为二,条件/默认标记随前段走)。仅面板拖入支持 —— 画布节点拖动是纯移动,不会改写拓扑
- **拖拽聚焦**:拖动节点时其余元素自动压暗,只突出被拖节点与它的连线,落点一目了然(节点拖动不触发画布自动平移,需平移用滚轮/空格拖)
- **连线**(两种等价方式):从节点右侧**出线口拖到**目标节点(**拖到节点身上即可**,无需瞄准端口;连接线为品牌色流动虚线);或先点出线口、再点目标节点;**Esc 取消**拉线。**拖线中所有节点显示虚线环**提示可放置
- **快捷新建**:从出线口**拖线到空白处松手**,弹出节点类型菜单,点选即在该处建节点并自动连线(n8n/Dify 模式);Esc 或点空白取消
- **连线容错**:非法连接实时禁止(end 不可出线 / start 不可入线 / 自环),重复连线给出提示而非静默
- **边改道**:点选连线后**直接抓线身拖到目标节点松手**即改接箭头端(要改起点用起点侧拖点;重复/非法目标被拦截,Esc 取消,落在空白处会提示取消)。两端也有「起点/终点」大拖点,多条线连入/连出同一节点时自动上下错开;拖动悬停到候选节点会**绿色描边**,一眼看清落点
- **调整位置**:直接拖动节点(拖拽期间自动校验不会使节点回弹);**接近其他节点的中心/边缘时自动磁吸对齐并显示红色参考线**(Figma 模式);一次拖动 = 一条撤销记录,拖乱 Ctrl+Z 即可找回;**长链点「⌗ 整理布局」自动蛇形折行成二维网格**(一维生长的解药);拖节点/连线甩出画布会自动折返,不会丢失操作
- **保存**:手动保存(顶栏「● 保存」按钮 / Ctrl+S / Cmd+S);有未保存变更时按钮点亮,关页前会拦截提示
- **编辑**:点节点/连线在右侧面板编辑(属性由 Spec 的 JSON Schema 生成);点连线选中后出现 ✕ 删除按钮;**右键节点**=拉线/复制/编辑/删除,**右键连线**=编辑/默认边/删除
- **画布徽章**:等待节点显示等什么/等多久(✉ 消息名 · ⏱ 分钟 · ⚡ 竞速),子流程显示被调定义,服务任务显示动作——不必点开就能读图
- **撤销/重做**:顶栏按钮或 Ctrl+Z / Ctrl+Shift+Z;**删除**:选中后 Delete
- 校验编辑后自动运行;「校验」页可点击问题条目定位元素;ERROR 级问题阻塞发布

## API 一览(均与 RepositoryService 一一映射)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/health` | 健康检查 |
| GET  | `/api/node-kinds` | 节点类型元数据 + 各 Spec 的 JSON Schema |
| GET  | `/api/definitions` | 定义汇总(草稿/已发布版本号) |
| GET  | `/api/definitions/{code}` | 定义详情(含最新草稿 Spec) |
| PUT  | `/api/definitions/{code}` | 保存草稿(就地更新或新建草稿版本) |
| POST | `/api/definitions/{code}/publish` | 发布(校验失败 → 422 + issues;响应含版本判定全貌 triggered_rules/identical_to) |
| POST | `/api/validate` | 设计时校验(不落库;`?deploy=true` 追加部署期校验预演) |
| POST | `/api/dry-run` | 路由预演(样例变量走一遍图;等待节点支持假设:消息到达/超时/两面都试) |
| POST | `/api/formula/preview` | CEL 公式试算(样例值) |
| GET  | `/api/catalog` | 表达式词汇目录(保留词/函数,含位点;条件编辑器点选数据源) |
| POST | `/api/definitions/{code}/diff` | 语义 diff(草稿 vs 最新发布) |
| POST | `/api/definitions/{code}/simulate` | **整图模拟**(草稿主场景:body 可带 spec,缺省取已保存草稿;模拟前强制校验;`?major=&revision=` 模拟已发布版本;模拟实例隔离于实例列表) |
| GET  | `/api/instances` | 实例列表(`?state=&definition_code=&include_simulated=`;默认隔离模拟实例) |
| GET  | `/api/instances/{id}` | 实例详情(实例 + 令牌状态 + 当前变量) |
| GET  | `/api/instances/{id}/timeline` | 流转轨迹(令牌+已办+事件按时间合并) |
| GET  | `/api/instances/{id}/route-decisions` | 路由决策审计(逐边公式结果 + 求值快照) |
| GET  | `/api/instances/{id}/variable-history` | 变量血缘(`?name=&limit=&offset=`) |
| GET  | `/api/instances/{id}/tasks` | 实例已办任务(`?offset=&limit=` 分页) |
| GET  | `/api/definitions/{code}/versions` | 已发布版本列表(新→旧) |
| GET  | `/api/definitions/{code}/versions/{major}` | 按版本取已发布 spec(`?revision=`;只读模式的数据前提) |
| GET  | `/api/handlers` | 已注册 handler 名录(服务任务动作下拉数据源) |
| GET  | `/api/directory/summary` | 组织目录只读摘要(`FLOWORK_ORG=org.yaml` 装配;未配置降级) |

## 插件化(`@flowork/designer`)

设计器可构建为可嵌入包(`npm run build:lib` → `dist-lib/`):ESM 单文件 + CSS,
宿主三行代码嵌入只读审计视图——

```html
<link rel="stylesheet" href="/designer-lib/flowork-designer.css">
<div id="audit" style="height:100vh"></div>
<script type="module">
  import { mountFloworkDesigner } from '/designer-lib/flowork-designer.es.js'
  mountFloworkDesigner('#audit', { apiBase: '', mode: 'readonly', definitionCode: 'leave' })
</script>
```

- 配置面:`apiBase`(API 前缀,同域留空) / `mode: 'edit' | 'readonly'`(缺省 readonly:
  画布不可编辑、调色板/属性面板/保存/发布隐藏,保留试运行与实例审计) /
  `definitionCode`(深链直开)。
- 单进程 demo:dist-lib 存在时引擎自动托管,`/designer-demo?code=xxx` 即嵌入示例页。
- 契约纪律:插件消费的 `/api` 面由 `tests/test_web_api_surface.py` 冻结(增删=显式动作)。

## 目录

```
designer/
├── src/
│   ├── api.ts               # fetch 封装
│   ├── store.ts             # zustand:Spec 状态 + 历史(undo/redo)+ 手动保存(dirty 标记)/校验防抖
│   ├── adapter.ts           # Spec → React Flow 元素派生(带内容签名缓存,未变元素不重渲染) + 条件摘要
│   ├── edgeInsert.ts        # 「节点插入连线中间」几何判定(smoothstep 路径采样找最近线)
│   ├── types.ts             # 与 pydantic 模型同构的类型
│   ├── App.tsx              # 两态壳:定义列表 ⇄ 设计器
│   ├── main.tsx / index.css # 入口与样式
│   └── components/
│       ├── DefinitionList.tsx      # 定义列表 / 新建
│       ├── Canvas.tsx              # 画布(受控渲染)
│       ├── FlowNodes.tsx           # 自定义节点(矩形/圆/菱形)+ 连线(点击连线/条件标签)
│       ├── Palette.tsx             # registry 驱动调色板
│       ├── RightPanel.tsx          # 属性 / Spec JSON / 校验 三 Tab
│       ├── SchemaForm.tsx          # JSON Schema 递归表单
│       ├── Topbar.tsx              # 生命周期状态 + 发布入口
│       ├── PublishDialog.tsx       # 发布确认(错误拦截/警告提示)
│       ├── DryRunDialog.tsx        # 路由预演(样例变量编辑 + 结果)
│       ├── ConnectMenu.tsx         # 拖线到空白处的快捷新建菜单
│       └── DevErrorBoundary.tsx    # 开发期错误边界```
