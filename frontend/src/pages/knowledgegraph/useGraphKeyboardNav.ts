/**
 * @file 图谱画布的键盘导航（批次 E4：当前节点支持上下切换）
 * @description `docs/visual-symbol-research.md` §C3 的「知识图谱」行：
 * "把「当前节点」做成可用键盘上下切换"。`e2e/a11y.spec.ts` 那条键盘扫描
 * （F-37）只能证明"能 Tab 到"，**证明不了"按上下真的换节点"** —— 所以这里是单测。
 *
 * ## 顺序从哪来（这里刻意选"数据顺序"而不是"坐标顺序"）
 *
 * 顺序 = `buildForceGraphData` 的输出顺序（= 画布拿到的那份 `nodes`）：
 *   ① 与顶部计数「N 节点 / M 边」是同一个集合（回收站节点与类型过滤已经生效），
 *      键盘走不到画布上不存在的节点，也不会漏掉画布上存在的节点；
 *   ② 力导向布局的 `x` / `y` 在模拟过程中一直在变，按坐标排序会让"下一个"
 *      在用户连按时跳来跳去；数据顺序对同一份图谱是稳定的。
 *
 * ## 循环与起点
 *
 * 到末尾再按「下」回到第一个（反向同理）。**还没有当前节点**时：
 * 「下」从第一个开始、「上」从最后一个开始 —— 否则首次按上下会"没反应"。
 */
import { useCallback } from 'react';
import type { KeyboardEvent } from 'react';
import type { ForceGraphNode } from '../../components/graph/types';

interface UseGraphKeyboardNavOptions {
  /** 画布上的节点（顺序即键盘顺序） */
  nodes: ForceGraphNode[];
  /** 当前节点的 id（没有选中时为 null） */
  selectedNodeId: string | null;
  /** 选中一个节点（与点击节点同一条路径：详情面板 + 侧边栏） */
  onSelect: (node: ForceGraphNode) => void;
}

/** 下一个节点的下标（循环；没有当前节点时按方向取首/尾） */
export function nextNodeIndex(count: number, currentIndex: number, step: 1 | -1): number | null {
  if (count <= 0) return null;
  if (currentIndex < 0) return step === 1 ? 0 : count - 1;
  return (currentIndex + step + count) % count;
}

export function useGraphKeyboardNav({
  nodes,
  selectedNodeId,
  onSelect,
}: UseGraphKeyboardNavOptions) {
  const handleCanvasKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      // 只处理落在**画布区域自己**身上的按键：区域里还有缩放按钮等子元素，
      // 它们的按键（Enter/Space）不该被这里接管，方向键也不该在按钮上"偷偷换节点"。
      if (event.target !== event.currentTarget) return;
      if (nodes.length === 0) return;

      // 方向键在这块区域是"切换节点"，不是滚动页面 —— 必须阻止默认行为，
      // 否则画布容器会跟着滚，视觉上像是节点动了。
      event.preventDefault();

      const currentIndex = nodes.findIndex((node) => node.id === selectedNodeId);
      const index = nextNodeIndex(nodes.length, currentIndex, event.key === 'ArrowDown' ? 1 : -1);
      if (index == null) return;
      onSelect(nodes[index]);
    },
    [nodes, selectedNodeId, onSelect],
  );

  return { handleCanvasKeyDown };
}
