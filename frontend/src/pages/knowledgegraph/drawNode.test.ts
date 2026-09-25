/**
 * @file 图谱节点绘制的大图降级路径（性能批次 1）
 *
 * ## 这个文件锁的是什么
 *
 * 大图（节点数 > `LARGE_GRAPH_NODES`）时，`drawGraphNode` 会跳过两处最贵的装饰：
 * **逐像素模糊的节点阴影**与**每帧新建的玻璃径向渐变**。真 Chromium 实测
 * （`npm run perf:graph`）这两处合计约 14.6 ms/帧，在 1181 节点下占了大头。
 *
 * 但"跳过装饰"这件事有个反向风险：**降级降过头**。
 * 枢纽节点（度数高，本来就靠外发光环表达重要性）与当前选中/悬停的节点
 * 必须**仍然**有阴影和渐变 —— 否则大图会变成一片没有层次的色点，
 * 而"变快了"会掩盖"看不清了"。
 *
 * 所以这里四条判据各占一条用例：
 *   1. 小图（`lowDetail: false`）**行为与改动前逐字一致**；
 *   2. 大图 + 普通节点 → 走廉价路径（不建渐变、不设阴影）；
 *   3. 大图 + 枢纽 / 选中 → **仍然**走昂贵路径；
 *   4. 坐标守卫与"不再写 `__bckgDimensions`"两条防回潮断言。
 *
 * 用假上下文而不是真 canvas：jsdom 不实现 2D 上下文，而且这里要断言的是
 * **调用模式**（建没建渐变、设没设阴影），不是像素。
 */
import { describe, expect, it, vi } from 'vitest';

import { type ForceGraphNode, getNodeSize } from '../../components/graph/types';
import { type NodeDrawState, drawGraphNode } from './drawNode';

interface FakeCtx {
  [key: string]: unknown;
}

/**
 * 只记录调用的假上下文。
 *
 * `shadowBlur` 不是函数而是一个**属性**：绘制代码靠 `ctx.shadowBlur = ...` 设置它，
 * 所以替身必须让它可读可写 —— 断言"有没有设过阴影"就是读它。
 */
function makeCtx() {
  const gradient = { addColorStop: vi.fn() };
  const ctx = {
    beginPath: vi.fn(),
    closePath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    arc: vi.fn(),
    quadraticCurveTo: vi.fn(),
    fill: vi.fn(),
    stroke: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    setLineDash: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn(() => ({ width: 20 })),
    roundRect: vi.fn(),
    createRadialGradient: vi.fn(() => gradient),
    // 可写属性：默认 undefined，表示"这一帧没设过阴影"
    shadowBlur: undefined as number | undefined,
    shadowColor: undefined as string | undefined,
    shadowOffsetX: undefined as number | undefined,
    shadowOffsetY: undefined as number | undefined,
    fillStyle: undefined as string | object | undefined,
    strokeStyle: undefined as string | undefined,
    lineWidth: undefined as number | undefined,
    font: undefined as string | undefined,
    textAlign: undefined as string | undefined,
    textBaseline: undefined as string | undefined,
  };
  return ctx as unknown as CanvasRenderingContext2D & {
    createRadialGradient: ReturnType<typeof vi.fn>;
    arc: ReturnType<typeof vi.fn>;
    fill: ReturnType<typeof vi.fn>;
    beginPath: ReturnType<typeof vi.fn>;
    shadowBlur?: number;
  };
}

function makeNode(over: Partial<ForceGraphNode> = {}): ForceGraphNode {
  return {
    id: 'c-1',
    title: '示例卡片',
    card_type: 'concept',
    note_trashed: false,
    relation_count: 0,
    x: 10,
    y: 20,
    ...over,
  } as ForceGraphNode;
}

function makeState(over: Partial<NodeDrawState> = {}): NodeDrawState {
  return {
    selectedNode: null,
    hoverNode: null,
    createMode: false,
    createFirstNode: null,
    createSecondNode: null,
    searchResultIds: new Set<string>(),
    lowDetail: false,
    ...over,
  };
}

/** 一次绘制里"用没用昂贵装饰"的两个判据 */
function usedRichDetail(ctx: ReturnType<typeof makeCtx>) {
  return {
    gradient: ctx.createRadialGradient.mock.calls.length > 0,
    shadow: ctx.shadowBlur !== undefined,
  };
}

describe('小图：行为与改动前逐字一致（lowDetail = false）', () => {
  it('普通节点仍然建渐变、仍然设阴影', () => {
    const ctx = makeCtx();
    drawGraphNode(makeNode(), ctx, 1, makeState({ lowDetail: false }));

    const used = usedRichDetail(ctx);
    expect(used.gradient).toBe(true);
    expect(used.shadow).toBe(true);
    // 阴影参数是改动前那一组，逐字保留
    expect(ctx.shadowBlur).toBe(8);
  });
});

describe('大图降级（lowDetail = true）', () => {
  it('★ 普通节点：不建渐变、不设阴影 —— 这是省下来的那 14.6 ms/帧', () => {
    const ctx = makeCtx();
    drawGraphNode(makeNode({ relation_count: 0 }), ctx, 1, makeState({ lowDetail: true }));

    const used = usedRichDetail(ctx);
    expect(used.gradient).toBe(false);
    expect(used.shadow).toBe(false);
  });

  it('廉价光晕仍然画出来（不是"什么都不画"）', () => {
    const ctx = makeCtx();
    drawGraphNode(makeNode({ relation_count: 0 }), ctx, 1, makeState({ lowDetail: true }));

    // 光晕 + 主体 = 至少两次填充；一次都不填就说明降级降成了"看不见"
    expect(ctx.fill.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('★ 枢纽节点（度 >= 3）仍然走昂贵路径 —— 大图也要有层次', () => {
    const ctx = makeCtx();
    drawGraphNode(makeNode({ relation_count: 5 }), ctx, 1, makeState({ lowDetail: true }));

    const used = usedRichDetail(ctx);
    expect(used.gradient).toBe(true);
    expect(used.shadow).toBe(true);
  });

  it('★ 选中的普通节点仍然走昂贵路径 —— 交互反馈不能因为图大就消失', () => {
    const node = makeNode({ relation_count: 0 });
    const ctx = makeCtx();
    drawGraphNode(
      node,
      ctx,
      1,
      makeState({ lowDetail: true, selectedNode: node }),
    );

    const used = usedRichDetail(ctx);
    expect(used.gradient).toBe(true);
    expect(used.shadow).toBe(true);
  });

  it('悬停的普通节点同样保留昂贵路径', () => {
    const node = makeNode({ relation_count: 0 });
    const ctx = makeCtx();
    drawGraphNode(node, ctx, 1, makeState({ lowDetail: true, hoverNode: node }));

    expect(usedRichDetail(ctx).shadow).toBe(true);
  });
});

describe('搜索命中改用集合判定（性能批次 1）', () => {
  it('命中时画出搜索高亮环（半径 = 节点半径 + 5）', () => {
    const node = makeNode({ relation_count: 0 });
    const size = getNodeSize(node);
    const ctx = makeCtx();

    drawGraphNode(node, ctx, 1, makeState({ searchResultIds: new Set([node.id]) }));

    const radii = ctx.arc.mock.calls.map((call) => call[2] as number);
    expect(radii).toContain(size + 5);
  });

  it('未命中时不画搜索高亮环', () => {
    const node = makeNode({ relation_count: 0 });
    const size = getNodeSize(node);
    const ctx = makeCtx();

    drawGraphNode(node, ctx, 1, makeState({ searchResultIds: new Set(['别的节点']) }));

    const radii = ctx.arc.mock.calls.map((call) => call[2] as number);
    expect(radii).not.toContain(size + 5);
  });
});

describe('防回潮', () => {
  it('坐标无效时一个绘制调用都不发（被测试压住的白屏路径）', () => {
    const ctx = makeCtx();
    drawGraphNode(makeNode({ x: undefined }), ctx, 1, makeState());

    expect(ctx.beginPath.mock.calls.length).toBe(0);
    expect(ctx.createRadialGradient.mock.calls.length).toBe(0);
  });

  it('不再往节点上写 `__bckgDimensions`（全依赖树零消费者，曾是每帧 1181 次分配）', () => {
    const node = makeNode();
    const ctx = makeCtx();
    drawGraphNode(node, ctx, 1, makeState());

    expect((node as unknown as FakeCtx).__bckgDimensions).toBeUndefined();
  });
});
