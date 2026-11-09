/**
 * @file 图谱画布绘制回调（nodeCanvasObject / linkCanvasObject）
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5）。
 * 两个 `useCallback` 的依赖数组是**关键**：`nodeCanvasObject` 的依赖里带着
 * 选中/悬停/创建目标/搜索结果，`linkCanvasObject` 的依赖里带着选中/悬停/关系类型高亮。
 * 依赖一旦多写或少写，画布要么不重绘、要么每次鼠标移动重绘整张图（§2.8 F-8）。
 *
 * ────────────────────────────────────────────────────────────────────────
 * ★ 性能批次 1 的两处改动（都在依赖数组上做文章，理由写在这里）：
 *
 * 1. **搜索结果传集合而不是数组**：原先每次重绘都做
 *    `searchResults.some(r => r.id === node.id)`，是"节点数 × 搜索结果数"的
 *    线性扫描（1181 × 20）。现在在 `useMemo` 里建成 `Set`，
 *    依赖仍然是 `searchResults` 本身 —— 它的引用只在搜索真的返回新结果时变，
 *    所以"什么时候重绘"的行为与改动前一致。
 * 2. **`lowDetail` 进依赖**：它由节点数决定（`isLargeGraph`），
 *    同一张图内是常量，不会引起额外重绘。
 */
import { useCallback, useMemo } from 'react';
import type { GraphSearchNode } from '../../api/client';
import type { ForceGraphLink, ForceGraphNode } from '../../components/graph/types';
import { useTranslation } from '../../i18n';
import { drawGraphNode } from './drawNode';
import { drawGraphLink } from './drawLink';

interface UseCanvasObjectsOptions {
  selectedNode: ForceGraphNode | null;
  hoverNode: ForceGraphNode | null;
  createMode: boolean;
  createFirstNode: ForceGraphNode | null;
  createSecondNode: ForceGraphNode | null;
  searchResults: GraphSearchNode[];
  selectedLink: ForceGraphLink | null;
  hoverLink: ForceGraphLink | null;
  highlightedRelationType: string | null;
  /** 大图降级（见 `components/graph/types.ts` 的 `LARGE_GRAPH_NODES`） */
  lowDetail: boolean;
}

export function useCanvasObjects({
  selectedNode,
  hoverNode,
  createMode,
  createFirstNode,
  createSecondNode,
  searchResults,
  selectedLink,
  hoverLink,
  highlightedRelationType,
  lowDetail,
}: UseCanvasObjectsOptions) {
  /** 搜索命中的 id 集合：把每帧每节点的线性扫描换成 O(1) 查表 */
  const searchResultIds = useMemo(
    () => new Set(searchResults.map((result) => result.id)),
    [searchResults],
  );

  /**
   * 翻译函数 —— 它**必须进两个 `useCallback` 的依赖数组**。
   *
   * 画布上有两处文字（节点里的首字、边中点的关系类型），而 canvas 里拿不到 hook，
   * 所以把 `t` **注入**进绘制函数（而不是让绘制函数自己读全局语言）：
   *
   *   - `t` 的引用只在**语言变化**时改变（`useTranslation` 内部 `useCallback([locale])`），
   *     于是切语言 → 两个回调引用变化 → force-graph 重绘一次，
   *     "切到英文"不会留下一屏中文；
   *   - 绘制函数保持纯函数（测试可以注入任意 `t`，不必先摆好全局语言）。
   *
   * 代价：一次语言切换触发一次整图重绘（不是每帧），与既有的选中/悬停重绘同量级。
   */
  const { t } = useTranslation();

  /** 自定义节点绘制 */
  const nodeCanvasObject = useCallback(
    (node: ForceGraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      drawGraphNode(node, ctx, globalScale, {
        selectedNode,
        hoverNode,
        createMode,
        createFirstNode,
        createSecondNode,
        searchResultIds,
        lowDetail,
        t,
      });
    },
    [
      selectedNode,
      hoverNode,
      createMode,
      createFirstNode,
      createSecondNode,
      searchResultIds,
      lowDetail,
      t,
    ],
  );

  /** 自定义边绘制 */
  const linkCanvasObject = useCallback(
    (link: ForceGraphLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
      drawGraphLink(link, ctx, globalScale, {
        selectedLink,
        hoverLink,
        highlightedRelationType,
        t,
      });
    },
    [selectedLink, hoverLink, highlightedRelationType, t],
  );

  return { nodeCanvasObject, linkCanvasObject };
}
