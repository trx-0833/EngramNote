import type { RefObject, MutableRefObject } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
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
}: GraphCanvasProps) {
  return (
    <div className={styles.graphCanvas} ref={graphCanvasRef} style={{ flex: 1 }}>
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
        linkDirectionalArrowLength={3}
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

      {/* 缩放控件 */}
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
          +
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
          −
        </button>
        <button
          className={styles.graphControlBtn}
          onClick={() => {
            const fg = graphRef.current;
            if (fg) fg.zoomToFit(400, 60);
          }}
          aria-label="适应屏幕"
          title="适应屏幕"
          style={{ fontSize: '0.85rem' }}
        >
          ⤢
        </button>
      </div>
    </div>
  );
}
