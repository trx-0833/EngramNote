/**
 * @file 图谱页归一化的单元测试：**容忍度不变，但漂移必须可见**（overhaul-plan AZ.7 / BB.8 第 1 部分）
 *
 * 页面级用例（`KnowledgeGraph.test.tsx`）只钉住了宽容解析的一半 —— "喂残缺数据不白屏"。
 * 另一半是 AZ.7 记下的代价：**形状不对时没有任何地方知道**，真实的后端契约破坏会静默扩散。
 * 这里逐条钉住三件事：
 *
 * 1. 可恢复的数组一定要恢复（拆包）—— 形状不对不等于数据没有；
 * 2. 漂移要**报出来**，带上"哪个接口、哪个字段、哪种形状"（提示文案就是按这两项写的）；
 * 3. **正常响应一条都不报**。一个总是响的提示比没有提示更糟：用户会学会无视它，
 *    真正出事那天也没人看。所以 (c) 类用例不是陪衬，与 (a)(b) 同等重要。
 *
 * 「包装载荷」在两类归一化里的含义不同，用例名里写清楚：
 * - 顶层数组契约（`suggestions`）：整个响应体是 `{items:[…]}` —— 拆包后照常渲染；
 * - 对象响应里的数组字段（`graph` / `stats` / `subgraph`）：**字段**被包成 `{items:[…]}`。
 *   字段级包装**不拆包**（容忍度与改动前逐字一致，只多一次上报）：把容忍度也一起扩大
 *   是另一次行为变更，得单独评估，不能搭在这次"让漂移可见"的车上一起来。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { GraphStats, SuggestedRelation } from '../../api/client';
import {
  resetContractDriftNotices,
  subscribeContractDrift,
  type ContractDriftEvent,
} from '../contractDrift';
import {
  normalizeGraphData,
  normalizeStats,
  normalizeSubgraph,
  normalizeSuggestions,
} from './normalize';

function makeSuggestion(over: Partial<SuggestedRelation> = {}): SuggestedRelation {
  return {
    id: 's-1',
    card_id_1: 'c-1',
    card_id_2: 'c-2',
    card_1_title: '浮充的定义',
    card_2_title: '均充的定义',
    similarity_score: 0.87,
    ...over,
  };
}

function makeStats(over: Partial<GraphStats> = {}): GraphStats {
  return {
    total_nodes: 4,
    total_edges: 2,
    confirmed_edges: 1,
    suggested_edges: 1,
    relation_type_distribution: [{ relation_type: 'related', count: 1 }],
    isolated_nodes: 1,
    ...over,
  };
}

// ── 漂移事件的收集：订阅是唯一的观察点（提示文案在 contractDrift 里由事件生成）──
let drifts: ContractDriftEvent[] = [];
let unsubscribe: (() => void) | null = null;

beforeEach(() => {
  // 去重键是模块级状态，会跨用例存活：不清就会让后面的用例"提示没出现"假红
  resetContractDriftNotices();
  drifts = [];
  unsubscribe = subscribeContractDrift((event) => drifts.push(event));
});

afterEach(() => {
  unsubscribe?.();
  unsubscribe = null;
});

describe('normalizeSuggestions（顶层数组契约）', () => {
  it('(a) 包装载荷：拆包后照常返回建议，并报出 wrapper 漂移', () => {
    const suggestion = makeSuggestion();

    const result = normalizeSuggestions({ items: [suggestion] });

    // 数据其实在：不能说成"暂无建议"，否则用户以为建议丢了
    expect(result).toEqual([suggestion]);
    expect(drifts).toHaveLength(1);
    expect(drifts[0]).toMatchObject({ kind: 'wrapper', source: 'GET /graph/suggestions' });
  });

  it('(b) 非数组载荷：归一成空列表，并报出 non-array 漂移', () => {
    expect(normalizeSuggestions({ total: 0 })).toEqual([]);
    expect(drifts).toHaveLength(1);
    expect(drifts[0]).toMatchObject({ kind: 'non-array', source: 'GET /graph/suggestions' });
  });

  it('(b) 空响应体（undefined）同样算非数组：不能把它当成"正常返回了空"', () => {
    expect(normalizeSuggestions(undefined)).toEqual([]);
    expect(drifts).toHaveLength(1);
    expect(drifts[0].kind).toBe('non-array');
  });

  it('(c) 正常数组：原样返回，一条漂移都不报', () => {
    const suggestion = makeSuggestion();

    const result = normalizeSuggestions([suggestion]);

    expect(result).toEqual([suggestion]);
    expect(drifts).toEqual([]);
  });

  it('(c) 正常的空数组（真的没有建议）同样不报 —— "空"不是漂移', () => {
    expect(normalizeSuggestions([])).toEqual([]);
    expect(drifts).toEqual([]);
  });

  it('去重：同一条建议被反复重拉（同一处漂移）只上报一次', () => {
    for (let i = 0; i < 5; i += 1) {
      normalizeSuggestions({ items: [] });
    }

    expect(drifts).toHaveLength(1);
  });

  it('去重不掩盖另一种形状：先包装后非数组，两种都要报出来', () => {
    normalizeSuggestions({ items: [] });
    normalizeSuggestions({ total: 0 });
    normalizeSuggestions({ items: [] });
    normalizeSuggestions({ total: 0 });

    expect(drifts.map((d) => d.kind)).toEqual(['wrapper', 'non-array']);
  });
});

describe('normalizeGraphData（对象响应里的数组字段）', () => {
  it('(a) 字段被包成 {items:[…]}：降级成空数组（容忍度未扩大）并报出该字段', () => {
    const result = normalizeGraphData({ nodes: { items: [] }, edges: { items: [] } } as never);

    expect(result).toEqual({ nodes: [], edges: [] });
    expect(drifts).toHaveLength(2);
    expect(drifts.map((d) => d.field)).toEqual(['nodes', 'edges']);
    expect(drifts.every((d) => d.source === 'GET /graph')).toBe(true);
  });

  it('(b) 字段缺失（后端只回 edges）时只报缺失的那个字段，边照常保留', () => {
    const result = normalizeGraphData({ edges: [] } as never);

    expect(result).toEqual({ edges: [], nodes: [] });
    expect(drifts).toHaveLength(1);
    expect(drifts[0]).toMatchObject({ kind: 'non-array', field: 'nodes' });
  });

  it('(b) 字段类型不对（nodes 是数字）同样归一成空数组并上报', () => {
    const result = normalizeGraphData({ nodes: 3, edges: [] } as never);

    expect(result?.nodes).toEqual([]);
    expect(drifts).toHaveLength(1);
    expect(drifts[0]).toMatchObject({ kind: 'non-array', field: 'nodes' });
  });

  it('(c) 正常响应：nodes/edges 原样返回，一条漂移都不报', () => {
    const result = normalizeGraphData({ nodes: [], edges: [] });

    expect(result).toEqual({ nodes: [], edges: [] });
    expect(drifts).toEqual([]);
  });

  it('(c) 还没加载（data 为空）不是漂移：那是"没有响应体"，不是形状不对', () => {
    expect(normalizeGraphData(null)).toBeNull();
    expect(normalizeGraphData(undefined)).toBeNull();
    expect(drifts).toEqual([]);
  });
});

describe('normalizeStats（对象响应里的数组字段）', () => {
  it('(a) 分布字段被包成 {items:[…]}：退化成空分布并报出字段名', () => {
    const result = normalizeStats(
      makeStats({ relation_type_distribution: { items: [] } as never }),
    );

    expect(result?.relation_type_distribution).toEqual([]);
    expect(drifts).toHaveLength(1);
    expect(drifts[0]).toMatchObject({
      kind: 'non-array',
      source: 'GET /graph/stats',
      field: 'relation_type_distribution',
    });
  });

  it('(b) 分布字段是对象、缺失、或是裸数字时都降级为空分布并上报', () => {
    normalizeStats(makeStats({ relation_type_distribution: { related: 1 } as never }));
    normalizeStats(makeStats({ relation_type_distribution: undefined as never }));
    normalizeStats(makeStats({ relation_type_distribution: 7 as never }));

    expect(drifts).toHaveLength(1); // 同一处漂移只报一次
    expect(drifts[0].kind).toBe('non-array');
  });

  it('(c) 正常响应：其余字段原样保留，一条漂移都不报', () => {
    const result = normalizeStats(makeStats());

    expect(result?.total_nodes).toBe(4);
    expect(result?.relation_type_distribution).toEqual([{ relation_type: 'related', count: 1 }]);
    expect(drifts).toEqual([]);
  });

  it('(c) 还没加载（data 为空）不报漂移', () => {
    expect(normalizeStats(null)).toBeNull();
    expect(drifts).toEqual([]);
  });
});

describe('normalizeSubgraph（对象响应里的数组字段）', () => {
  it('(a) 邻居/边被包成 {items:[…]}：退化成空列表并报出字段名（不改降级结果）', () => {
    const result = normalizeSubgraph({
      center_node: { id: 'c-1', title: '浮充的定义' },
      neighbor_nodes: { items: [] },
      edges: { items: [] },
    } as never);

    expect(result?.neighbor_nodes).toEqual([]);
    expect(result?.edges).toEqual([]);
    expect(drifts.map((d) => d.field).sort()).toEqual(['edges', 'neighbor_nodes']);
    expect(drifts.every((d) => d.source === 'GET /graph/node/{id}/subgraph')).toBe(true);
  });

  it('(b) 邻居数组缺失时保留中心节点、只报缺失的那个字段', () => {
    const result = normalizeSubgraph({
      center_node: { id: 'c-1', title: '浮充的定义' },
      edges: [],
    } as never);

    expect(result?.center_node.id).toBe('c-1');
    expect(result?.neighbor_nodes).toEqual([]);
    expect(drifts).toHaveLength(1);
    expect(drifts[0]).toMatchObject({ kind: 'non-array', field: 'neighbor_nodes' });
  });

  it('(c) 正常响应：中心节点与邻居原样返回，一条漂移都不报', () => {
    const node = { id: 'c-1', title: '浮充的定义' };

    const result = normalizeSubgraph({
      center_node: node,
      neighbor_nodes: [node],
      edges: [],
    } as never);

    expect(result?.neighbor_nodes).toHaveLength(1);
    expect(drifts).toEqual([]);
  });

  it('(c) 缺 center_node 走既有的"无子图"分支：面板不出现，但不是数组形状漂移', () => {
    // 这条分支的语义是"后端说这个节点没有子图"，与"字段形状不对"是两回事；
    // 保持原样（不报漂移）是本用例钉住的行为，扩到"包装对象"要单独评估。
    expect(normalizeSubgraph({ edges: [] } as never)).toBeNull();
    expect(drifts).toEqual([]);
  });
});
