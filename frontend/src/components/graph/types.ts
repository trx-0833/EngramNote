/**
 * @file 知识图谱可视化组件共享类型与常量
 * @description 自 pages/KnowledgeGraph.tsx 拆分（只搬不改），供 components/graph/* 复用。
 */
import type { ForceGraphMethods, NodeObject, LinkObject } from 'react-force-graph-2d';
import type { GraphNode } from '../../api/client';

/** 关系类型 → 中文标签 */
export const RELATION_TYPE_LABELS: Record<string, string> = {
  related: '相关',
  prerequisite: '前置',
  subsequent: '后续',
  contrast: '对比',
};

/**
 * 关系类型 → 边颜色（**批次 E4 起降级为辅助通道**）
 *
 * 区分关系类型的**主通道是线型**（见下方 `RELATION_LINE_DASHES`）：颜色只做辅助，
 * 色觉障碍用户不依赖它也能读出类型。这里保留颜色是因为它仍然承担
 * "哪种关系的墨色更重"这层观感（前提=深海蓝、对比=朱红…），不是唯一线索。
 *
 * ⚠️ **A4 修正**：`related` 与 `subsequent` 用的曾是 a11y 压深**之前**的旧值
 * （`#9a9ab0` 三级文字只有 2.75:1、`#2d8a56` 成功色只有 4.30:1）。
 * 全局令牌在那一轮改了，但**这两张 ts 侧色表没有跟上** ——
 * 于是图谱的边色与界面其它地方的同义色并不是同一个颜色。
 * 现值与令牌对齐：`#6f6f8a` = `--color-text-tertiary`、`#25714a` = `--color-success`。
 *
 * 为什么这里保留字面量而不是 `var(--…)`：canvas 绘制拿不到 CSS 变量
 * （每帧 `getComputedStyle` 不划算）。所以它是**与令牌同源**的字面量 ——
 * 批次 F2 会加一条检查，把这种"手工同步"钉住，不让它再漂移。
 */
export const RELATION_TYPE_COLORS: Record<string, string> = {
  related: '#6f6f8a', // 淡墨（= --color-text-tertiary）
  prerequisite: '#0f3460', // 深海蓝（= --color-primary）
  subsequent: '#25714a', // 墨绿（= --color-success）
  contrast: '#c0392b', // 朱红（= --color-error）
};

/** 关系线的线型（批次 E4：类型的主通道） */
export type RelationLineStyle = 'solid' | 'dashed' | 'longDash' | 'dotted';

/**
 * 线型 → `ctx.setLineDash` 图案（单位 px；绘制时按 `globalScale` 缩放）
 *
 * ⚠️ **与 `Graph.module.css` 里 `.graphRelationLine*` 那几条
 * `repeating-linear-gradient` 一一对应**（图例画的就是画布上那条线）：
 * `dashed` = 5/9、`longDash` = 10/16、`dotted` = 1.5/5 的周期。
 * 改一处必须改另一处，否则图例与画布各说一套。
 */
export const RELATION_LINE_DASHES: Record<RelationLineStyle, number[]> = {
  solid: [],
  dashed: [5, 4],
  longDash: [10, 6],
  dotted: [1.5, 3.5],
};

/**
 * 关系类型 → 线型（`docs/visual-design-spec.md` §6.4）
 *
 * 设计点名的是三类：**实线 = 前提、虚线 = 相关、点线 = 对比**。
 * `subsequent`（后续）在设计里没被点名，但它是接口里真实存在的第四类
 * （`backend/openapi.json` 的 `RelationType`：`prerequisite` 是"card_id_1 是
 * card_id_2 的前置知识"，`subsequent` 是它的**镜像写法**）。
 * 若只给三类线型，"后续"就只能与"相关"共用虚线 ⇒ 类型之间又分不开了。
 * 所以它拿**长虚线**：四类各有一种线型，图例能逐行对上。
 */
export const RELATION_TYPE_LINE_STYLES: Record<string, RelationLineStyle> = {
  related: 'dashed',
  prerequisite: 'solid',
  subsequent: 'longDash',
  contrast: 'dotted',
};

/**
 * 未知关系类型时的兜底线型 = 相关（虚线）。
 * 理由与 `FALLBACK_RELATION_COLOR` 一致：读不出来的类型**不能冒充**
 * "前提 / 后续"这种强主张，用最弱的一种。
 */
export const FALLBACK_RELATION_LINE_STYLE: RelationLineStyle = 'dashed';

/** 取某个关系类型的线型（未知类型走兜底） */
export function getRelationLineStyle(relationType: string): RelationLineStyle {
  return RELATION_TYPE_LINE_STYLES[relationType] ?? FALLBACK_RELATION_LINE_STYLE;
}

/** 取某个关系类型的虚线图案，并按 `globalScale` 缩放（缩放后线宽与图案同步） */
export function getRelationDash(relationType: string, globalScale: number): number[] {
  return RELATION_LINE_DASHES[getRelationLineStyle(relationType)].map((n) => n / globalScale);
}

/**
 * 有向关系：只有「前提 / 后续」两类（`openapi.json` 里它们的语义是
 * "card_id_1 是 card_id_2 的前置/后续知识"）。相关与对比是无向的，
 * 给它们画箭头是噪音 —— 批次 E4 把箭头也收进"类型通道"。
 */
const DIRECTED_RELATION_TYPES = new Set(['prerequisite', 'subsequent']);

/** 这个关系类型有没有方向（决定画不画箭头） */
export function hasDirection(relationType: string): boolean {
  return DIRECTED_RELATION_TYPES.has(relationType);
}

/**
 * 未知关系类型时的兜底边色（= `--color-text-tertiary` 的淡墨）。
 *
 * A4 之前这个字面量 `#9a9ab0` 在 4 个文件里各写了一遍
 * （`drawLink.ts` / `GraphCanvas.tsx` ×2 / `GraphSidebar.tsx`），
 * 而且和上面那张表用的是**同一支被淘汰的旧灰**。
 * 收敛到一处之后，"改一次、四处跟着变"才成立。
 */
export const FALLBACK_RELATION_COLOR = '#6f6f8a';

/** 节点形状类型 */
export type NodeShape = 'circle' | 'diamond' | 'rounded' | 'hexagon';

/** 卡片类型 → 节点形状 */
export const CARD_TYPE_SHAPES: Record<string, NodeShape> = {
  concept: 'circle',
  formula: 'diamond',
  qa: 'rounded',
  definition: 'hexagon',
};

/** 卡片类型 → 节点内显示的首字 */
export const CARD_TYPE_INITIALS: Record<string, string> = {
  concept: '概',
  formula: '式',
  qa: '问',
  definition: '定',
};

/** 关系类型选项，用于创建关系表单 */
export const RELATION_TYPE_OPTIONS = [
  { value: 'related', label: '相关' },
  { value: 'prerequisite', label: '前置' },
  { value: 'subsequent', label: '后续' },
  { value: 'contrast', label: '对比' },
];

/** 力导向图内部节点类型 */
export interface ForceGraphNode extends GraphNode {
  x?: number;
  y?: number;
  __bckgDimensions?: [number, number];
}

/** 力导向图内部边类型 */
export interface ForceGraphLink {
  id: string;
  source: string | ForceGraphNode;
  target: string | ForceGraphNode;
  relation_type: string;
  status: string;
  /**
   * 相似度分数
   *
   * 阶段 5.1 / S2：契约里 `GraphEdge.similarity_score` 是 `?: number | null`
   * （既可能缺省、也可能是 null；`GET /graph` 与 `/graph/node/{id}/subgraph`
   * 两条构建路径的口径本来就不同）。这里跟着改成可选 ——
   * 读取方（`getLinkWidth` / GraphCanvas / GraphSidebar）用的都是 `== null` / `!= null`，
   * 对 `undefined` 与 `null` 的处理**完全一致**，所以这是纯类型修正，
   * 没有任何运行时行为变化。
   */
  similarity_score?: number | null;
}

/** 侧边栏面板类型 */
export type SidebarPanel = 'suggestions' | 'nodeDetail' | 'createRelation' | 'viewSubgraph' | null;

/** 力导向图 ref 类型 */
export type GraphForceRef = ForceGraphMethods<
  NodeObject<ForceGraphNode>,
  LinkObject<ForceGraphNode, ForceGraphLink>
>;

/** 力导向图渲染数据 */
export interface ForceGraphViewData {
  nodes: ForceGraphNode[];
  links: ForceGraphLink[];
}

/**
 * 绘制不同形状的节点路径
 */
export function drawNodeShapePath(
  ctx: CanvasRenderingContext2D,
  shape: NodeShape,
  x: number,
  y: number,
  size: number,
) {
  ctx.beginPath();
  switch (shape) {
    case 'circle':
      ctx.arc(x, y, size, 0, 2 * Math.PI);
      break;
    case 'diamond': {
      const d = size * 1.15;
      ctx.moveTo(x, y - d);
      ctx.lineTo(x + d, y);
      ctx.lineTo(x, y + d);
      ctx.lineTo(x - d, y);
      ctx.closePath();
      break;
    }
    case 'rounded': {
      const s = size * 0.95;
      const r = size * 0.25;
      ctx.moveTo(x - s + r, y - s);
      ctx.lineTo(x + s - r, y - s);
      ctx.quadraticCurveTo(x + s, y - s, x + s, y - s + r);
      ctx.lineTo(x + s, y + s - r);
      ctx.quadraticCurveTo(x + s, y + s, x + s - r, y + s);
      ctx.lineTo(x - s + r, y + s);
      ctx.quadraticCurveTo(x - s, y + s, x - s, y + s - r);
      ctx.lineTo(x - s, y - s + r);
      ctx.quadraticCurveTo(x - s, y - s, x - s + r, y - s);
      ctx.closePath();
      break;
    }
    case 'hexagon': {
      for (let i = 0; i < 6; i++) {
        const angle = (Math.PI / 3) * i - Math.PI / 2;
        const px = x + size * Math.cos(angle);
        const py = y + size * Math.sin(angle);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      break;
    }
  }
}

/** 计算节点大小 */
export function getNodeSize(node: ForceGraphNode): number {
  return Math.min(15, Math.max(5, 5 + (node.relation_count || 0) * 2));
}

/** 计算边宽度 */
export function getLinkWidth(link: ForceGraphLink): number {
  const score = link.similarity_score;
  if (score == null) return 1.5;
  return Math.min(3, Math.max(1, score * 3));
}

/*
 * canvas 侧的 `--graph-*` 令牌取值（批次 E4 接通）
 *
 * 背景：审计 §2.6 记的是"图谱与令牌层的连接是断的"——`base.css` 的 `--graph-*` 段
 * 有 6 枚水墨令牌，其中 `--graph-ink` / `--graph-ink-faint` / `--graph-paper-deep`
 * **零引用**；canvas 里则各写各的字面量。批次 A4 判定"canvas 拿不到 `var()`，
 * 接线待 E4"，于是这三条一直挂在 `scripts/design-drift.mjs` 的 `PENDING_WIRING` 上。
 *
 * 这里的做法（`docs/visual-design-spec.md` §6.4 给的两条路里的第一条）：
 * **第一次绘制时读一次 `:root` 并缓存**，于是画布颜色与 `base.css` 是同一个来源，
 * 而不是"靠注释手工同步的字面量"。CSS 侧能写 `var()` 的地方照旧写 `var()`
 * （见 `Graph.module.css` 的 `.graphCanvas::before` / `.graphControls` / `.graphMinimap`）。
 */

/** canvas 真正消费的两枚水墨令牌 */
export interface GraphCanvasTokens {
  /** 宣纸底（`base.css` 的 `--graph-*` 段：`--graph-paper`） */
  paper: string;
  /** 淡墨（同段：`--graph-ink-faint`） */
  inkFaint: string;
}

/**
 * 读不到令牌时的回退值 —— **就是令牌在 `base.css` 里的字面量**（同源）。
 *
 * 为什么必须有：jsdom 里 `getComputedStyle(root).getPropertyValue('--graph-paper')`
 * 返回**空串**，而 canvas 对非法颜色是**静默忽略这次赋值**（不是抛错）——
 * 于是 `fillStyle` 会停留在上一笔的颜色上，画出随机结果且极难排查。
 * 风险登记表（`visual-refactor-plan.md` §9）第 5 条点名了这一点：必须有回退值 + 有测试。
 */
export const GRAPH_CANVAS_TOKEN_FALLBACKS: GraphCanvasTokens = {
  paper: '#f5f0e4',
  inkFaint: 'rgba(26, 26, 46, 0.14)',
};

let graphCanvasTokensCache: GraphCanvasTokens | null = null;

function readGraphCanvasTokens(): GraphCanvasTokens {
  const root = typeof document === 'undefined' ? null : document.documentElement;
  const style = root ? getComputedStyle(root) : null;
  const read = (name: string, fallback: string) => {
    const value = style?.getPropertyValue(name).trim();
    return value ? value : fallback;
  };
  return {
    paper: read('--graph-paper', GRAPH_CANVAS_TOKEN_FALLBACKS.paper),
    inkFaint: read('--graph-ink-faint', GRAPH_CANVAS_TOKEN_FALLBACKS.inkFaint),
  };
}

/** 取 canvas 用的水墨令牌（读一次并缓存；每帧 `getComputedStyle` 不划算） */
export function getGraphCanvasTokens(): GraphCanvasTokens {
  if (!graphCanvasTokensCache) {
    graphCanvasTokensCache = readGraphCanvasTokens();
  }
  return graphCanvasTokensCache;
}

/** 清掉令牌缓存（**只给测试用**：用例要分别模拟"读得到"与"读不到"两种浏览器状态） */
export function resetGraphCanvasTokensCache(): void {
  graphCanvasTokensCache = null;
}
