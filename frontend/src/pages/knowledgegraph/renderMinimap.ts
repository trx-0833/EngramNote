/**
 * @file 图谱缩略图（minimap）绘制
 * @description 自 `pages/KnowledgeGraph.tsx` 的 `drawMinimap` 拆分（overhaul-plan 5.5），
 * **只搬不改**：画布尺寸、宣纸底色、边/节点的归一化坐标换算、视口框的虚线绘制
 * 全部逐字保留，仅把闭包读取的 ref（canvas / graphData / viewport / 容器）
 * 改成显式入参。
 *
 * `getContext('2d')` 仍在本函数内、且在**数据为空时不会被调用**（与拆分前同一顺序）：
 * jsdom 里 `getContext` 返回 null，页面据此提前返回，不画任何东西。
 */
import type { GraphData } from '../../api/client';
import { type ForceGraphNode, getGraphCanvasTokens } from '../../components/graph/types';
import { cardTypeColors as CARD_TYPE_COLORS, FALLBACK_CATEGORY_COLOR } from '../../utils/labels';

/** 当前视口信息（由 GraphCanvas 的 onZoom 写入） */
export interface GraphViewport {
  k: number;
  x: number;
  y: number;
}

/**
 * 绘制 minimap 缩略图
 *
 * @param canvas 缩略图画布（未挂载时为 null，直接返回）
 * @param graphData 归一化后的图谱数据（为空时不绘制）
 * @param viewport 当前视口（k / x / y）
 * @param graphContainer 画布容器（用于换算视口框尺寸，未挂载时跳过视口框）
 */
export function renderMinimap(
  canvas: HTMLCanvasElement | null,
  graphData: GraphData | null,
  viewport: GraphViewport,
  graphContainer: HTMLElement | null,
) {
  if (!canvas || !graphData || graphData.nodes.length === 0) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const W = canvas.width;
  const H = canvas.height;

  const nodes = graphData.nodes as ForceGraphNode[];

  // 性能批次 1：原来这里是
  //     const xs = nodes.map(n => n.x || 0); const ys = ...
  //     let minX = Math.min(...xs), maxX = Math.max(...xs); ...
  // 两个问题：① `Math.min(...arr)` 把 1181 个参数摊开压进调用栈（数组一大就危险）；
  // ② 白建两个等长数组。改成一次循环直接求边界。
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const x = node.x || 0;
    const y = node.y || 0;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  if (!Number.isFinite(minX) || !Number.isFinite(maxX)) {
    minX = 0;
    maxX = 0;
  }
  if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
    minY = 0;
    maxY = 0;
  }
  if (maxX - minX < 1) {
    maxX = minX + 1;
  }
  if (maxY - minY < 1) {
    maxY = minY + 1;
  }

  const padX = (maxX - minX) * 0.1;
  const padY = (maxY - minY) * 0.1;
  minX -= padX;
  maxX += padX;
  minY -= padY;
  maxY += padY;

  const scaleX = W / (maxX - minX);
  const scaleY = H / (maxY - minY);
  const scale = Math.min(scaleX, scaleY);
  const offsetX = (W - (maxX - minX) * scale) / 2;
  const offsetY = (H - (maxY - minY) * scale) / 2;

  const mx = (x: number) => (x - minX) * scale + offsetX;
  const my = (y: number) => (y - minY) * scale + offsetY;

  ctx.clearRect(0, 0, W, H);
  // 宣纸底 / 淡墨连线：批次 E4 起读 `--graph-paper` 与 `--graph-ink-faint`
  // （原来是与令牌同源的手写字面量 `rgba(245, 240, 228, 0.75)` / `rgba(154, 154, 176, 0.4)`，
  //  读不到令牌时的回退值就是它们，见 `graph/types.ts` 的 `GRAPH_CANVAS_TOKEN_FALLBACKS`）
  const tokens = getGraphCanvasTokens();
  ctx.fillStyle = tokens.paper;
  ctx.fillRect(0, 0, W, H);

  ctx.strokeStyle = tokens.inkFaint;
  ctx.lineWidth = 0.5;
  // 性能批次 1：原先每条边都要 `nodes.find(...)` 两次 —— 260 条边 × 1181 个节点
  // ≈ 每帧三十万次比较，而 minimap 每次缩放都会重画（100ms 节流只是把频率压下来，
  // 单次成本没变）。这里先建一次 id → 节点 的索引，整体降到 O(N + E)。
  const nodeById = new Map<string, ForceGraphNode>();
  for (const node of nodes) nodeById.set(node.id, node);
  graphData.edges.forEach((e) => {
    const src = nodeById.get(e.source);
    const tgt = nodeById.get(e.target);
    if (!src || !tgt) return;
    ctx.beginPath();
    ctx.moveTo(mx(src.x || 0), my(src.y || 0));
    ctx.lineTo(mx(tgt.x || 0), my(tgt.y || 0));
    ctx.stroke();
  });

  nodes.forEach((n) => {
    const color = CARD_TYPE_COLORS[n.card_type] || FALLBACK_CATEGORY_COLOR;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(mx(n.x || 0), my(n.y || 0), 1.5, 0, 2 * Math.PI);
    ctx.fill();
  });

  // 绘制当前视口框
  const vp = viewport;
  if (graphContainer) {
    const vw = graphContainer.clientWidth / vp.k;
    const vh = graphContainer.clientHeight / vp.k;
    const vx = -vp.x / vp.k;
    const vy = -vp.y / vp.k;
    ctx.strokeStyle = 'rgba(15, 52, 96, 0.7)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.strokeRect(mx(vx), my(vy), vw * scale, vh * scale);
    ctx.setLineDash([]);
  }
}
