/**
 * 问题集列表 · 搜索词 / 筛选 / 排序挂在 URL 上（2026-11-10）
 *
 * ## 这条守的是"回原文核对题目"这条工作流
 *
 * 每组的「查看笔记」会跳去 `/notes/{id}`；读完回来时本页重新挂载。
 * 改之前：搜索被清空、题型/难度回到「全部」、排序回到接口顺序 ——
 * 于是要在几千道题里重新筛一遍（真库 3358 道，见 `agent-workstream-plan.md` §2.3）。
 *
 * 现在这些状态都在 URL 上（`kw` / `type` / `diff` / `sort`），"返回到同一个 URL"
 * 就是回到同一个视图。判据因此是"带参数挂载页面 → 看到的是不是那个视图"，
 * 以及"点一下控件 → 参数有没有进 URL"。
 *
 * ⚠️ 排序参数只接受**已知的列与方向**：URL 是用户可编辑的，
 * `?sort=bogus:up` 必须退化成"不排序"，而不是让排序器拿着不存在的键去取值。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getQuestions, type QuizItem } from '../api/client';
import QuestionSets from './QuestionSets';
import { renderWithProviders } from '../test/render';
import pageStyles from './QuestionSets.module.css';

vi.mock('../api/client', () => ({ getQuestions: vi.fn() }));

const mockedQuestions = vi.mocked(getQuestions);

/** 与主测试文件同形的三条题（C 09-03 简答困难 / A 09-01 选择简单 / B 09-02 填空中等） */
function makeQuestion(over: Partial<QuizItem> & Pick<QuizItem, 'id'>): QuizItem {
  return {
    user_id: 'user-1',
    card_id: 'card-1',
    note_id: 'note-1',
    note_title: '锂离子电池的浮充与均充',
    question_type: 'short_answer',
    difficulty: 'hard',
    question: 'C 题：硫化的成因是什么？',
    answer: '极板上生成不可逆硫酸铅',
    options: null,
    explanation: '硫化会让容量下降。',
    metadata_: null,
    created_at: '2026-09-03T00:00:00+00:00',
    updated_at: '2026-09-03T00:00:00+00:00',
    ...over,
  };
}

const ITEMS: QuizItem[] = [
  makeQuestion({ id: 'q-1' }),
  makeQuestion({
    id: 'q-3',
    question: 'A 题：浮充与均充的主要区别是什么？',
    question_type: 'choice',
    difficulty: 'easy',
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
  }),
  makeQuestion({
    id: 'q-2',
    question: 'B 题：均充的作用是什么？',
    question_type: 'fill_blank',
    difficulty: 'medium',
    created_at: '2026-09-02T00:00:00+00:00',
    updated_at: '2026-09-02T00:00:00+00:00',
  }),
];

function shownQuestions(container: HTMLElement): string[] {
  return [...container.querySelectorAll(`.${pageStyles.questionText}`)].map(
    (el) => el.textContent ?? '',
  );
}

beforeEach(() => {
  mockedQuestions.mockReset();
  mockedQuestions.mockResolvedValue({
    items: ITEMS,
    page: 1,
    page_size: 1000,
    total: ITEMS.length,
  });
});

describe('问题集列表：筛选 / 排序 / 搜索由 URL 决定', () => {
  it('★ 带 ?diff=hard 进来：只剩那道困难题，chip 是按下态', async () => {
    const { container } = renderWithProviders(<QuestionSets />, {
      route: '/questions?diff=hard',
    });
    await screen.findByText('C 题：硫化的成因是什么？');

    expect(shownQuestions(container)).toEqual(['C 题：硫化的成因是什么？']);
    // chip 的 aria-pressed 读的是 URL 里的筛选值
    expect(screen.getByRole('button', { name: '困难' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '简单' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('★ 带 ?sort=created_at:desc 进来：按创建时间降序（C / B / A），列头 aria-sort 同步', async () => {
    const { container } = renderWithProviders(<QuestionSets />, {
      route: '/questions?sort=created_at:desc',
    });
    await screen.findByText('C 题：硫化的成因是什么？');

    await waitFor(() =>
      expect(shownQuestions(container)).toEqual([
        'C 题：硫化的成因是什么？',
        'B 题：均充的作用是什么？',
        'A 题：浮充与均充的主要区别是什么？',
      ]),
    );
    expect(screen.getByRole('columnheader', { name: /创建时间/ })).toHaveAttribute(
      'aria-sort',
      'descending',
    );
  });

  it('★ 非法排序（?sort=bogus:up）当作不排序，页面照常呈现接口顺序', async () => {
    const { container } = renderWithProviders(<QuestionSets />, {
      route: '/questions?sort=bogus:up',
    });
    await screen.findByText('C 题：硫化的成因是什么？');

    expect(shownQuestions(container)).toEqual([
      'C 题：硫化的成因是什么？',
      'A 题：浮充与均充的主要区别是什么？',
      'B 题：均充的作用是什么？',
    ]);
  });

  it('★ 带 ?kw=均充 进来：搜索框回填，取数带上关键词', async () => {
    renderWithProviders(<QuestionSets />, { route: '/questions?kw=%E5%9D%87%E5%85%85' });

    await waitFor(() =>
      expect(mockedQuestions).toHaveBeenCalledWith(1, 1000, undefined, '均充', expect.anything()),
    );
    expect(screen.getByRole('textbox')).toHaveValue('均充');
  });

  it('★ 点「困难」chip：筛选写进 URL（返回后不会丢）', async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<QuestionSets />, { route: '/questions' });
    await screen.findByText('C 题：硫化的成因是什么？');

    await user.click(screen.getByRole('button', { name: '困难' }));

    await waitFor(() => expect(shownQuestions(container)).toEqual(['C 题：硫化的成因是什么？']));
    expect(screen.getByRole('button', { name: '困难' })).toHaveAttribute('aria-pressed', 'true');
  });
});
