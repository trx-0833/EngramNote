/**
 * @file 知识卡片列表的掌握度展示（visual-refactor-plan 批次 E3）
 *
 * 本批次把卡片上的**掌握度进度条**换成了**环形**（借鉴表 §C2 第 6 行：
 * 「环形 / 圆点，**不用星星**」）。这一页此前没有测试文件，这一批补上，
 * 盯住的是"换形状时**不许弄丢**"的三件事：
 *
 * 1. 数值仍然有一句**可见文字**（`掌握度 NN%`）—— 环是 `aria-hidden` 的装饰件，
 *    读屏用户靠这句话拿数据，所以这句话必须存在（a11y 场景
 *    `knowledge-cards` 也断言了 `getByText('掌握度')` 可见）；
 * 2. 色阶仍然**只有 `getMasteryColor` 一个来源**（与 `difficultyColors` 同源）；
 * 3. `> 0` 的渲染判据没变：从未复习过的卡片不显示"掌握度 0%"。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getKnowledgeCards, type KnowledgeCard } from '../api/client';
import { getMasteryColor } from '../utils/labels';
import KnowledgeCards from './KnowledgeCards';
import masteryStyles from '../components/MasteryRing.module.css';

vi.mock('../api/client', () => ({ getKnowledgeCards: vi.fn() }));
vi.mock('../api/knowledge', () => ({
  generateExtension: vi.fn(),
  generateExtensionQuestions: vi.fn(),
  markCard: vi.fn(),
}));
vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const mockedCards = vi.mocked(getKnowledgeCards);

function makeCard(over: Partial<KnowledgeCard> = {}): KnowledgeCard {
  return {
    id: 'card-1',
    note_id: 'note-1',
    note_title: '锂离子电池的浮充与均充',
    title: '浮充的定义',
    content: '蓄电池的一种运行方式，端电压保持恒定。',
    summary: '浮充 = 恒压运行',
    card_type: 'concept',
    card_category: 'regular',
    chapter_title: '第一章 蓄电池',
    mastery_level: 62,
    is_key_point: false,
    is_difficulty: false,
    source_text: null,
    source_note_ids: null,
    parent_card_id: null,
    metadata_: null,
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
    user_id: 'user-1',
    ...over,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <KnowledgeCards />
    </MemoryRouter>,
  );
}

/** 接口一次性返回整页（`page` / `page_size` 是响应契约的一部分，不能省） */
function mockCards(items: KnowledgeCard[], total = items.length) {
  mockedCards.mockResolvedValue({ items, page: 1, page_size: 100, total });
}

/** 同一个色值在 jsdom 里的规范写法（`#8f7020` 会被读成 `rgb(143, 112, 32)`） */
function normalizeColor(value: string): string {
  const probe = document.createElement('span');
  probe.style.color = value;
  return probe.style.color;
}

/** 卡片上那枚环的 svg（颜色写在行内 `color` 上，见 MasteryRing.tsx） */
function ringColor(node: Element): string {
  const svg = node.querySelector('svg') as SVGElement | null;
  expect(svg, '环里没有 svg').not.toBeNull();
  return normalizeColor(svg!.style.color);
}

describe('知识卡片列表：掌握度改成环形（批次 E3）', () => {
  beforeEach(() => {
    mockedCards.mockReset();
  });

  it('★ 卡片上是"一枚环 + 一句可见文字"，而不是进度条', async () => {
    mockCards([makeCard({ mastery_level: 62 })]);
    const { container } = renderPage();
    await screen.findByText('浮充的定义');

    // 数值有可见文字：环 aria-hidden 之后，读屏用户的信息来源就是这一句
    expect(screen.getByText('掌握度 62%')).toBeInTheDocument();

    const rings = container.querySelectorAll(`.${masteryStyles.masteryRing}`);
    expect(rings).toHaveLength(1);
    expect(rings[0].querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('★ 每张有掌握度的卡片各一枚环，颜色逐张取自 getMasteryColor（与"难度"同源）', async () => {
    mockCards([
      makeCard({ id: 'a', title: '卡A', mastery_level: 30 }),
      makeCard({ id: 'b', title: '卡B', mastery_level: 62 }),
      makeCard({ id: 'c', title: '卡C', mastery_level: 90 }),
    ]);
    const { container } = renderPage();
    await screen.findByText('卡A');

    const rings = [...container.querySelectorAll(`.${masteryStyles.masteryRing}`)];
    expect(rings).toHaveLength(3);
    expect(rings.map(ringColor)).toEqual(
      [30, 62, 90].map((level) => normalizeColor(getMasteryColor(level))),
    );
  });

  it('掌握度 0（从未复习）不显示环 —— 与改动前 `mastery_level > 0` 的判据逐字一致', async () => {
    mockCards([makeCard({ mastery_level: 0 })]);
    const { container } = renderPage();
    await screen.findByText('浮充的定义');

    expect(container.querySelectorAll(`.${masteryStyles.masteryRing}`)).toHaveLength(0);
    expect(screen.queryByText(/掌握度/)).toBeNull();
  });
});

/**
 * 服务端分页（走查报告 §4 的 🟠「声称 3469 张，只渲染 999 张，无分页无提示」）
 *
 * 改造前是 `getKnowledgeCards(1, 999, …)` —— 一页 999 张、没有分页控件、
 * 页尾也不说"还有多少没显示"，所以真库里 3469 张卡片有 2470 张**静默不可见**。
 *
 * 这一组用例把新的三件事钉住：
 * 1. 请求带的是 `PAGE_SIZE`（100）与当前页码，不是写死的 999；
 * 2. 超过一页时出现分页控件，且能翻页（翻页后请求页码跟着变）；
 * 3. 页尾如实说明"本页显示第几到第几张、共多少张"。
 */
describe('知识卡片列表：服务端分页', () => {
  beforeEach(() => {
    mockedCards.mockReset();
  });

  it('★ 请求用的是 PAGE_SIZE=100 与当前页码，不再写死 999', async () => {
    mockCards([makeCard()]);
    renderPage();
    await screen.findByText('浮充的定义');

    expect(mockedCards).toHaveBeenCalledWith(1, 100, undefined, undefined);
  });

  it('★ 超过一页时出现分页控件，翻页后请求第 2 页', async () => {
    // 第 1 页返回 5 张、总数 3469 → 35 页。
    // ⚠️ 这里刻意**不**造满 100 张卡：本用例要验的是分页控件与请求页码，
    // 与"这一页有几张卡"无关；造满 100 张会让本用例在整仓测试并发跑时
    // 超 5s 默认超时（实测：单独跑 2.0s、整仓跑 >5s）。
    const page1 = Array.from({ length: 5 }, (_, i) =>
      makeCard({ id: `c${i}`, title: `卡片${i}` }),
    );
    mockedCards.mockResolvedValueOnce({ items: page1, page: 1, page_size: 100, total: 3469 });
    mockedCards.mockResolvedValueOnce({
      items: [makeCard({ id: 'p2', title: '第二页的卡片' })],
      page: 2,
      page_size: 100,
      total: 3469,
    });

    renderPage();
    await screen.findByText('卡片0');

    // 控件出现，且页尾如实说明范围
    expect(screen.getByRole('navigation', { name: '知识卡片分页' })).toBeInTheDocument();
    expect(screen.getByText('第 1 / 35 页')).toBeInTheDocument();
    expect(screen.getByText('本页显示第 1–5 张，共 3469 张')).toBeInTheDocument();

    // 上一页在第 1 页时不可点
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();

    // 翻到第 2 页：请求页码跟着变，页尾数字也跟着变
    screen.getByRole('button', { name: '下一页' }).click();
    await screen.findByText('第二页的卡片');

    expect(mockedCards).toHaveBeenLastCalledWith(2, 100, undefined, undefined);
    expect(screen.getByText('第 2 / 35 页')).toBeInTheDocument();
    expect(screen.getByText('本页显示第 101–101 张，共 3469 张')).toBeInTheDocument();
  });

  it('只有一页时不显示分页控件 —— 那排按钮没有任何可做的事', async () => {
    mockCards([makeCard()], 1);
    renderPage();
    await screen.findByText('浮充的定义');

    expect(screen.queryByRole('navigation', { name: '知识卡片分页' })).toBeNull();
    expect(screen.queryByText(/本页显示第/)).toBeNull();
  });
});
