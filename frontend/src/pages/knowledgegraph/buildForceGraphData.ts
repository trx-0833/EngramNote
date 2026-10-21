/**
 * @file 图谱页的画布数据装配（回收站过滤 + 类型过滤 + 孤立点折叠）
 * @description 自 `pages/KnowledgeGraph.tsx` 的 `forceGraphData` useMemo 拆分
 * （overhaul-plan 5.5），**只搬不改**：过滤顺序、`visibleIds` 的构造、
 * 类型过滤下"保留被过滤类型节点 + 与之关联的节点"的语义、以及每项 `{...n}` / `{...e}`
 * 的浅拷贝（避免 force-graph 把坐标写回接口数据）均与拆分前逐字一致。
 *
 * 2026-10-21 新增第三道过滤（C3-③）：`hideIsolated` 为真时只保留**有关系**的节点。
 * 为什么需要它见 `buildForceGraphData` 的说明。
 */
import type { GraphData } from '../../api/client';
import type { ForceGraphViewData } from '../../components/graph/types';

/** `buildForceGraphData` 的结果：画布数据 + 被折叠掉的孤立点数量 */
export interface ForceGraphBuildResult {
  /** 传给力导向图的数据（已过回收站 / 类型 / 孤立点三道过滤） */
  data: ForceGraphViewData;
  /**
   * **在类型过滤之后**仍然一条边都没有的节点数
   *
   * ⚠️ 口径很重要：它是"相对当前筛选结果"的孤立点，不是接口原始数据里的孤立点。
   * 例如筛了「公式」之后，那些"本来有边、但边连的不是公式"的节点也会变成孤立 ——
   * 用户此刻看到的就是这个数，所以报这个数才不会与画布矛盾。
   */
  isolatedCount: number;
}

/** 将 GraphData 转为 ForceGraph2D 所需格式 */
export function buildForceGraphData(
  graphData: GraphData | null,
  filterCardType: string | null,
  hideIsolated = false,
): ForceGraphBuildResult {
  if (!graphData) return { data: { nodes: [], links: [] }, isolatedCount: 0 };

  // 本函数在组件早退之前执行：即便 graphData 缺 nodes/edges（后端只回一半、
  // 或命中旧结构缓存）也必须自身安全，所以两处都归一成数组再用。
  let nodes = graphData.nodes ?? [];
  let edges = graphData.edges ?? [];

  // 回收站过滤：所属笔记已进回收站的节点不渲染（关系记录后端保留，
  // 恢复后自动复原）；同时剔除指向回收站节点的边，避免 force-graph 生成幽灵节点
  const visibleIds = new Set(nodes.filter((n) => !n.note_trashed).map((n) => n.id));
  nodes = nodes.filter((n) => visibleIds.has(n.id));
  edges = edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));

  // 类型过滤
  if (filterCardType) {
    const filteredNodeIds = new Set(
      nodes.filter((n) => n.card_type === filterCardType).map((n) => n.id),
    );
    /**
     * 保留哪些节点：**被筛类型的节点本身** + 与它们相连的节点
     *
     * ⚠️ 2026-10-21 修（C3-③ 写用例时实测出来的）：
     * 改造前这里**只**收集"出现在匹配边上的节点"：
     *
     * ```ts
     * const connectedNodeIds = new Set<string>();
     * edges.forEach((e) => {
     *   if (filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target)) { … }
     * });
     * nodes = nodes.filter((n) => connectedNodeIds.has(n.id));
     * ```
     *
     * 于是**筛出来的类型一张都看不到**的情况真的会发生：
     * 筛「公式」而库里没有任何"至少一端是公式"的边时（46 张 formula 卡、
     * 边只有 604 条且全是 `related`），`connectedNodeIds` 是空集 ⇒ 画布全空，
     * 而用户的动作明明是"看公式"。上面的注释写的是"显示被过滤类型节点 +
     * 与之关联的节点"，实现却漏了前半句。
     *
     * 现在按注释的语义实现：**先收被筛类型的节点，再补它们的邻居**。
     * 邻居那条边仍然要求"至少一端是被筛类型"（下面 `edges` 的过滤不变），
     * 所以图的形状没有被放宽 —— 放宽的只是"孤立但类型匹配"的节点不再被误删。
     */
    const keepNodeIds = new Set<string>(filteredNodeIds);
    edges.forEach((e) => {
      if (filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target)) {
        keepNodeIds.add(e.source);
        keepNodeIds.add(e.target);
      }
    });
    nodes = nodes.filter((n) => keepNodeIds.has(n.id));
    edges = edges.filter((e) => filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target));
  }

  // ── 第三道：孤立点（C3-③，走查报告 §3.10）──
  //
  // ## 为什么需要
  //
  // 真库实测 **3469 节点 / 604 边 / 3028 孤立（87.3%）**。力导向把 3469 个节点
  // 铺成一个**实心圆盘**，节点文字重叠成灰色噪点、缩略图里只有一个点 ——
  // 走查报告把它判为"在真实规模下不可用"。
  //
  // ## 口径：孤立 = 在当前这批边里**一次都没出现**
  //
  // 放在**类型过滤之后**算，理由见 `isolatedCount` 的注释（与画布一致）。
  // 顺序不能反过来：先摘孤立点再按类型过滤，会把"只因类型过滤才变孤立"的节点
  // 算成"本来就没关系"，那个数会骗人。
  //
  // ## 为什么**不**默认打开
  //
  // 走查报告的建议是"默认只画连通分量"。但真库 87.3% 是孤立点 ⇒ 默认打开等于
  // 一进页面就**少画八成节点**，而界面上如果没有一条清晰的出路，用户会以为
  // 数据丢了（比"画得乱"更糟）。所以做成**显式开关**（默认关 = 行为与改造前一致），
  // 且关的时候在工具栏把"有 N 个未关联卡片"说出来 —— 让问题**可见**，
  // 而不是替用户决定。要不要改成默认开，是产品决策，登记在
  // `docs/agent-workstream-progress.md` 的待决项里。
  const linkedIds = new Set<string>();
  edges.forEach((e) => {
    linkedIds.add(e.source);
    linkedIds.add(e.target);
  });
  const isolatedCount = nodes.reduce((n, node) => (linkedIds.has(node.id) ? n : n + 1), 0);
  if (hideIsolated) {
    nodes = nodes.filter((n) => linkedIds.has(n.id));
  }

  return {
    data: {
      nodes: nodes.map((n) => ({ ...n })),
      links: edges.map((e) => ({
        ...e,
        source: e.source,
        target: e.target,
      })),
    },
    isolatedCount,
  };
}
