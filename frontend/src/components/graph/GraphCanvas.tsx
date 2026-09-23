import type { KeyboardEvent, RefObject, MutableRefObject } from 'react';
import { useId } from 'react';
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
}: GraphCanvasProps) {
  /**
   * 画布用法说明的元素 id（`aria-describedby` 用）。
   * `useId` 而不是写死字符串：一个页面上可能有多个画布实例（局部图谱面板正在长），
   * 写死会造成重复 id —— 那是 HTML 合法性错误，也是 axe 会报的一类。
   */
  const hintId = useId();

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
        // 连线流光粒子：能量沿墨线缓慢流动（水墨丹青动效）
        linkDirectionalParticles={(link: ForceGraphLink) =>
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
