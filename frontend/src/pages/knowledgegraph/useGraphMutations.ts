/**
 * @file 图谱页的写操作：确认/拒绝/批量/生成建议/删除关系/创建关系
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 每个处理器的 `try/catch/finally`、toast 文案、以及**重新拉取的方式**都逐字保留 ——
 * 单条确认/拒绝是「先 `getGraphData` 再 `getGraphStats`」（顺序 await），
 * 批量与创建/删除是 `Promise.all`，生成建议只在 `new_count > 0` 时重拉。
 * 这些顺序影响"确认后数字与图是否同步"，不能顺手统一。
 * BB.8 第 1 条的处置：8 处"重新拉取"抽成本文件内的 `refetchGraphAndStats(mode)` ——
 * 归一化与写回只写一次（BB.6 那种"漏了一处判据"从此不可能再发生），而两种 `mode`
 * 让**调用次数与发起顺序逐字不变**：顺序版仍是"图谱失败就不拉统计"，
 * 并发版仍是"两个请求同时发出"。合并的是重复的**写法**，不是可观测的**行为**。
 *
 * 失败路径只报 toast / 面板内错误，**不把已确认的建议从列表里抹掉**（否则用户以为成功了）。
 *
 * 拆分后补的一道护栏（BB.5）：**同一份数据的每条入口都要过同一道判据**。
 * `useGraphData.fetchData` 已在入口归一化，而本文件这两条入口原来直接写原始响应
 * （生成建议的 `suggestions`、每次"重新拉取"的 `stats`）—— 后端若在这两条路径上漂移
 * （数组被包成 `{items:[…]}`、或列表字段不是数组），建议面板 / 统计面板会在渲染中对
 * 非数组 `.map` → 整页被错误边界接走；而加载路径拿到同样的响应却不会。故统一过 `normalize` 的
 * 同一组判据（包装对象拆包后照常渲染，真正缺失/非数组才降级），而不是散落 `?.`。
 * `setGraphData` 不在此列：`useGraphData` 用 memo 统一过 `normalizeGraphData`，这里写回原始响应即可。
 */
import { useState, type Dispatch, type SetStateAction } from 'react';
import {
  batchConfirmRelations,
  batchRejectRelations,
  confirmRelation,
  createRelation,
  deleteRelation,
  getGraphData,
  getGraphStats,
  getSuggestions,
  rejectRelation,
  suggestRelations,
  type GraphData,
  type GraphStats,
  type SuggestedRelation,
} from '../../api/client';
import type { ForceGraphNode } from '../../components/graph/types';
import { useConfirm } from '../../components/ConfirmProvider';
import { useToast } from '../../components/Toast';
import { normalizeStats, normalizeSuggestions } from './normalize';

interface UseGraphMutationsOptions {
  setGraphData: Dispatch<SetStateAction<GraphData | null>>;
  setStats: Dispatch<SetStateAction<GraphStats | null>>;
  setSuggestions: Dispatch<SetStateAction<SuggestedRelation[]>>;
  /** 已勾选的建议 ID：批量确认/拒绝读它，提交后按它过滤列表 */
  selectedSuggestions: Set<string>;
  /** 清空勾选（与拆分前的 `setSelectedSuggestions(new Set())` 等价） */
  clearSelectedSuggestions: () => void;
  /** 清除选中的边：确认/拒绝/删除后详情面板不能残留旧状态 */
  clearSelectedLink: () => void;
  /** 创建关系表单：两个端点与关系类型 */
  createFirstNode: ForceGraphNode | null;
  createSecondNode: ForceGraphNode | null;
  createRelationType: string;
  /** 创建成功后退出创建模式（与「取消」按钮同一组状态重置） */
  exitCreateMode: () => void;
}

export function useGraphMutations({
  setGraphData,
  setStats,
  setSuggestions,
  selectedSuggestions,
  clearSelectedSuggestions,
  clearSelectedLink,
  createFirstNode,
  createSecondNode,
  createRelationType,
  exitCreateMode,
}: UseGraphMutationsOptions) {
  const toast = useToast();
  const confirm = useConfirm();

  /** 操作中状态 */
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  /** 手动生成相关建议中 */
  const [suggesting, setSuggesting] = useState(false);
  /** 手动生成相关建议的错误提示 */
  const [suggestError, setSuggestError] = useState('');
  /** 创建关系提交中 */
  const [creating, setCreating] = useState(false);

  /**
   * 写操作后"重新拉取图谱 + 统计"的唯一出口
   *
   * 这 8 处原先各写一遍（确认/拒绝是顺序 await，批量/删除/创建/生成是 Promise.all），
   * 重复的代价不是行数而是**判据会漂**：BB.6 里 7 处 `setStats(原始值)` 漏了 `normalizeStats`
   * 就是这么来的 —— 抽出来后归一化只可能写一次。
   *
   * `mode` 必须显式传、不能统一成一种：两种方式的**调用次数与失败行为可观测地不同** ——
   * `parallel` 两个请求同时发出（一个失败时另一个已经在路上），`sequential` 先拉图谱、
   * 写完再拉统计（图谱失败时统计**根本不会发**）。`KnowledgeGraph.test.tsx` 有两条用例
   * 专门钉这两种顺序，所以"顺手统一成 Promise.all"会立刻变红。
   */
  async function refetchGraphAndStats(mode: 'parallel' | 'sequential') {
    if (mode === 'parallel') {
      const [data, statsData] = await Promise.all([getGraphData(), getGraphStats()]);
      setGraphData(data);
      setStats(normalizeStats(statsData));
      return;
    }
    const data = await getGraphData();
    setGraphData(data);
    const statsData = await getGraphStats();
    setStats(normalizeStats(statsData));
  }

  /** 确认建议关系 */
  async function handleConfirm(relationId: string) {
    setActionLoading(relationId);
    try {
      await confirmRelation(relationId);
      setSuggestions((prev) => prev.filter((s) => s.id !== relationId));
      clearSelectedLink(); // 清除选中的待审边，避免详情面板残留旧状态
      await refetchGraphAndStats('sequential');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setActionLoading(null);
    }
  }

  /** 拒绝建议关系 */
  async function handleReject(relationId: string) {
    setActionLoading(relationId);
    try {
      await rejectRelation(relationId);
      setSuggestions((prev) => prev.filter((s) => s.id !== relationId));
      clearSelectedLink(); // 清除选中的待审边，避免详情面板残留旧状态
      await refetchGraphAndStats('sequential');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setActionLoading(null);
    }
  }

  /** 批量确认 */
  async function handleBatchConfirm() {
    if (selectedSuggestions.size === 0) return;
    setBatchLoading(true);
    try {
      await batchConfirmRelations(Array.from(selectedSuggestions));
      setSuggestions((prev) => prev.filter((s) => !selectedSuggestions.has(s.id)));
      clearSelectedSuggestions();
      await refetchGraphAndStats('parallel');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量操作失败');
    } finally {
      setBatchLoading(false);
    }
  }

  /** 批量拒绝 */
  async function handleBatchReject() {
    if (selectedSuggestions.size === 0) return;
    // 「拒绝建议」不加 danger：拒绝的是尚未采纳的建议，不是已存在的数据
    const ok = await confirm({
      title: `确定要拒绝 ${selectedSuggestions.size} 条建议关系吗？`,
      confirmText: '拒绝',
    });
    if (!ok) return;
    setBatchLoading(true);
    try {
      await batchRejectRelations(Array.from(selectedSuggestions));
      setSuggestions((prev) => prev.filter((s) => !selectedSuggestions.has(s.id)));
      clearSelectedSuggestions();
      await refetchGraphAndStats('parallel');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量操作失败');
    } finally {
      setBatchLoading(false);
    }
  }

  /** 手动生成相关建议（可能耗时数十秒，需显式触发，避免阻塞页面加载） */
  async function handleGenerateSuggestions() {
    setSuggesting(true);
    setSuggestError('');
    try {
      const result = await suggestRelations();
      const sug = await getSuggestions();
      // 与 fetchData 同一道判据：{items:[…]} 拆包后照常渲染（建议其实在，不能说成"暂无"），
      // 真正缺失/非数组才归一成空列表
      setSuggestions(normalizeSuggestions(sug));
      if (result.new_count > 0) {
        await refetchGraphAndStats('parallel');
      }
    } catch (err) {
      setSuggestError(err instanceof Error ? err.message : '生成建议失败');
    } finally {
      setSuggesting(false);
    }
  }

  /** 删除已确认的关系 */
  async function handleDeleteRelation(relationId: string) {
    const ok = await confirm({
      title: '确定要删除此关系吗？',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    setActionLoading(relationId);
    try {
      await deleteRelation(relationId);
      clearSelectedLink();
      await refetchGraphAndStats('parallel');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败');
    } finally {
      setActionLoading(null);
    }
  }

  /** 提交创建关系 */
  async function handleCreateRelation() {
    if (!createFirstNode || !createSecondNode) return;
    setCreating(true);
    try {
      await createRelation(createFirstNode.id, createSecondNode.id, createRelationType);
      exitCreateMode();
      await refetchGraphAndStats('parallel');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '创建失败');
    } finally {
      setCreating(false);
    }
  }

  return {
    actionLoading,
    batchLoading,
    suggesting,
    suggestError,
    creating,
    handleConfirm,
    handleReject,
    handleBatchConfirm,
    handleBatchReject,
    handleGenerateSuggestions,
    handleDeleteRelation,
    handleCreateRelation,
  };
}
