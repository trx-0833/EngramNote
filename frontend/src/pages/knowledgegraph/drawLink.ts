/**
 * @file 图谱边绘制（linkCanvasObject 的函数体）
 * @description 自 `pages/KnowledgeGraph.tsx` 的 `linkCanvasObject` useCallback 拆分
 * （overhaul-plan 5.5），**只搬不改**：端点守卫、待审边虚线、高亮光晕、
 * 关系类型描边、hover/选中时的中点标签绘制顺序与颜色全部逐字保留。
 *
 * ★ 第一条守卫是被测试压住的白屏路径：force-graph 在布局完成前会把 `source`/`target`
 * 作为**字符串**传进来（甚至是半个对象），此时 `.x` 为 undefined，直接读会抛错。
 * 拆分时不得删改。
 */
import {
  type ForceGraphLink,
  type ForceGraphNode,
  RELATION_TYPE_LABELS,
  RELATION_TYPE_COLORS,
  getLinkWidth,
} from '../../components/graph/types';

/** 边绘制依赖的页面状态（选中/悬停/关系类型高亮） */
export interface LinkDrawState {
  selectedLink: ForceGraphLink | null;
  hoverLink: ForceGraphLink | null;
  highlightedRelationType: string | null;
}

/** 自定义边绘制 */
export function drawGraphLink(
  link: ForceGraphLink,
  ctx: CanvasRenderingContext2D,
  globalScale: number,
  state: LinkDrawState,
) {
  const { selectedLink, hoverLink, highlightedRelationType } = state;

  const src = link.source as ForceGraphNode;
  const tgt = link.target as ForceGraphNode;
  if (
    src.x == null ||
    tgt.x == null ||
    !Number.isFinite(src.x) ||
    !Number.isFinite(src.y!) ||
    !Number.isFinite(tgt.x) ||
    !Number.isFinite(tgt.y!)
  )
    return;

  const isSuggested = link.status === 'suggested';
  const isSelected = selectedLink?.id === link.id;
  const isHovered = hoverLink?.id === link.id;
  const relationColor = RELATION_TYPE_COLORS[link.relation_type] || '#9a9ab0';

  const isDimmed =
    highlightedRelationType != null && link.relation_type !== highlightedRelationType;

  // 选中边高亮光晕
  if (isSelected || isHovered) {
    ctx.beginPath();
    ctx.moveTo(src.x!, src.y!);
    ctx.lineTo(tgt.x!, tgt.y!);
    ctx.setLineDash([]);
    ctx.strokeStyle = `${relationColor}33`;
    ctx.lineWidth = (getLinkWidth(link) * 3) / globalScale;
    ctx.stroke();
  }

  // 主线
  ctx.beginPath();
  ctx.moveTo(src.x!, src.y!);
  ctx.lineTo(tgt.x!, tgt.y!);

  if (isSuggested) {
    ctx.setLineDash([4 / globalScale, 4 / globalScale]);
    ctx.strokeStyle = isSelected
      ? relationColor
      : isDimmed
        ? `${relationColor}22`
        : `${relationColor}88`;
  } else {
    ctx.setLineDash([]);
    ctx.strokeStyle = isSelected
      ? relationColor
      : isDimmed
        ? `${relationColor}22`
        : `${relationColor}A8`; // 墨晕感：非选中连线略淡（原: CC）
  }

  ctx.lineWidth = getLinkWidth(link) / globalScale;
  ctx.stroke();
  ctx.setLineDash([]);

  // hover/选中时在中点显示关系类型标签
  if (isHovered || isSelected) {
    const midX = (src.x! + tgt.x!) / 2;
    const midY = (src.y! + tgt.y!) / 2;
    const label = RELATION_TYPE_LABELS[link.relation_type] || link.relation_type;
    const fontSize = Math.max(11 / globalScale, 3);
    ctx.font = `500 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
    const textWidth = ctx.measureText(label).width;
    const bgWidth = textWidth + 8 / globalScale;
    const bgHeight = fontSize + 4 / globalScale;

    ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
    ctx.beginPath();
    ctx.roundRect(midX - bgWidth / 2, midY - bgHeight / 2, bgWidth, bgHeight, 9999);
    ctx.fill();

    ctx.strokeStyle = `${relationColor}66`;
    ctx.lineWidth = 1 / globalScale;
    ctx.stroke();

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = relationColor;
    ctx.fillText(label, midX, midY);
  }
}
