import type { KeyboardEvent, RefObject, MutableRefObject } from 'react';
import { useCallback, useEffect, useId, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import Icon from '../Icon';
import Minimap from './Minimap';
// 图谱功能的类名归模块所有（overhaul-plan 5.6 序 10）：见 Graph.module.css 文件头
import styles from './Graph.module.css';
import {
  type ForceGraphNode,
  type ForceGraphLink,
  type GraphForceRef,
  type ForceGraphViewData,
  RELATION_TYPE_COLORS,
  FALLBACK_RELATION_COLOR,
  getNodeSize,
  getLinkWidth,
  hasDirection,
} from './types';

interface GraphCanvasProps {
  graphRef: MutableRefObject<GraphForceRef | undefined>;
  graphCanvasRef: RefObject<HTMLDivElement>;
  minimapRef: RefObject<HTMLCanvasElement>;
  viewportRef: MutableRefObject<{ k: number; x: number; y: number }>;
  minimapTimerRef: MutableRefObject<number | null>;
  drawMinimap: () => void;
  forceGraphData: ForceGraphViewData;
  nodeCanvasObject: (
    node: ForceGraphNode,
    ctx: CanvasRenderingContext2D,
    globalScale: number,
  ) => void;
  linkCanvasObject: (
    link: ForceGraphLink,
    ctx: CanvasRenderingContext2D,
    globalScale: number,
  ) => void;
  onNodeClick: (node: ForceGraphNode) => void;
  onNodeHover: (node: ForceGraphNode | null) => void;
  onLinkClick: (link: ForceGraphLink) => void;
  onLinkHover: (link: ForceGraphLink | null) => void;
  onBackgroundClick: () => void;
  /** 画布区域上的键盘操作（批次 E4：上下方向键切换当前节点） */
  onCanvasKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  /** 大图降级（见 `types.ts` 的 `LARGE_GRAPH_NODES`）：关粒子 + 开视口剔除 */
  lowDetail: boolean;
}

/** 图谱画布：力导向图 + 缩略图 + 缩放控件 */
export default function GraphCanvas({
  graphRef,
  graphCanvasRef,
  minimapRef,
  viewportRef,
  minimapTimerRef,
  drawMinimap,
  forceGraphData,
  nodeCanvasObject,
  linkCanvasObject,
  onNodeClick,
  onNodeHover,
  onLinkClick,
  onLinkHover,
  onBackgroundClick,
  onCanvasKeyDown,
  lowDetail,
}: GraphCanvasProps) {
  /**
   * 画布用法说明的元素 id（`aria-describedby` 用）。
   * `useId` 而不是写死字符串：一个页面上可能有多个画布实例（局部图谱面板正在长），
   * 写死会造成重复 id —— 那是 HTML 合法性错误，也是 axe 会报的一类。
   */
  const hintId = useId();

  /*
   * ── 性能批次 1：视口剔除 ──
   *
   * force-graph 的 `nodeVisibility` 默认恒为 `true`，而它在 `tickFrame` 里是
   * **唯一的过滤条件**（`force-graph.mjs:557` 的 `filter(getVisibility)`）——
   * 也就是说：**离屏节点照样每帧走完整绘制**。放大到只剩几十个节点在屏幕上时，
   * 另外一千多个仍在白画。
   *
   * 判据用的是页面**已经在维护**的 `viewportRef`（minimap 也读它，
   * 由 `onZoom` 写入）：d3-zoom 的变换是 `screen = world * k + (x, y)`。
   *
   * 尺寸必须**缓存**：`clientWidth` 是强制布局读，每帧每节点读一次就是
   * 1181 次布局查询。所以用 ResizeObserver 维护一份，而不是在判据里现读。
   */
  const viewportSizeRef = useRef({ width: 0, height: 0 });
  useEffect(() => {
    const element = graphCanvasRef.current;
    if (!element) return;
    const measure = () => {
      viewportSizeRef.current = { width: element.clientWidth, height: element.clientHeight };
    };
    measure();
    // ⚠️ jsdom **没有** `ResizeObserver`：不判类型直接用会抛
    // `ReferenceError: ResizeObserver is not defined`，而它是在 effect 里抛的 ——
    // 表现为整页渲染被 ErrorBoundary 接住，`KnowledgeGraph.test.tsx` 里
    // 42 条与本改动毫不相干的用例一起变红（第一次实现时真踩到了）。
    // 本仓库的既有做法就是"先判类型再用"（见 `pages/StyleGuide.tsx:208-213` 对
    // `matchMedia` 的处理）。没有观察者时只量一次：判据在尺寸为 0 时一律放行，
    // 也就是"不剔除" —— 退化成改动前的行为，而不是崩掉。
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [graphCanvasRef]);

  /**
   * 节点是否可能出现在屏幕上。
   *
   * 只在大图时挂上去（`lowDetail`）：小图本来就全在屏幕内，挂上去只会多一层判据，
   * 而既有的一切小图场景（a11y 桩、全链路用例、视觉基线）需要的是**行为逐字不变**。
   *
   * 余量给得宽（节点半径 + 24 + 40 像素，且随缩放放大）：剔除错一个边缘节点的代价
   * 是"它忽然不见了"，比多画几个节点难看得多。坐标还没算出来的节点一律放行，
   * 交给绘制侧那条被测试压住的守卫处理。
   */
  const isNodeInViewport = useCallback(
    (node: ForceGraphNode) => {
      if (node.x == null || node.y == null) return true;
      const { width, height } = viewportSizeRef.current;
      if (width === 0 || height === 0) return true;
      const { k, x, y } = viewportRef.current;
      const margin = (getNodeSize(node) + 24) * k + 40;
      const screenX = node.x * k + x;
      const screenY = node.y * k + y;
      return (
        screenX >= -margin &&
        screenX <= width + margin &&
        screenY >= -margin &&
        screenY <= height + margin
      );
    },
    [viewportRef],
  );

  /*
   * ── 批次 E4：当前节点的键盘上下切换 ──
   *
   * canvas 里画的是位图，节点**没有任何 DOM 语义**，键盘与读屏都到不了它；
   * 所以"当前节点是哪个"必须由几样东西共同承担（缺一不可）：
   *   ① 焦点能落到这个区域上，上下方向键由页面处理（`onCanvasKeyDown`）；
   *   ② 区域名 + 用法说明（`aria-label` / `aria-describedby`）；
   *   ③ 页面上 aria-live 的"当前节点：…"播报（见 `pages/KnowledgeGraph.tsx`）——
   *      只更新详情面板是不够的，读屏不会自动念出新内容。
   *
   * `role="application"` 是**刻意的**：读屏的浏览模式会把上下方向键截走
   * （拿去逐行朗读），这里必须让方向键真的送到页面 —— 这正是 ARIA 里
   * application 角色的用途（它也因此要求一个可访问名，见 `aria-label`）。
   *
   * ⚠️ `tabIndex` 只加在**有 role** 的元素上：`e2e/a11y.spec.ts` 的键盘扫描
   * 判据②会把"tabindex 挂在非控件、且没有 role"报出来（axe 自己报不出来这一类）。
   * 焦点环由全局的 `:focus-visible` 提供，不另写样式。
   */
  return (
    <div
      className={styles.graphCanvas}
      ref={graphCanvasRef}
      style={{ flex: 1 }}
      tabIndex={0}
      role="application"
      aria-label="知识图谱画布"
      aria-describedby={hintId}
      onKeyDown={onCanvasKeyDown}
    >
      <ForceGraph2D
        ref={graphRef}
        graphData={forceGraphData}
        nodeCanvasObject={nodeCanvasObject}
        nodePointerAreaPaint={(
          node: ForceGraphNode,
          color: string,
          ctx: CanvasRenderingContext2D,
        ) => {
          if (
            node.x == null ||
            node.y == null ||
            !Number.isFinite(node.x) ||
            !Number.isFinite(node.y)
          )
            return;
          const size = getNodeSize(node);
          ctx.beginPath();
          ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI);
          ctx.fillStyle = color;
          ctx.fill();
        }}
        linkCanvasObject={linkCanvasObject}
        linkCanvasObjectMode={() => 'replace'}
        onNodeClick={onNodeClick}
        onNodeHover={onNodeHover}
        onLinkClick={onLinkClick}
        onLinkHover={onLinkHover}
        onBackgroundClick={onBackgroundClick}
        onZoom={({ k, x, y }: { k: number; x: number; y: number }) => {
          viewportRef.current = { k, x, y };
          if (minimapTimerRef.current == null) {
            minimapTimerRef.current = window.setTimeout(() => {
              drawMinimap();
              minimapTimerRef.current = null;
            }, 100);
          }
        }}
        nodeVal={(node: ForceGraphNode) => node.relation_count}
        linkWidth={getLinkWidth}
        /* 箭头也是"类型通道"的一部分（批次 E4）：只给**有向**的两类
           （前提 / 后续，语义见 openapi.json 的 RelationType 描述）画箭头；
           相关与对比是无向的，画了只是噪音。待审建议一律不画 ——
           线型已经让给关系类型，待审改用"淡色 + 无箭头"两条通道表达。 */
        linkDirectionalArrowLength={(link: ForceGraphLink) =>
          hasDirection(link.relation_type) && link.status !== 'suggested' ? 3 : 0
        }
        linkDirectionalArrowRelPos={1}
        linkColor={(link: ForceGraphLink) => {
          const color = RELATION_TYPE_COLORS[link.relation_type] || FALLBACK_RELATION_COLOR;
          return link.status === 'suggested' ? `${color}88` : color;
        }}
        // 视口剔除（见上面的 `isNodeInViewport`）：小图不挂，行为与改动前逐字一致
        nodeVisibility={lowDetail ? isNodeInViewport : undefined}
        // 连线流光粒子：能量沿墨线缓慢流动（水墨丹青动效）
        //
        // ⚠️ 性能批次 1：大图时**整段关掉**（返回 0 = 该边没有粒子）。
        // 理由在 `force-graph.mjs:1626-1628` 的 doRedraw 判定里：
        //     links.some(d => d.__photons && d.__photons.length)
        // 只要**任何一条边**还有粒子，画布就每帧清屏 + 全量重绘，**永不空闲**。
        // 实测（1181 节点 / 260 边，力导向冷却后的静止窗口，用户什么都没做）：
        //     clearRect 18~20 次/秒
        // 而真实数据里 260 条边的 similarity_score **全部 > 0.6**，
        // 于是每条边 2 个粒子 = 520 个持续动画的粒子。
        // 大图上它们既看不清、又让整机持续满负荷，所以关掉；
        // 小图（≤ `LARGE_GRAPH_NODES`）保持原样。
        linkDirectionalParticles={
          lowDetail
            ? 0
            : (link: ForceGraphLink) =>
                link.similarity_score != null && link.similarity_score > 0.6 ? 2 : 1
        }
        linkDirectionalParticleWidth={2.2}
        linkDirectionalParticleSpeed={0.004}
        linkDirectionalParticleColor={(link: ForceGraphLink) => {
          const color = RELATION_TYPE_COLORS[link.relation_type] || FALLBACK_RELATION_COLOR;
          return link.status === 'suggested' ? '#c9a959' : color;
        }}
        cooldownTicks={100}
        enableNodeDrag={true}
        enableZoomInteraction={true}
        enablePanInteraction={true}
      />

      {/* Minimap */}
      <Minimap minimapRef={minimapRef} />

      {/* 缩放控件 —— 批次 B3：三个按钮原先各走一路字形
          （ASCII `+`、`\u2212` −、`\u2922` ⤢），同一排里视觉重量完全不同；
          现在三枚都是 `<Icon>`（`zoom-in` / `zoom-out` / `fit-screen`），
          尺寸与线宽由 `Icon.tsx` 统一施加。`aria-label` / `title` 逐字保留。 */}
      <div className={styles.graphControls}>
        <button
          className={styles.graphControlBtn}
          onClick={() => {
            const fg = graphRef.current;
            if (fg) fg.zoom(viewportRef.current.k * 1.4, 400);
          }}
          aria-label="放大"
          title="放大"
        >
          <Icon name="zoom-in" size={16} />
        </button>
        <button
          className={styles.graphControlBtn}
          onClick={() => {
            const fg = graphRef.current;
            if (fg) fg.zoom(viewportRef.current.k / 1.4, 400);
          }}
          aria-label="缩小"
          title="缩小"
        >
          <Icon name="zoom-out" size={16} />
        </button>
        <button
          className={styles.graphControlBtn}
          onClick={() => {
            const fg = graphRef.current;
            if (fg) fg.zoomToFit(400, 60);
          }}
          aria-label="适应屏幕"
          title="适应屏幕"
        >
          <Icon name="fit-screen" size={16} />
        </button>
      </div>

      {/* 画布用法说明：视觉上不可见（`.graphA11yStatus`），
          但留在无障碍树里，由上面区域的 `aria-describedby` 引用 ——
          键盘用户聚焦到画布时会听到"按上下方向键切换当前节点" */}
      <div id={hintId} className={styles.graphA11yStatus}>
        按上下方向键切换当前节点，当前节点的详情会显示在侧边栏面板中。
      </div>
    </div>
  );
}
