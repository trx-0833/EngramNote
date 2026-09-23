/**
 * @file 图谱节点绘制（nodeCanvasObject 的函数体）
 * @description 自 `pages/KnowledgeGraph.tsx` 的 `nodeCanvasObject` useCallback 拆分
 * （overhaul-plan 5.5），**只搬不改**：坐标守卫、高关联度外发光环、搜索高亮环、
 * 选中/悬停光环、玻璃珠质感（外发光 + 径向渐变 + 高光点）、首字与标签绘制的
 * 顺序与数值全部逐字保留。
 *
 * ★ 第一条守卫（无坐标 / 非有限坐标直接 return）是被测试压住的白屏路径：
 * 真实 canvas 对 `undefined` / `NaN` 直接抛 TypeError，力导向布局起步阶段
 * 节点坐标就是 undefined。拆分时不得删改。
 */
import type { GraphSearchNode } from '../../api/client';
import {
  type ForceGraphNode,
  CARD_TYPE_SHAPES,
  CARD_TYPE_INITIALS,
  drawNodeShapePath,
  getGraphCanvasTokens,
  getNodeSize,
} from '../../components/graph/types';
import { cardTypeColors as CARD_TYPE_COLORS, FALLBACK_CATEGORY_COLOR } from '../../utils/labels';

/** 节点绘制依赖的页面状态（选中/悬停/创建目标/搜索命中） */
export interface NodeDrawState {
  selectedNode: ForceGraphNode | null;
  hoverNode: ForceGraphNode | null;
  createMode: boolean;
  createFirstNode: ForceGraphNode | null;
  createSecondNode: ForceGraphNode | null;
  searchResults: GraphSearchNode[];
}

/** 自定义节点绘制 */
export function drawGraphNode(
  node: ForceGraphNode,
  ctx: CanvasRenderingContext2D,
  globalScale: number,
  state: NodeDrawState,
) {
  const { selectedNode, hoverNode, createMode, createFirstNode, createSecondNode, searchResults } =
    state;

  // 力导向布局过程中节点坐标可能为 undefined/NaN，跳过无效坐标避免 canvas 抛错
  if (node.x == null || node.y == null || !Number.isFinite(node.x) || !Number.isFinite(node.y)) {
    return;
  }
  const size = getNodeSize(node);
  const color = CARD_TYPE_COLORS[node.card_type] || FALLBACK_CATEGORY_COLOR;
  const shape = CARD_TYPE_SHAPES[node.card_type] || 'circle';
  const initial = CARD_TYPE_INITIALS[node.card_type] || '';

  const isSelected = selectedNode?.id === node.id;
  const isHovered = hoverNode?.id === node.id;
  const isCreateTarget =
    createMode && (createFirstNode?.id === node.id || createSecondNode?.id === node.id);
  const isHub = (node.relation_count || 0) >= 3;
  // 搜索高亮
  const isSearchHit = searchResults.some((r) => r.id === node.id);

  // 高关联度节点静态外发光环
  if (isHub) {
    ctx.beginPath();
    ctx.arc(node.x!, node.y!, size + 3, 0, 2 * Math.PI);
    ctx.fillStyle = `${color}1f`;
    ctx.fill();
  }

  // 搜索高亮环
  if (isSearchHit && !isSelected && !isHovered) {
    ctx.beginPath();
    ctx.arc(node.x!, node.y!, size + 5, 0, 2 * Math.PI);
    ctx.fillStyle = 'rgba(201, 169, 89, 0.15)';
    ctx.fill();
  }

  // 选中/悬停时绘制金色光环
  if (isSelected || isHovered || isCreateTarget) {
    ctx.beginPath();
    ctx.arc(node.x!, node.y!, size + 4 / globalScale, 0, 2 * Math.PI);
    ctx.fillStyle = 'rgba(201, 169, 89, 0.25)';
    ctx.fill();
  }

  // 节点主体（玻璃珠质感：增强外发光 + 径向渐变内透光 + 反射高光点）
  ctx.save();
  ctx.shadowColor = `${color}80`;
  ctx.shadowBlur = 8 / globalScale;
  ctx.shadowOffsetX = 0;
  ctx.shadowOffsetY = 1.5 / globalScale;

  drawNodeShapePath(ctx, shape, node.x!, node.y!, size);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();

  // 玻璃内透光：左上高光 → 透明（径向渐变叠加，营造玻璃珠内部透光感）
  drawNodeShapePath(ctx, shape, node.x!, node.y!, size);
  const glassGrad = ctx.createRadialGradient(
    node.x! - size * 0.35,
    node.y! - size * 0.35,
    size * 0.1,
    node.x!,
    node.y!,
    size,
  );
  glassGrad.addColorStop(0, 'rgba(255, 255, 255, 0.55)');
  glassGrad.addColorStop(0.5, 'rgba(255, 255, 255, 0.12)');
  glassGrad.addColorStop(1, 'rgba(255, 255, 255, 0)');
  ctx.fillStyle = glassGrad;
  ctx.fill();

  // 玻璃珠深色边缘（轮廓）—— 批次 E4：改读 `--graph-ink-faint`（淡墨）。
  // 原值是 `rgba(0, 0, 0, 0.14)`：alpha 与令牌**逐字相同**，只是色相从纯黑
  // 换成墨阶的 `rgba(26, 26, 46, …)`（同一枚令牌也用在 minimap 的连线上）。
  drawNodeShapePath(ctx, shape, node.x!, node.y!, size);
  ctx.strokeStyle = getGraphCanvasTokens().inkFaint;
  ctx.lineWidth = 1 / globalScale;
  ctx.stroke();

  // 玻璃反射高光点（左上）
  ctx.beginPath();
  ctx.arc(
    node.x! - size * 0.32,
    node.y! - size * 0.38,
    Math.max(size * 0.22, 1.2 / globalScale),
    0,
    2 * Math.PI,
  );
  ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
  ctx.fill();

  // 选中/悬停时金色描边
  if (isSelected || isHovered || isCreateTarget) {
    drawNodeShapePath(ctx, shape, node.x!, node.y!, size);
    ctx.strokeStyle = '#c9a959';
    ctx.lineWidth = 2 / globalScale;
    ctx.stroke();
  }

  // 搜索高亮描边
  if (isSearchHit && !isSelected && !isHovered) {
    drawNodeShapePath(ctx, shape, node.x!, node.y!, size);
    ctx.strokeStyle = '#c9a959';
    ctx.lineWidth = 1.5 / globalScale;
    ctx.stroke();
  }

  // 节点内显示类型首字
  if (size >= 8 && initial) {
    const fontSize = Math.max(size * 0.9, 6 / globalScale);
    ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.fillText(initial, node.x!, node.y! + 0.5 / globalScale);
  }

  // 标签
  if (globalScale > 0.8 || isHovered || isSelected) {
    const label = node.title;
    const fontSize = Math.max(12 / globalScale, 3);
    ctx.font = `500 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
    const textWidth = ctx.measureText(label).width;
    const bgWidth = textWidth + 6 / globalScale;
    const bgHeight = fontSize + 4 / globalScale;

    ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
    ctx.beginPath();
    ctx.roundRect(
      node.x! - bgWidth / 2,
      node.y! + size + 2 / globalScale,
      bgWidth,
      bgHeight,
      3 / globalScale,
    );
    ctx.fill();

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = isSelected || isHovered ? '#0f3460' : '#1a1a2e';
    ctx.fillText(label, node.x!, node.y! + size + 4 / globalScale);
  }

  node.__bckgDimensions = [size * 2.4, size * 2.4];
}
