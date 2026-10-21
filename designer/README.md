# flowork Designer(流程设计器)

React + TypeScript + React Flow 实现的 flowork 流程界面。定位(2026-10-16 拍板)＝**流程的信任面**:
① **可视化检视**——AI 能审,人看一眼仍不可替代;② **调整编辑**——不是主战场,保留全套能力;
③ **模拟测试**——发布前拿证据(路由预演/整图模拟,补全计划见 `docs/product/designer-simulation-plan.md`)。
画布/表单仍是 **Spec(模板 JSON)的编辑视图**,与 CLI / API / AI 写路径等价。

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
# 1. 后端 API(仓库根目录;不连任何用户数据库——见下)
FLOWORK_DEFINITIONS_DIR=./examples uv run uvicorn flowork.contrib.web.app:app --port 8901

# 2. 前端(本目录)
npm install
npm run dev        # http://localhost:5173,/api 代理到 8901
```

### 自定义扩展(取人函数/handler/节点类型)带进设计器进程

设计器是**独立进程**。宿主的扩展全住在宿主 Python 代码里——不带进来,用了自定义
取人函数的流程连模拟都跑不起来(求值报未声明函数)。原则:**扩展跟着引擎走,
不跟着面走**。

**宿主挂载(推荐,零配置)**——自己装配好引擎,一行挂上,函数/handler/自定义节点
全跟引擎来,不需要再配任何东西:

```python
from flowork.contrib.web.app import register_designer_routes
register_designer_routes(app, engine)   # 宿主 FastAPI + 宿主引擎
```

注意:模拟实例会写传入引擎的库(simulated 隔离);CORS/鉴权/生命周期归宿主。

**独立进程**——`FLOWORK_SETUP=模块` 一次配齐三钩子(全可选):

```python
# myapp/designer_setup.py
def nodes(registry): ...        # registry.register(kind, spec, ui=...) 自定义节点
def vocabulary(org): ...        # 返回 ExpressionFunctions(取人/谓词函数)
def handlers(engine): ...       # engine.register_handler(name, fn)
```

```bash
FLOWORK_SETUP=myapp.designer_setup FLOWORK_ORG=./examples/org.yaml \
  FLOWORK_DEFINITIONS_DIR=./examples uv run uvicorn flowork.contrib.web.app:app
```

词汇优先级:显式参数 > `setup.vocabulary` > `FLOWORK_VOCABULARY`(便捷子集,仅函数)
> 配了组织目录时 `contrib.cel_vocabulary`。REST 独立进程(`contrib/rest`)同走
同一装配——`FLOWORK_SETUP` 对 /v2 面同样生效。

**目录自动下发**:装配的表达式函数从签名推导目录条目(`/api/catalog`),连线条件
编辑器的点选列表自动出现宿主函数(带中文标签与签名)——编辑器与校验器看同一个
世界;位点按返回类型推导(取人族→取人位,谓词→条件/出口位,值类型→任意位)。

**当前进程认识哪些函数**在首页与 `/api/runtime` 的 `vocabulary_functions` 里直接列出来——
没装配时会明确说「用了 manager_of 等取人词汇的流程跑不起来」,不会让人对着
「取人公式求值失败」自己猜。设计器不会偷偷补一套标准词汇:生产没注册的函数在这里也不装,
否则会出现「设计器绿、线上挂」。

模拟时可指定**发起人**(`start_user`):`initiator` 是取人公式最常用的输入,
缺省 `simulator` 不在组织目录里,`manager_of(initiator)` 会解析空——那时指定真实用户才能验通。

### 设计器不碰用户数据库

设计器自己的引擎是一份**进程内临时库**(内存 SQLite),只装本次会话的模拟/场景执行痕迹,
进程退出即消失。`FLOWORK_DB`(宿主的运行时库)被**有意忽略**——设了会在启动时打一行提示。
需要留存会话痕迹时才用 `FLOWORK_DESIGNER_DB` 指向一个与生产库**物理分开**的专用临时库。

定义的事实源有两种:

| | 无 `FLOWORK_DEFINITIONS_DIR` | 有 `FLOWORK_DEFINITIONS_DIR` |
|---|---|---|
| 打开对话框列什么 | 本会话打开过的定义 | 目录里的 `*.json` |
| 保存写到哪 | 会话临时库(关掉即失) | **写回那个 JSON 文件**(git 立刻可见) |
| 发布按钮 | 有(写会话库,不影响生产) | **没有**——进生产是 CI/CD 的 `flowork deploy` |
| 适用 | 临时试画一份图 | 日常开发 |

文件形态的三条纪律:写入前过装载与设计期校验(带病不落盘);同一 `code` 只能有一份文件;
落盘内容剥掉 `major/revision/status/version`(版本号由 deploy 判定,写进文件只是 git diff 噪音)。

### 只有一个页面:设计器

没有独立的定义列表页——设计器常驻,未打开流程时画布区是空态,「打开流程」按钮弹出选择框
(列目录文件/会话内定义 + 新建 + 打开本地文件)。列表页在开发态只是把 IDE 已有的文件树再列一遍,
不回答任何判断性问题;打开动作收进设计器后,首屏就是干活的地方。

**打开本地文件**不落库也不进目录,适合 review 别人提的图纸;该会话保存即下载(没有其他落盘通道)。
三条打开路径按环境选:文件选择器(独立浏览器)、**整页拖入**(把 .json 拖到空态区)、
**粘贴 JSON 文本**(对话框内)。后两条是为嵌入型 webview 准备的——这类环境会屏蔽系统文件选择器,
点击选择文件既不开窗也无回调,用户只看到宿主报「无法加载」。

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
| GET  | `/api/runtime` | 运行形态(`db`/`files`)+ 会话库是否临时 + **已装配的表达式函数名**(自定义取人函数可见性) |
| GET  | `/api/node-kinds` | 节点类型元数据 + 各 Spec 的 JSON Schema(含 setup 注册的自定义节点) |
| GET  | `/api/files` | **文件清单**(仅 `--definitions-dir` 形态;含每份文件的装载结论,坏文件/重复 code 如实报错) |
| GET  | `/api/files/{file_name}` | 读取目录里的定义 JSON(装载失败 → 422,不进画布) |
| PUT  | `/api/files/{file_name}` | 保存到目录里的 JSON(校验不过不落盘;剥 `major/revision/status/version`;原子写) |
| GET  | `/api/definitions` | 定义汇总(草稿/已发布版本号) |
| GET  | `/api/definitions/{code}` | 定义详情(含最新草稿 Spec;`?source=published` 取已发布版——导出去 git 的正确来源) |
| PUT  | `/api/definitions/{code}` | 保存草稿(就地更新或新建草稿版本) |
| POST | `/api/definitions/{code}/publish` | 发布(校验失败 → 422 + issues;响应含版本判定全貌 triggered_rules/identical_to) |
| POST | `/api/definitions/{code}/verdict` | **版本判定预演**(只判定不落库:下一版号/破坏性规则/幂等去重;编辑期看后果) |
| POST | `/api/validate` | 设计时校验(不落库;`?deploy=true` 追加部署期校验预演) |
| POST | `/api/dry-run` | 路由预演(样例变量走一遍图;等待节点支持假设:消息到达/超时/两面都试) |
| POST | `/api/formula/preview` | CEL 公式试算(样例值) |
| GET  | `/api/catalog` | 表达式词汇目录(保留词/函数,含位点;条件编辑器点选数据源) |
| POST | `/api/definitions/{code}/diff` | 语义 diff(草稿 vs 最新发布) |
| POST | `/api/definitions/{code}/simulate` | **整图模拟**(body 可带 spec/variables/controls/`start_user`;`?major=&revision=` 模拟已发布版本;零副作用) |
| GET  | `/api/definitions/{code}/versions` | 已发布版本列表(新→旧) |
| GET  | `/api/definitions/{code}/versions/{major}` | 按版本取已发布 spec(`?revision=`;只读模式的数据前提) |
| GET  | `/api/handlers` | 已注册 handler 名录(服务任务动作下拉数据源) |
| GET  | `/api/directory/summary` | 组织目录只读摘要(`FLOWORK_ORG=org.yaml` 装配;未配置降级) |
| GET  | `/api/definitions/{code}/scenario` | 读场景文件（`<code>.scenario.json`，与定义同目录；没有则 404） |
| PUT  | `/api/definitions/{code}/scenario` | 写场景文件（校验脚本形状后原子落盘，进 git） |
| DELETE | `/api/definitions/{code}/scenario` | 删除场景文件 |
| POST | `/api/definitions/{code}/scenarios/run` | 直跑脚本（`{script, spec?}`，零副作用；CI/临时验证口） |

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
  画布不可编辑、调色板/属性面板/保存/发布隐藏,保留试运行与场景) /
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
│   ├── App.tsx              # 设计器常驻壳(未打开流程时画布区为空态)
│   ├── main.tsx / index.css # 入口与样式
│   └── components/
│       ├── EmptyState.tsx          # 空态:打开/拖入入口
│       ├── OpenDialog.tsx          # 打开流程:目录/库里的定义 + 新建 + 本地文件(拖入/粘贴)
│       ├── Canvas.tsx              # 画布(受控渲染)
│       ├── FlowNodes.tsx           # 自定义节点(矩形/圆/菱形)+ 连线(点击连线/条件标签)
│       ├── Palette.tsx             # registry 驱动调色板
│       ├── RightPanel.tsx          # 属性 / Spec JSON / 校验 三 Tab
│       ├── SchemaForm.tsx          # JSON Schema 递归表单
│       ├── Topbar.tsx              # 生命周期状态 + 发布入口
│       ├── PublishDialog.tsx       # 发布确认(错误拦截/警告提示)
│       ├── DryRunDialog.tsx        # 试运行:路由预演 + 完整模拟(控制面四键)
│       ├── ScenarioDialog.tsx      # 场景回归:只读运行(<code>.scenario.json)
│       ├── ConnectMenu.tsx         # 拖线到空白处的快捷新建菜单
│       └── DevErrorBoundary.tsx    # 开发期错误边界```
