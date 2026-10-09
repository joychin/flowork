/** 画布:React Flow 受控渲染;调色板拖放入画布(落到连线中间 = 直接插入)、节点拖拽调位、拖拽/点击两种连线 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeMouseHandler,
  type EdgeTypes,
  type NodeChange,
  type Node as RFNode,
  type NodeMouseHandler,
  type OnConnectEnd,
  type OnConnectStart,
  type OnNodeDrag,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  hitFromSteps,
  newElementsCache,
  nodeSize,
  toFlowElements,
} from "../adapter";
import { findEdgeNear } from "../edgeInsert";
import { useStore } from "../store";
import { ConnectMenu } from "./ConnectMenu";
import { ContextMenu } from "./ContextMenu";
import { edgeTypes, nodeTypes, Icon } from "./FlowNodes";

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}

function CanvasInner() {
  const spec = useStore((s) => s.spec);
  const metaByKind = useStore((s) => s.metaByKind);
  const issues = useStore((s) => s.issues);
  const selection = useStore((s) => s.selection);
  const pendingConnect = useStore((s) => s.pendingConnect);
  const dryRun = useStore((s) => s.dryRun);
  const select = useStore((s) => s.select);
  const setNodePos = useStore((s) => s.setNodePos);
  const connect = useStore((s) => s.connect);
  const setPendingConnect = useStore((s) => s.setPendingConnect);
  const addNode = useStore((s) => s.addNode);
  const insertNodeOnFlow = useStore((s) => s.insertNodeOnFlow);
  const openConnectMenu = useStore((s) => s.openConnectMenu);
  const rf = useReactFlow();
  const [hintOpen, setHintOpen] = useState(true);
  const [dropReady, setDropReady] = useState(false);
  const [splitTarget, setSplitTarget] = useState<string | null>(null);

  // 拖拽中的实时位置已由「渲染层 nodes + applyNodeChanges」承接(受控模式标准管线),
  // 此 ref 机制整体移除 —— RF 内部不再需要外部防回弹补丁
  // 插线检测节流
  const splitCheckAt = useRef(0);

  // 元素对象缓存:未变化的节点/连线复用旧引用,避免全量重渲染
  const cacheRef = useRef(newElementsCache());
  // 数组级引用复用:所有元素对象都命中缓存时,整个数组也复用旧引用,
  // 使"节点内容未变"的重算(校验响应/其他 store 字段变化)不触发 RF 受控重置
  const arrCacheRef = useRef<{ nodes: RFNode[]; edges: Edge[] } | null>(null);

  const elements = useMemo(() => {
    if (!spec) return { nodes: [] as RFNode[], edges: [] as Edge[] };
    const hit = dryRun ? hitFromSteps(dryRun.steps) : null;
    const derived = toFlowElements(
      spec,
      metaByKind,
      issues,
      selection,
      pendingConnect,
      hit,
      cacheRef.current,
      splitTarget,
    );
    const prev = arrCacheRef.current;
    const nodesStable =
      !!prev &&
      prev.nodes.length === derived.nodes.length &&
      derived.nodes.every((n, i) => n === prev.nodes[i]);
    const edgesStable =
      !!prev &&
      prev.edges.length === derived.edges.length &&
      derived.edges.every((e, i) => e === prev.edges[i]);
    const out = {
      nodes: nodesStable && prev ? prev.nodes : derived.nodes,
      edges: edgesStable && prev ? prev.edges : derived.edges,
    };
    if (nodesStable && edgesStable) return out;
    arrCacheRef.current = out;
    return out;
  }, [
    spec,
    metaByKind,
    issues,
    selection,
    pendingConnect,
    dryRun,
    splitTarget,
  ]);

  // ── 受控模式标准管线(RF v12):渲染层 nodes 状态承接拖动/选中/测量变更。
  //  受控下 RF 只把 position changes 派发给 onNodesChange、内部不应用 ——
  //  必须由 applyNodeChanges 应用回 nodes,拖动中的节点才会跟手渲染。
  //  拖动进行中(存在 dragging 节点)不用 spec 派生值覆盖渲染层,防止校验响应等
  //  异步更新把拖动中的位置打回旧值;松手提交后派生值自然接管。 ──
  const [renderNodes, setRenderNodes] = useState<RFNode[]>([]);
  useEffect(() => {
    setRenderNodes((prev) =>
      prev.some((n) => n.dragging) ? prev : elements.nodes,
    );
  }, [elements.nodes]);
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setRenderNodes((nds) => applyNodeChanges(changes, nds));
  }, []);

  // ── 拖动对齐辅助线(Figma/bpmn 模式):接近其他节点的中心/边缘时磁吸并显示参考线。
  //  实现要点:磁吸在 onNodesChange 里直接改 position change(拖拽管线零 React 状态),
  //  参考线用直接 DOM 画(_guidesRef),均不触发重渲染。 ──
  const ALIGN_THRESHOLD = 6;
  const renderNodesRef = useRef<RFNode[]>([]);
  renderNodesRef.current = renderNodes;
  const guidesRef = useRef<HTMLDivElement | null>(null);

  const drawGuides = useCallback(
    (lines: { dir: "v" | "h"; at: number }[]) => {
      const el = guidesRef.current;
      if (!el || !rf) return;
      el.textContent = "";
      if (!lines.length) return;
      const box = el.getBoundingClientRect();
      for (const l of lines) {
        const div = document.createElement("div");
        div.className = `fk-align-guide ${l.dir}`;
        const p =
          l.dir === "v"
            ? rf.flowToScreenPosition({ x: l.at, y: 0 })
            : rf.flowToScreenPosition({ x: 0, y: l.at });
        if (l.dir === "v") div.style.left = `${Math.round(p.x - box.x)}px`;
        else div.style.top = `${Math.round(p.y - box.y)}px`;
        el.appendChild(div);
      }
    },
    [rf],
  );

  const onNodesChangeWithAlign = useCallback(
    (changes: NodeChange[]) => {
      const dragChanges = changes.filter(
        (c): c is Extract<NodeChange, { type: "position" }> =>
          c.type === "position" && c.dragging === true && !!c.position,
      );
      if (dragChanges.length === 1) {
        const change = dragChanges[0];
        const pos = change.position;
        if (pos) {
          const nodes = renderNodesRef.current;
          const me = nodes.find((n) => n.id === change.id);
          if (me) {
            const mw = me.width ?? 164;
            const mh = me.height ?? 56;
            const my = {
              cx: pos.x + mw / 2,
              l: pos.x,
              r: pos.x + mw,
              cy: pos.y + mh / 2,
              t: pos.y,
              b: pos.y + mh,
            };
            let bx: { diff: number; at: number } | null = null;
            let by: { diff: number; at: number } | null = null;
            for (const o of nodes) {
              if (o.id === change.id || o.dragging) continue;
              const ow = o.width ?? 164;
              const oh = o.height ?? 56;
              const ot = {
                cx: o.position.x + ow / 2,
                l: o.position.x,
                r: o.position.x + ow,
                cy: o.position.y + oh / 2,
                t: o.position.y,
                b: o.position.y + oh,
              };
              const xs: [number, number][] = [
                [my.cx, ot.cx],
                [my.l, ot.l],
                [my.l, ot.r],
                [my.r, ot.l],
                [my.r, ot.r],
              ];
              const ys: [number, number][] = [
                [my.cy, ot.cy],
                [my.t, ot.t],
                [my.t, ot.b],
                [my.b, ot.t],
                [my.b, ot.b],
              ];
              for (const [mv, ov] of xs) {
                const diff = ov - mv;
                if (
                  Math.abs(diff) <= ALIGN_THRESHOLD &&
                  (!bx || Math.abs(diff) < Math.abs(bx.diff))
                )
                  bx = { diff, at: ov };
              }
              for (const [mv, ov] of ys) {
                const diff = ov - mv;
                if (
                  Math.abs(diff) <= ALIGN_THRESHOLD &&
                  (!by || Math.abs(diff) < Math.abs(by.diff))
                )
                  by = { diff, at: ov };
              }
            }
            if (bx) pos.x += bx.diff;
            if (by) pos.y += by.diff;
            drawGuides([
              ...(bx ? [{ dir: "v" as const, at: bx.at }] : []),
              ...(by ? [{ dir: "h" as const, at: by.at }] : []),
            ]);
          }
        }
      } else if (dragChanges.length === 0) {
        drawGuides([]);
      }
      onNodesChange(changes);
    },
    [onNodesChange, drawGuides],
  );

  // 拖动提交:onNodeDragStop 把最终位置写回 spec(历史/保存/校验跟上)。
  // 拖动中的跟手渲染由上面受控管线(onNodesChange→applyNodeChanges)负责。
  const onNodeDragStop = useCallback<OnNodeDrag>(
    (_event, node) => {
      drawGuides([]);
      setNodePos(
        node.id,
        Math.round(node.position.x),
        Math.round(node.position.y),
      );
    },
    [setNodePos, drawGuides],
  );

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      // pendingConnect 时的连接由节点组件自身 onClick 处理(此处若再连会双触发 → 误弹「连线已存在」)
      if (!pendingConnect) select({ type: "node", id: node.id });
    },
    [pendingConnect, select],
  );

  const onEdgeClick = useCallback<EdgeMouseHandler>(
    (_event, edge) => select({ type: "edge", id: edge.id }),
    [select],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        connect(connection.source, connection.target);
        setPendingConnect(null); // 拖拽连线成功后,清理目标 handle 上误触发的 pending 状态
      }
    },
    [connect, setPendingConnect],
  );

  // 拖线过程中的实时校验:非法目标不吸附、不落线(end 无出线 / start 无入线 / 自环)
  const isValidConnection = useCallback(
    (conn: Connection | Edge) => {
      if (!spec) return false;
      const { source, target } = conn;
      if (!source || !target || source === target) return false;
      const sourceNode = spec.nodes.find((n) => n.code === source);
      const targetNode = spec.nodes.find((n) => n.code === target);
      if (!sourceNode || !targetNode) return false;
      if (sourceNode.kind === "end" || targetNode.kind === "start")
        return false;
      return true;
    },
    [spec],
  );

  // 兜底:连线松手时若落在「节点身上」(而非端口附近),视为连接该节点 —— 符合直觉,无需瞄准小圆点。
  // 落在空白处且确有拖动 → 弹出快捷新建菜单(n8n/Dify 模式);原地点选即建节点并连好线
  const connStartPos = useRef<{ x: number; y: number } | null>(null);

  const onConnectStart = useCallback<OnConnectStart>((event) => {
    const me = event as MouseEvent | TouchEvent;
    const pt = "clientX" in me ? me : (me as TouchEvent).touches[0];
    connStartPos.current = pt ? { x: pt.clientX, y: pt.clientY } : null;
  }, []);

  const nodeUnderCursor = (x: number, y: number): string | null => {
    const el = document.elementFromPoint(x, y);
    return el?.closest(".react-flow__node")?.getAttribute("data-id") ?? null;
  };

  const onConnectEnd = useCallback<OnConnectEnd>(
    (event, state) => {
      const start = connStartPos.current;
      connStartPos.current = null;
      if (!state?.fromNode) return;
      if (state.isValid === true) return;
      const me = event as MouseEvent;
      // 落点折返(压测观察②):拖出画布的松手点(如向右扩展甩进右栏)钳回画布内 —— 出界不再是死路
      const cr = wrapRef.current?.getBoundingClientRect();
      let cx = me.clientX;
      let cy = me.clientY;
      if (cr) {
        cx = Math.min(Math.max(cx, cr.left + 70), cr.right - 70);
        cy = Math.min(Math.max(cy, cr.top + 50), cr.bottom - 50);
      }
      const targetId = nodeUnderCursor(cx, cy);
      if (targetId && targetId !== state.fromNode.id) {
        const source = state.fromNode.id;
        if (
          isValidConnection({
            source,
            target: targetId,
            sourceHandle: null,
            targetHandle: null,
          })
        )
          connect(source, targetId);
        return;
      }
      // 落空白:区分「点击端口」(未拖动 → 忽略)与「拖线到空白」(→ 快捷新建菜单)
      const moved = start
        ? Math.hypot(me.clientX - start.x, me.clientY - start.y)
        : 0;
      if (moved > 6 && rf) {
        const fp = rf.screenToFlowPosition({ x: cx, y: cy });
        const menuCr = { bx: cr ? cr.left : 0, bw: cr ? cr.width : window.innerWidth };
        openConnectMenu({
          sx: cx,
          sy: cy,
          fx: fp.x,
          fy: fp.y,
          source: state.fromNode.id,
          bx: menuCr.bx,
          bw: menuCr.bw,
        });
      }
    },
    [isValidConnection, connect, openConnectMenu, rf],
  );

  // ── 面板拖拽会话(指针驱动):不用 HTML5 原生拖拽 —— 原生拖拽的幽灵图由 OS 渲染,
  //    macOS/Electron 上跟手延迟明显;自绘幽灵走 transform,和画布拖节点一样跟手。
  //    原地松手 = 点击添加;落到连线中间 = 插入。 ──
  const paletteDrag = useStore((s) => s.paletteDrag);
  const cancelPaletteDrag = useStore((s) => s.cancelPaletteDrag);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const ghostRef = useRef<HTMLDivElement | null>(null);
  const dragSession = useRef<{
    startX: number | null;
    startY: number;
    moved: boolean;
  } | null>(null);
  // 窗口级监听器只在会话开始时挂一次,spec/meta 经 ref 转发保证读到最新值
  const specRef = useRef(spec);
  specRef.current = spec;
  const metaRef = useRef(metaByKind);
  metaRef.current = metaByKind;

  // useLayoutEffect:监听器随渲染同步挂载,极速点击(pointerdown 紧跟 pointerup)也不会漏掉
  useLayoutEffect(() => {
    if (!paletteDrag) return;
    dragSession.current = { startX: null, startY: 0, moved: false };
    document.body.classList.add("fk-dragging");

    const insideCanvas = (x: number, y: number) => {
      const r = wrapRef.current?.getBoundingClientRect();
      return !!r && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    };

    const onMove = (e: PointerEvent) => {
      const s = dragSession.current;
      if (!s) return;
      if (s.startX === null) {
        s.startX = e.clientX;
        s.startY = e.clientY;
      }
      if (
        !s.moved &&
        Math.hypot(e.clientX - s.startX, e.clientY - s.startY) > 3
      )
        s.moved = true;
      if (ghostRef.current) {
        ghostRef.current.style.opacity = "1";
        ghostRef.current.style.transform = `translate(${e.clientX + 12}px, ${e.clientY + 10}px)`;
      }
      const inside = insideCanvas(e.clientX, e.clientY);
      setDropReady(inside);
      if (!inside || !rf || !specRef.current) {
        setSplitTarget(null);
        return;
      }
      const now = Date.now();
      if (now - splitCheckAt.current >= 90) {
        splitCheckAt.current = now;
        const hit = findEdgeNear(
          specRef.current,
          metaRef.current,
          rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
        );
        setSplitTarget(hit?.id ?? null);
      }
    };

    const onUp = (e: PointerEvent) => {
      const s = dragSession.current;
      if (!s) return;
      const { kind, defaults } = paletteDrag;
      const spec = specRef.current;
      const inside = insideCanvas(e.clientX, e.clientY);
      if (!s.moved) {
        addNode(kind, defaults);
      } else if (inside && spec && rf) {
        const pos = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
        const size = nodeSize(metaRef.current[kind]);
        const topLeft = {
          x: Math.round(pos.x - size.w / 2),
          y: Math.round(pos.y - size.h / 2),
        };
        const hitEdge = findEdgeNear(spec, metaRef.current, pos);
        if (hitEdge)
          insertNodeOnFlow(
            kind,
            defaults,
            topLeft,
            hitEdge.source,
            hitEdge.target,
          );
        else addNode(kind, defaults, topLeft);
      }
      cancelPaletteDrag();
      setDropReady(false);
      setSplitTarget(null);
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        cancelPaletteDrag();
        setDropReady(false);
        setSplitTarget(null);
      }
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("fk-dragging");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
    };
  }, [paletteDrag, rf, addNode, insertNodeOnFlow, cancelPaletteDrag]);

  if (!spec) return null;

  const dragMeta = paletteDrag ? metaByKind[paletteDrag.kind]?.ui : undefined;
  return (
    <div
      ref={wrapRef}
      className={`canvas-wrap ${dropReady ? "drop-ready" : ""}`}
    >
      <div className="fk-align-guides" ref={guidesRef} />
      <ReactFlow
        key={spec.code}
        nodes={renderNodes}
        edges={elements.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes as EdgeTypes}
        onNodesChange={onNodesChangeWithAlign}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onConnect={onConnect}
        onConnectStart={onConnectStart}
        onConnectEnd={onConnectEnd}
        onPaneClick={() => {
          select(null);
          setPendingConnect(null);
        }}
        onNodeContextMenu={(event, node) => {
          event.preventDefault();
          select({ type: "node", id: node.id });
          useStore.getState().openContextMenu({ x: event.clientX, y: event.clientY, kind: 'node', id: node.id });
        }}
        onEdgeContextMenu={(event, edge) => {
          event.preventDefault();
          select({ type: "edge", id: edge.id });
          useStore.getState().openContextMenu({ x: event.clientX, y: event.clientY, kind: 'edge', id: edge.id });
        }}
        onPaneContextMenu={(event) => event.preventDefault()}
        onMoveStart={() => useStore.getState().closeContextMenu()}
        isValidConnection={isValidConnection}
        onlyRenderVisibleElements
        // 拖节点到视口边缘时不自动平移画布 —— 画布跟着鼠标跑是"失控/卡"手感的来源
        // (需要平移时滚轮/空格拖/触摸板即可;连线拖到边缘的自动平移保留)
        autoPanOnNodeDrag={false}
        connectionRadius={28}
        connectionLineType={ConnectionLineType.SmoothStep}
        connectionLineStyle={{
          stroke: "#2f6bff",
          strokeWidth: 2.2,
          strokeDasharray: "7 5",
        }}
        zoomOnDoubleClick={false}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.08}
        maxZoom={1.8}
        deleteKeyCode={null}
        multiSelectionKeyCode={null}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={22}
          size={1.6}
          color="#d7dee9"
        />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeStrokeWidth={3} className="fk-minimap" />
      </ReactFlow>
      <CanvasBanners
        pendingConnect={pendingConnect}
        hintOpen={hintOpen}
        closeHint={() => setHintOpen(false)}
      />
      <ConnectMenu />
      <ContextMenu />
      {paletteDrag &&
        createPortal(
          <div ref={ghostRef} className="palette-ghost" style={{ opacity: 0 }}>
            <span
              className="palette-ico"
              style={{
                background: `${dragMeta?.color ?? "#2563eb"}1a`,
                color: dragMeta?.color ?? "#2563eb",
              }}
            >
              <Icon name={dragMeta?.icon} />
            </span>
            <span>{paletteDrag.label}</span>
          </div>,
          document.body,
        )}
    </div>
  );
}

function CanvasBanners({
  pendingConnect,
  hintOpen,
  closeHint,
}: {
  pendingConnect: string | null;
  hintOpen: boolean;
  closeHint: () => void;
}) {
  return (
    <>
      {pendingConnect && (
        <div className="connect-banner">
          正在从 <b>{pendingConnect}</b> 拉线 —
          点击目标节点完成连接,点击空白处取消
        </div>
      )}
      {hintOpen && !pendingConnect && (
        <div className="canvas-hint-banner">
          <span>
            <b>加节点</b>:把左侧类型拖进画布(拖到连线中间 = 直接插入) ·{" "}
            <b>连线</b>:从节点右侧圆点<b>拖到</b>目标节点 ·<b> 调位置</b>
            :直接拖动节点
          </span>
          <button className="hint-close" onClick={closeHint} title="关闭提示">
            ×
          </button>
        </div>
      )}
    </>
  );
}
