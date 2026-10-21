/**
 * @file 画布数据装配的三道过滤（回收站 / 类型 / 孤立点折叠）
 *
 * ## 为什么这个文件现在才出现
 *
 * `buildForceGraphData` 是 overhaul-plan 5.5 从 `KnowledgeGraph.tsx` 的
 * `forceGraphData` useMemo **纯提取**出来的，当时它的行为由页面级用例
 * （`KnowledgeGraph.test.tsx` 的 53 条）间接覆盖，没有自己的单测。
 *
 * C3-③ 给它加了第三道过滤与一个新的返回值（`isolatedCount`），
 * 而这一道的**口径**（孤立点按"类型过滤之后"算）是它最容易写错的地方 ——
 * 顺序反了不会崩、不会报错，只会给出一个骗人的数字。
 * 所以补上这一层直接的单测。
 *
 * ## 口径为什么必须钉住
 *
 * 真库实测：**3469 节点 / 604 边 / 3028 孤立（87.3%）**（走查报告 §3.10）。
 * 类型过滤之后孤立点会**变多**（"本来有边、但边连的不是这个类型"的节点
 * 此刻也成了孤立）。用户在画布上看到的是过滤后的结果，所以计数必须与它一致。
 */
import { describe, expect, it } from 'vitest';
import type { GraphData, GraphNode, GraphEdge } from '../../api/client';
import { buildForceGraphData } from './buildForceGraphData';

function makeNode(over: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'n1',
    title: '节点',
    card_type: 'concept',
    note_id: 'note-1',
    note_title: '笔记',
    note_trashed: false,
    relation_count: 0,
    ...over,
  } as GraphNode;
}

function makeEdge(source: string, target: string, over: Partial<GraphEdge> = {}): GraphEdge {
  return {
    id: `${source}-${target}`,
    source,
    target,
    relation_type: 'related',
    status: 'confirmed',
    ...over,
  } as GraphEdge;
}

function makeGraph(nodes: GraphNode[], edges: GraphEdge[]): GraphData {
  return { nodes, edges } as GraphData;
}

/** a—b 一条边；c 与 d 完全孤立 */
const graphWithTwoIsolated = makeGraph(
  [
    makeNode({ id: 'a', card_type: 'concept' }),
    makeNode({ id: 'b', card_type: 'formula' }),
    makeNode({ id: 'c', card_type: 'concept' }),
    makeNode({ id: 'd', card_type: 'qa' }),
  ],
  [makeEdge('a', 'b')],
);

describe('buildForceGraphData：孤立点折叠（C3-③）', () => {
  it('默认不折叠 —— 行为与改造前逐字一致（4 个节点全在画布上）', () => {
    const { data, isolatedCount } = buildForceGraphData(graphWithTwoIsolated, null);

    expect(data.nodes.map((n) => n.id).sort()).toEqual(['a', 'b', 'c', 'd']);
    expect(data.links).toHaveLength(1);
    expect(isolatedCount).toBe(2);
  });

  it('★ hideIsolated=true 时只保留有边的节点，边不变', () => {
    const { data, isolatedCount } = buildForceGraphData(graphWithTwoIsolated, null, true);

    expect(data.nodes.map((n) => n.id).sort()).toEqual(['a', 'b']);
    expect(data.links).toHaveLength(1);
    // 计数是"当前筛选结果里有多少孤立点"，与是否折叠**无关** ——
    // 折叠只是把它们从画布上摘掉，不改变"有多少个"这个事实
    expect(isolatedCount).toBe(2);
  });

  it('★ 口径：孤立点按**类型过滤之后**算，不是按接口原始数据算', () => {
    // 只看 formula：a—b 这条边连的是 formula，所以 a 与 b 都留下；
    // c（concept）与 d（qa）在类型过滤里就被剔掉了 —— 它们**不算**孤立点。
    // 顺序写反（先摘孤立点、再按类型过滤）在这里得到同样的 2，
    // 但下面那条用例会把两种顺序分开。
    const { data, isolatedCount } = buildForceGraphData(graphWithTwoIsolated, 'formula');

    expect(data.nodes.map((n) => n.id).sort()).toEqual(['a', 'b']);
    expect(isolatedCount).toBe(0);
  });

  it('★ 类型过滤必须保留"被筛类型但一条匹配边都没有"的节点', () => {
    // 构造：y—z 一条边（都不是 concept），x 是 concept 且完全孤立。
    // 筛 concept 时，改造前的实现会给出**空画布** —— 用户的动作明明是"看 concept"，
    // 却一张都看不到（注释写的是"显示被过滤类型节点 + 与之关联的节点"，
    // 实现漏了前半句）。这条用例把这个修复钉住。
    const graph = makeGraph(
      [
        makeNode({ id: 'x', card_type: 'concept' }),
        makeNode({ id: 'y', card_type: 'qa' }),
        makeNode({ id: 'z', card_type: 'qa' }),
      ],
      [makeEdge('y', 'z')],
    );

    const { data, isolatedCount } = buildForceGraphData(graph, 'concept');
    expect(data.nodes.map((n) => n.id)).toEqual(['x']);
    // y—z 这条边两端都不是 concept ⇒ 被类型过滤剔除 ⇒ x 在筛选结果里是孤立的
    expect(data.links).toHaveLength(0);
    expect(isolatedCount).toBe(1);
  });

  it('★ 修复后的口径：被筛类型的节点无论有没有匹配边都留下，邻居仍按"至少一端匹配"补', () => {
    const graph = makeGraph(
      [
        makeNode({ id: 'p', card_type: 'concept' }),
        makeNode({ id: 'q', card_type: 'definition' }),
        makeNode({ id: 'r', card_type: 'concept' }), // 完全孤立
      ],
      [makeEdge('p', 'q')],
    );

    const unfiltered = buildForceGraphData(graph, null);
    const filtered = buildForceGraphData(graph, 'concept');

    expect(unfiltered.isolatedCount).toBe(1); // r
    expect(filtered.isolatedCount).toBe(1); // 仍是 r；p 有边（p—q 一端是 concept）
    // p（concept，有边）、q（邻居，被补进来）、r（concept，孤立但类型匹配）
    expect(filtered.data.nodes.map((n) => n.id).sort()).toEqual(['p', 'q', 'r']);
    // 折叠后只剩 p、q —— r 是唯一被摘掉的
    const collapsed = buildForceGraphData(graph, 'concept', true);
    expect(collapsed.data.nodes.map((n) => n.id).sort()).toEqual(['p', 'q']);
  });

  it('空图 / 缺字段不崩：null 与 `{}` 都给出空结果与 0 计数', () => {
    expect(buildForceGraphData(null, null)).toEqual({
      data: { nodes: [], links: [] },
      isolatedCount: 0,
    });
    expect(buildForceGraphData({} as GraphData, null, true)).toEqual({
      data: { nodes: [], links: [] },
      isolatedCount: 0,
    });
  });

  it('回收站节点先被剔除，它的边也不参与"有没有关联"的判定', () => {
    const graph = makeGraph(
      [
        makeNode({ id: 'a' }),
        makeNode({ id: 'b', note_trashed: true }), // 回收站里的节点
      ],
      [makeEdge('a', 'b')],
    );

    const { data, isolatedCount } = buildForceGraphData(graph, null, true);
    // b 被剔除 ⇒ 指向它的边也被剔除 ⇒ a 变成孤立 ⇒ 折叠后画布为空
    expect(data.nodes).toEqual([]);
    expect(isolatedCount).toBe(1);
  });
});
