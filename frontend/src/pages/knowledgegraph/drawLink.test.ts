/**
 * 图谱**边**绘制：中点关系类型标签（i18n 回归，2026-11-07）
 *
 * ## 为什么这条必须存在
 *
 * i18n 阶段 3 把 `RELATION_TYPE_LABELS` 的**值**从中文文案换成了 i18n 键
 * （`{ related: 'graphTypes.relationRelated', … }`，见 `components/graph/types.ts:10`），
 * 而画布这边的 `drawLink.ts` 仍然把它**当文案直接 `fillText`** ——
 * 于是 hover / 选中一条边时，画布上出现的是
 *
 *     graphTypes.relationRelated
 *
 * 这不是"翻译没生效"，是**键名泄漏到用户界面**：组件里的同类映射（`GraphSidebar`）
 * 都走了 `t(labelKey)`，只有 canvas 这条路径漏了（canvas 里没有 hook）。
 *
 * 判据只有一条：画到画布上的字符串**不能以 `graphTypes` 开头**，必须是真文案。
 */
import { describe, expect, it, vi } from 'vitest';

import type { ForceGraphLink, ForceGraphNode } from '../../components/graph/types';
import { lookupMessage } from '../../i18n/translate';
import { drawGraphLink } from './drawLink';

function makeCtx() {
  const ctx = {
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    setLineDash: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn(() => ({ width: 24 })),
    roundRect: vi.fn(),
    fillStyle: '' as string,
    strokeStyle: '' as string,
    lineWidth: 1,
    font: '',
    textAlign: '',
    textBaseline: '',
  };
  return ctx as unknown as CanvasRenderingContext2D & { fillText: ReturnType<typeof vi.fn> };
}

function makeNode(over: Partial<ForceGraphNode> = {}): ForceGraphNode {
  return {
    id: 'n-1',
    title: '节点',
    card_type: 'concept',
    note_trashed: false,
    relation_count: 0,
    x: 0,
    y: 0,
    ...over,
  } as ForceGraphNode;
}

function makeLink(over: Partial<ForceGraphLink> = {}): ForceGraphLink {
  return {
    id: 'l-1',
    source: makeNode({ id: 'a', x: 0, y: 0 }),
    target: makeNode({ id: 'b', x: 100, y: 0 }),
    relation_type: 'related',
    status: 'confirmed',
    ...over,
  } as ForceGraphLink;
}

/** 画到画布上的所有文字 */
function drawnTexts(ctx: ReturnType<typeof makeCtx>): string[] {
  return ctx.fillText.mock.calls.map((call) => String(call[0]));
}

/** 真实翻译（钉 zh-CN）—— 判据是"画上去的是不是真文案"，所以不用假 t */
const t = (key: Parameters<typeof lookupMessage>[0]) => lookupMessage(key, 'zh-CN');

describe('边中点标签：必须画翻译后的文案', () => {
  it('★★ 悬停一条「相关」边时画的是「相关」，不是 `graphTypes.relationRelated`', () => {
    const ctx = makeCtx();
    const link = makeLink();

    drawGraphLink(link, ctx, 1, {
      selectedLink: null,
      hoverLink: link,
      highlightedRelationType: null,
      t,
    });

    const texts = drawnTexts(ctx);
    expect(texts.length).toBeGreaterThan(0);
    expect(texts).toContain('相关');
    expect(texts.some((text) => text.startsWith('graphTypes'))).toBe(false);
  });

  it('★ 四种关系类型都不泄漏键名（逐个画一遍）', () => {
    const expected: Record<string, string> = {
      related: '相关',
      prerequisite: '前置',
      subsequent: '后续',
      contrast: '对比',
    };

    for (const [relationType, label] of Object.entries(expected)) {
      const ctx = makeCtx();
      const link = makeLink({ relation_type: relationType });
      drawGraphLink(link, ctx, 1, {
        selectedLink: link,
        hoverLink: null,
        highlightedRelationType: null,
        t,
      });
      expect(drawnTexts(ctx), relationType).toContain(label);
    }
  });

  it('未知关系类型仍然回退成类型名本身（不画空、也不画键名）', () => {
    const ctx = makeCtx();
    const link = makeLink({ relation_type: 'unknown_type' });

    drawGraphLink(link, ctx, 1, {
      selectedLink: null,
      hoverLink: link,
      highlightedRelationType: null,
      t,
    });

    expect(drawnTexts(ctx)).toContain('unknown_type');
  });
});
