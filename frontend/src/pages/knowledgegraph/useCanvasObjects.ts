/**
 * @file 图谱画布绘制回调（nodeCanvasObject / linkCanvasObject）
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 两个 `useCallback` 的依赖数组与拆分前**逐项一致** —— `nodeCanvasObject` 的依赖里
 * 带着选中/悬停/创建目标/搜索结果，`linkCanvasObject` 的依赖里带着选中/悬停/关系类型高亮。
 * 依赖一旦多写或少写，画布要么不重绘、要么每次鼠标移动重绘整张图（§2.8 F-8）。
 */
import { useCallback } from 'react';
import type { GraphSearchNode } from '../../api/client';
import type { ForceGraphLink, ForceGraphNode } from '../../components/graph/types';
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
}: UseCanvasObjectsOptions) {
  /** 自定义节点绘制 */
  const nodeCanvasObject = useCallback(
    (node: ForceGraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      drawGraphNode(node, ctx, globalScale, {
        selectedNode,
        hoverNode,
        createMode,
        createFirstNode,
        createSecondNode,
        searchResults,
      });
    },
    [selectedNode, hoverNode, createMode, createFirstNode, createSecondNode, searchResults],
  );

  /** 自定义边绘制 */
  const linkCanvasObject = useCallback(
    (link: ForceGraphLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
      drawGraphLink(link, ctx, globalScale, { selectedLink, hoverLink, highlightedRelationType });
    },
    [selectedLink, hoverLink, highlightedRelationType],
  );

  return { nodeCanvasObject, linkCanvasObject };
}
