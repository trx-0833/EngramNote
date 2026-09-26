/**
 * 图谱规模探针的桩数据：**真实结构 + 合成标题**
 *
 * ## 为什么需要它
 *
 * 真实库（`backend/data/db/engramnote.db`，2026-09-26 只读统计）的实际形状。
 *
 * ⚠️ **2026-09-26 这份数字变过一次，而且变了 5 倍。** 关系判定改完之后
 * （孤点优先的候选池 + 跨笔记下界 0.80，见
 * `docs/journal/relation-quality-2026-09-26.md` §12/§13），全量收敛后是：
 *
 *     knowledge_cards  1181   concept 838 / definition 216 / qa 82 / formula 45
 *     card_relations   1362   （原来是 260）
 *     有边的卡片        941   （原来是 120）
 *     度 1 / 度 2 / 度 >= 3   253 / 180 / **508**（原来度 >= 3 只有 65）
 *     最大度             23
 *     similarity_score  min 0.750 / 中位 0.814 / max 0.999（**全部 > 0.6**）
 *     title 长度        min 2 / median 9 / max 29
 *
 * **"度 >= 3 的卡片 65 → 508"这一条最要紧**：`drawNode.ts:56` 的**外发光环**
 * 只对枢纽节点画，而它是最贵的绘制路径。这条数字涨了 7.8 倍，
 * 所以探针必须按新数字重测 —— 否则量的是"上一个版本画不画得动"。
 *
 * 而 `GET /api/graph`（`backend/app/api/graph.py:57-71`）**没有上限**，
 * 且 `graph_service.get_graph_data` 取的是 `confirmed` **与** `suggested` 两种状态
 * —— 也就是说用户一点「生成建议」，这 1362 条边**立刻**全上屏。
 *
 * `similarity_score 全部 > 0.6` 这一条仍然成立：`GraphCanvas.tsx:288` 的
 * `linkDirectionalParticles` 据此给**每条边**返回 2 个粒子。
 * 不过 1181 > `LARGE_GRAPH_NODES`，大图路径下粒子已经是 0 ——
 * 这个数字现在的意义是"**没有**因为粒子而额外变慢"。
 *
 * ## 为什么标题是合成的
 *
 * 真实标题是用户的语料。`backend/tests/test_no_personal_data.py` 存在的理由
 * 正是"真实语料不得进入被跟踪文件"，所以这里只复制**长度分布**，
 * 文字一律用中性占位 —— 用等宽 CJK，`measureText` 的成本与真实标题同量级
 * （`drawNode.ts:163` 每个节点每帧都要量一次）。
 *
 * ## 确定性
 *
 * 固定种子的 mulberry32，**不用 `Math.random()`**：否则两次运行的帧率不可比，
 * "改前 / 改后"就成了一笔糊涂账。
 */

/** 真实规模。改这里的数就要同时改文件头的说明与断言。 */
export const REAL_SHAPE = {
  nodes: 1181,
  edges: 1362,
  /** `card_type` 的真实分布（顺序无关，后面会被确定性洗牌） */
  cardTypes: [
    { type: 'concept', count: 838 },
    { type: 'definition', count: 216 },
    { type: 'qa', count: 82 },
    { type: 'formula', count: 45 },
  ],
  /**
   * 度分布：**度 1 / 度 2 / 度 >= 3** 三段。
   *
   * 原来只分"枢纽 + 叶子"两段（非枢纽一律按度 1），而真实数据里有
   * **180 张度 2** 的卡片 —— 它们的节点半径（`types.ts:237-239` 按
   * `relation_count` 算）与叶子不同，混成一段会让"节点绘制成本"失真。
   */
  degree1Nodes: 253,
  degree2Nodes: 180,
  /** 度 >= 3 的"枢纽"（会触发 `drawNode.ts:56` 的外发光环） */
  hubNodes: 508,
  maxDegree: 23,
  titleMin: 2,
  titleMax: 29,
} as const;

export interface PerfGraphNode {
  id: string;
  title: string;
  card_type: string;
  note_id: string;
  note_trashed: boolean;
  relation_count: number;
}

export interface PerfGraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  status: string;
  similarity_score: number;
}

/** mulberry32：小、快、确定性够用的 PRNG */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffle<T>(items: readonly T[], rng: () => number): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * 度序列：`hubs` 个节点分摊 `totalDegree`，每个 >= 3 且 <= `maxDegree`。
 *
 * 为什么不用随机图模型：随机的度分布每次都不同，而探针要的是**可比**。
 * 这里用"从 3 起步、轮流加 4 直到分完"的确定性分配 —— 得到的是
 * "多数中等、少数很大"的形状，与真实数据（最大度 23、65 张 >= 3）一致。
 */
function hubDegrees(hubs: number, totalDegree: number, maxDegree: number): number[] {
  const degrees = new Array<number>(hubs).fill(3);
  let remaining = totalDegree - hubs * 3;
  if (remaining < 0) throw new Error(`度预算不足：${totalDegree} < ${hubs * 3}`);
  let i = 0;
  while (remaining > 0) {
    const idx = i % hubs;
    const add = Math.min(maxDegree - degrees[idx], remaining, 4);
    if (add > 0) {
      degrees[idx] += add;
      remaining -= add;
    }
    i += 1;
    if (i > hubs * 200) throw new Error('度序列分配失败（预算无法分完）');
  }
  return degrees;
}

/** 标题长度：多数短、少数长，中位数落在 9 附近（与真实分布对齐） */
function titleLength(rng: () => number): number {
  const r = rng();
  if (r < 0.5) return 2 + Math.floor(rng() * 8); // 2..9
  if (r < 0.85) return 10 + Math.floor(rng() * 10); // 10..19
  return 20 + Math.floor(rng() * 10); // 20..29
}

/** 中性占位标题：等宽 CJK，长度由调用方给定 */
const TITLE_FILLER = '知识卡片示例标题占位文本内容说明';

function syntheticTitle(length: number): string {
  let out = '';
  while (out.length < length) out += TITLE_FILLER;
  return out.slice(0, length);
}

export interface LargeGraph {
  nodes: PerfGraphNode[];
  edges: PerfGraphEdge[];
}

/**
 * 生成与真实规模一致的图谱桩（1181 节点 / 1362 边）。
 *
 * `relation_count` **由生成的边反推**（而不是另外编一份）——
 * 真实后端也是这么算的，而它决定节点半径（`types.ts:237-239`）
 * 与枢纽外发光环（`drawNode.ts:56`）。
 */
export function buildLargeGraph(): LargeGraph {
  const rng = mulberry32(0x5eed);
  const { nodes: nodeCount, edges: edgeCount, degree1Nodes, degree2Nodes, hubNodes, maxDegree } =
    REAL_SHAPE;
  const touchedNodes = degree1Nodes + degree2Nodes + hubNodes;

  // 1) 度序列：508 个枢纽 + 180 个度 2 + 253 个度 1，合计 2 × 1362 = 2724
  //
  //    枢纽分到的度预算 = 总和 − 度 2 段 − 度 1 段 —— 先把固定段扣掉，
  //    剩下的才轮到 `hubDegrees` 去分（它要求每个枢纽 >= 3）。
  const hubBudget = edgeCount * 2 - degree2Nodes * 2 - degree1Nodes;
  const hubs = hubDegrees(hubNodes, hubBudget, maxDegree);
  const degrees = [
    ...hubs,
    ...new Array<number>(degree2Nodes).fill(2),
    ...new Array<number>(degree1Nodes).fill(1),
  ];

  // 2) 桩配对成边（确定性洗牌；自环就地与后面一条换掉）
  const stubs: number[] = [];
  degrees.forEach((degree, index) => {
    for (let k = 0; k < degree; k += 1) stubs.push(index);
  });
  const pool = shuffle(stubs, rng);
  const pairs: Array<[number, number]> = [];
  for (let i = 0; i + 1 < pool.length; i += 2) {
    let a = pool[i];
    const b = pool[i + 1];
    if (a === b) {
      // 与下一条桩互换，消掉自环（力导向图里的自环既不真实也会画出怪线）
      const swapAt = i + 2;
      if (swapAt < pool.length) {
        const tmp = pool[swapAt];
        pool[swapAt] = a;
        a = tmp;
      }
    }
    pairs.push([a, b]);
  }

  // 3) relation_count 由边反推
  const degreeOf = new Array<number>(touchedNodes).fill(0);
  pairs.forEach(([a, b]) => {
    degreeOf[a] += 1;
    degreeOf[b] += 1;
  });

  // 4) 类型分布：先按真实计数铺开，再确定性洗牌（避免"枢纽清一色 concept"）
  const typePool: string[] = [];
  REAL_SHAPE.cardTypes.forEach(({ type, count }) => {
    for (let i = 0; i < count; i += 1) typePool.push(type);
  });
  const shuffledTypes = shuffle(typePool, rng);

  const nodes: PerfGraphNode[] = Array.from({ length: nodeCount }, (_, index) => ({
    id: `c-${index}`,
    title: syntheticTitle(titleLength(rng)),
    card_type: shuffledTypes[index] ?? 'concept',
    note_id: `note-${index % 20}`,
    note_trashed: false,
    relation_count: index < touchedNodes ? degreeOf[index] : 0,
  }));

  // 5) 边：全部 related + confirmed，similarity_score 全部 > 0.6
  //    （真实数据 260/260 都 > 0.6 —— 这正是"每条边 2 个粒子"的来源）
  const edges: PerfGraphEdge[] = pairs.map(([a, b], index) => ({
    id: `e-${index}`,
    source: `c-${a}`,
    target: `c-${b}`,
    relation_type: 'related',
    status: 'confirmed',
    similarity_score: 0.82,
  }));

  return { nodes, edges };
}
