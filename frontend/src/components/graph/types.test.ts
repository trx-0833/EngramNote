/**
 * @file 图谱共享表（线型 / 令牌读取）的单测（批次 E4）
 *
 * ## 为什么单独一个文件
 *
 * 这两样东西都是**纯函数 + 常量**，但它们的正确性靠"页面级"的用例看不出来：
 *   - 线型表：四类关系两两不同，是"色觉障碍用户也能读"这条要求的**前提** ——
 *     谁哪天顺手把 `subsequent` 改成 `dashed`，页面上一切照旧，只有这里会红；
 *   - 令牌读取：canvas 拿不到 `var()`，所以它是"画布颜色与 `base.css` 同源"的
 *     唯一保证；读不到时的回退值（风险登记表 §9 第 5 条）也在这里钉住。
 *
 * `pages/KnowledgeGraph.test.tsx` 那边则负责**集成**：画布绘制回调真的用上了
 * 这些表（`ctx.setLineDash` / `ctx.strokeStyle` 的实际取值）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  FALLBACK_RELATION_LINE_STYLE,
  GRAPH_CANVAS_TOKEN_FALLBACKS,
  RELATION_LINE_DASHES,
  RELATION_TYPE_LINE_STYLES,
  getGraphCanvasTokens,
  getRelationDash,
  getRelationLineStyle,
  hasDirection,
  resetGraphCanvasTokensCache,
} from './types';

afterEach(() => {
  // `vi.stubGlobal` 装的替身是全局的，不清会漏给下一个用例
  vi.unstubAllGlobals();
  resetGraphCanvasTokensCache();
});

describe('关系类型 → 线型（批次 E4：线型取代颜色成为主通道）', () => {
  it('★ 设计点名的三类各就各位：实线 = 前提、虚线 = 相关、点线 = 对比', () => {
    expect(getRelationLineStyle('prerequisite')).toBe('solid');
    expect(getRelationLineStyle('related')).toBe('dashed');
    expect(getRelationLineStyle('contrast')).toBe('dotted');
  });

  it('★ 接口里的四类关系两两不同 —— 不允许两类共用一种线型', () => {
    const styles = Object.values(RELATION_TYPE_LINE_STYLES);
    expect(Object.keys(RELATION_TYPE_LINE_STYLES)).toHaveLength(4);
    expect(new Set(styles).size).toBe(styles.length);
    // 后续是"前提"的镜像写法（openapi.json 的 RelationType 描述），
    // 但它是**独立的一类**，所以不能退回实线/虚线，拿长虚线
    expect(getRelationLineStyle('subsequent')).toBe('longDash');
  });

  it('未知/缺失的关系类型走兜底（= 相关：最弱的一种主张）', () => {
    expect(getRelationLineStyle('unknown-type')).toBe(FALLBACK_RELATION_LINE_STYLE);
    expect(getRelationLineStyle('')).toBe(FALLBACK_RELATION_LINE_STYLE);
    expect(getRelationDash('unknown-type', 1)).toEqual(RELATION_LINE_DASHES.dashed);
  });

  it('虚线图案按 globalScale 缩放（缩放后图案与线宽同步，图案不会"糊成实线"）', () => {
    expect(getRelationDash('related', 1)).toEqual([5, 4]);
    expect(getRelationDash('related', 2)).toEqual([2.5, 2]);
    // 实线永远是空数组（`setLineDash([])` 才是真的实线）
    expect(getRelationDash('prerequisite', 1)).toEqual([]);
    expect(getRelationDash('prerequisite', 3)).toEqual([]);
  });

  it('箭头只给有向的两类（相关/对比是无向关系）', () => {
    expect(hasDirection('prerequisite')).toBe(true);
    expect(hasDirection('subsequent')).toBe(true);
    expect(hasDirection('related')).toBe(false);
    expect(hasDirection('contrast')).toBe(false);
    expect(hasDirection('unknown-type')).toBe(false);
  });
});

describe('canvas 的 --graph-* 令牌读取（批次 E4 接通）', () => {
  it('★ 读不到自定义属性时退回与 base.css 同源的字面量（不能画成透明/黑）', () => {
    // jsdom 不认识 `:root` 上的自定义属性 → 走回退分支；真实浏览器里 `:root`
    // 一定有这两枚令牌（`base.css` 的 `--graph-*` 段），所以这条是"环境缺陷不污染产品"
    expect(getGraphCanvasTokens()).toEqual(GRAPH_CANVAS_TOKEN_FALLBACKS);
    expect(GRAPH_CANVAS_TOKEN_FALLBACKS.paper).not.toBe('');
    expect(GRAPH_CANVAS_TOKEN_FALLBACKS.inkFaint).not.toBe('');
  });

  it('★ 读得到时用的就是令牌值、并且去掉首尾空白', () => {
    vi.stubGlobal('getComputedStyle', () => ({
      getPropertyValue: (name: string) =>
        name === '--graph-paper' ? 'rgb(9, 9, 9)' : '  rgb(1, 2, 3)  ',
    }));
    expect(getGraphCanvasTokens()).toEqual({ paper: 'rgb(9, 9, 9)', inkFaint: 'rgb(1, 2, 3)' });
  });

  it('只读一次并缓存（每帧 getComputedStyle 不划算，这是 A4 当初改不动的原因）', () => {
    const spy = vi.fn(() => ({ getPropertyValue: () => 'rgb(1, 2, 3)' }));
    vi.stubGlobal('getComputedStyle', spy);
    const first = getGraphCanvasTokens();
    const second = getGraphCanvasTokens();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
  });

  it('空串（jsdom / 令牌被删）不会被当成有效值', () => {
    vi.stubGlobal('getComputedStyle', () => ({ getPropertyValue: () => '' }));
    expect(getGraphCanvasTokens()).toEqual(GRAPH_CANVAS_TOKEN_FALLBACKS);
  });
});
