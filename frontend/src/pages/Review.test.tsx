/**
 * @file 答题复习页的交互测试（5.12：与卡片复习页统一的那些约定）
 *
 * 5.12 之前，答题复习与卡片复习各有一套"看着差不多"的实现，其中两处
 * 差异是**缺陷**而不是设计：
 *
 * 1. 答题页的回车处理无差别 `preventDefault()` —— 焦点在"查看原文语境"
 *    按钮上按回车，原文不展开、人却被带去下一题；焦点在四档自评上按回车，
 *    自评根本没提交。卡片页在附录 AA.7 已有守卫，答题页没有。
 * 2. 答题页的进度条公式是各页手抄的一份，没有测试盯着。
 *
 * 本文件把答题这一侧钉住，与 `CardReview.test.tsx` 一起构成"两条流程
 * 行为一致"的证据。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getDueQuizzes,
  getReviewStats,
  submitAnswer,
  type DueQuiz,
  type ReviewStats,
  type SubmitAnswerResponse,
} from '../api/client';
import { getKnowledgeCard } from '../api/qa';
import Review from './Review';

vi.mock('../api/client', () => ({
  getDueQuizzes: vi.fn(),
  submitAnswer: vi.fn(),
  getReviewStats: vi.fn(),
}));
vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// 原文语境组件按需请求卡片详情；测试里只关心"有没有请求"，不拉真网络
vi.mock('../api/qa', () => ({ getKnowledgeCard: vi.fn() }));

const mockedDue = vi.mocked(getDueQuizzes);
const mockedSubmit = vi.mocked(submitAnswer);
const mockedStats = vi.mocked(getReviewStats);
const mockedCard = vi.mocked(getKnowledgeCard);

function makeQuiz(over: Partial<DueQuiz> = {}): DueQuiz {
  return {
    id: 'q1',
    card_id: 'c1',
    note_id: 'n1',
    question_type: 'fill_blank',
    difficulty: 'medium',
    question: '浮充是什么？',
    options: null,
    next_review_at: null,
    review_count: 2,
    interval: 3,
    easiness_factor: 2.5,
    // 阶段 5.1 / S2：契约（`DueQuizResponse.repetition`，带默认值）里这是**必填**，
    // 而手写的 `DueQuiz` 根本没有这个字段 —— 也就是说这条夹具此前构造的是一个
    // "前端自认为完整、契约里缺一项"的形状。SM-2 的 repetition 与 review_count
    // 同源，这里给 2 保持与 review_count 一致。
    repetition: 2,
    ...over,
  };
}

function makeStats(over: Partial<ReviewStats> = {}): ReviewStats {
  return {
    due_count: 2,
    today_done: 1,
    today_correct: 1,
    today_accuracy: 100,
    total_reviews: 10,
    total_correct: 8,
    total_accuracy: 80,
    total_quizzes: 20,
    daily_limit: 10,
    ...over,
  };
}

function makeResult(over: Partial<SubmitAnswerResponse> = {}): SubmitAnswerResponse {
  return {
    quiz_id: 'q1',
    is_correct: true,
    quality: 4,
    correct_answer: '蓄电池的一种恒压运行方式',
    explanation: null,
    options: null,
    question_type: 'fill_blank',
    sm2: {
      interval: 6,
      repetition: 2,
      easiness_factor: 2.5,
      next_review_at: '2026-09-17T04:00:00+00:00',
      rating: 3,
      predicted_retention: 0.62,
    },
    self_rating: null,
    grading_method: 'fill_blank',
    needs_self_assessment: false,
    completing_placeholder: false,
    grading_reason: null,
    grading_detail: null,
    ...over,
  };
}

/** 简答题的占位提交：后端要求用户自评，此刻调度尚未推进 */
function makePending(over: Partial<SubmitAnswerResponse> = {}): SubmitAnswerResponse {
  return makeResult({
    is_correct: false,
    quality: 1,
    question_type: 'short_answer',
    grading_method: 'ungraded',
    needs_self_assessment: true,
    self_rating: null,
    ...over,
  });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Review />
    </MemoryRouter>,
  );
}

/** 等第一题挂出来 */
async function loaded() {
  await screen.findByText('浮充是什么？');
}

function fillWidth(container: HTMLElement): string {
  const fill = container.querySelector('.progress-bar-fill');
  if (!(fill instanceof HTMLElement)) throw new Error('没有渲染 .progress-bar-fill');
  return fill.style.width;
}

beforeEach(() => {
  mockedDue.mockReset();
  mockedSubmit.mockReset();
  mockedStats.mockReset();
  mockedCard.mockReset();
  mockedDue.mockResolvedValue({
    items: [makeQuiz(), makeQuiz({ id: 'q2', question: '均充是什么？' })],
    total: 2,
  });
  mockedStats.mockResolvedValue(makeStats());
});

describe('答题复习的进度显示（与卡片复习页共用同一组件）', () => {
  it('★ 进度条把"刚结账的这一张"算进去：提交后从 0% 涨到 50%', async () => {
    mockedSubmit.mockResolvedValue(makeResult());
    const { container } = renderPage();
    await loaded();

    expect(fillWidth(container)).toBe('0%');

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    await screen.findByText('回答正确!');
    expect(fillWidth(container)).toBe('50%');
  });

  it('两侧计数：题号进度在左、本次正确数与今日额度在右', async () => {
    renderPage();
    await loaded();

    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.getByText('0/0 正确 | 今日 1/10')).toBeInTheDocument();
  });
});

describe('每日限额的分流依据是 error_code，不是中文文案（阶段 0.11）', () => {
  /** 造一个带 code 的异常，模拟 api/client 抛出的 ApiError（结构一致） */
  function apiError(code: string, message: string): Error {
    const err = new Error(message) as Error & { code?: string };
    err.code = code;
    return err;
  }

  it('★ error_code=DAILY_REVIEW_LIMIT_REACHED 时进入"复习完成"，且刷新统计', async () => {
    // 文案里**故意不带**"每日上限"四个字：只要分支还在匹配中文，这条就会红
    // （这正是 F-19 要消灭的形态 —— 后端改文案不该影响前端分支）。
    mockedSubmit.mockRejectedValue(apiError('DAILY_REVIEW_LIMIT_REACHED', '今日额度已用完'));
    /**
     * ⚠️ `today_done` 必须是 **9**（未满）而不是 10。
     *
     * 阶段 4 之后，`today_done >= daily_limit` 会让提交按钮**提前置灰**，
     * 于是 `click` 根本不会触发请求，这条用例就永远走不到 429 那条分支
     * （实测踩到：改成 10 之后这条立刻红）。
     *
     * 9 是真实场景：页面加载时额度还剩 1，用户做这一道的同时
     * 额度被用尽（比如另一个标签页也在做题），后端于是返回 429 ——
     * 这正是"提前置灰"覆盖不到的竞态，也是这条用例该守的东西。
     */
    mockedStats.mockResolvedValue(makeStats({ today_done: 9, daily_limit: 10 }));

    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    expect(await screen.findByText('复习完成')).toBeInTheDocument();
    // 进入完成页前必须重新拉一次统计（否则"今日已完成"显示的是旧值）
    expect(mockedStats).toHaveBeenCalledTimes(2);
  });

  it('★ 额度已满时**不发请求**（阶段 4：提前置灰取代了"撞 429"）', async () => {
    mockedStats.mockResolvedValue(makeStats({ today_done: 10, daily_limit: 10 }));
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    const submit = screen.getByRole('button', { name: '提交答案' });
    expect(submit).toBeDisabled();
    // 按钮禁用，所以即使点也发不出去 —— 这才是"不撞墙"的实证
    await userEvent.click(submit);
    expect(mockedSubmit).not.toHaveBeenCalled();
  });

  it('其它错误码仍然报错，不会被误判成"今日完成"', async () => {
    mockedSubmit.mockRejectedValue(apiError('REVIEW_QUIZ_NOT_FOUND', '题目不存在'));

    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    // 没被误判成"额度用尽"：没有跳完成页，也没有多拉一次统计
    expect(screen.queryByText('复习完成')).not.toBeInTheDocument();
    expect(screen.getByText('浮充是什么？')).toBeInTheDocument();
    expect(mockedStats).toHaveBeenCalledTimes(1);
  });
});

describe('答题复习的回车约定（与卡片复习页一致）', () => {
  it('★ 焦点不在按钮上时，回车提交答案', async () => {
    mockedSubmit.mockResolvedValue(makeResult());
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行{Enter}');

    expect(mockedSubmit).toHaveBeenCalledTimes(1);
    expect(mockedSubmit).toHaveBeenCalledWith(
      'q1',
      '恒压运行',
      expect.any(Number),
      undefined,
      false,
    );
  });

  it('★ 焦点在"提交答案"按钮上时回车只提交一次（原生激活与容器接管不叠加）', async () => {
    mockedSubmit.mockResolvedValue(makeResult());
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    screen.getByRole('button', { name: '提交答案' }).focus();
    await userEvent.keyboard('{Enter}');

    expect(mockedSubmit).toHaveBeenCalledTimes(1);
  });

  it('★ 焦点在四档自评上时，回车要真的提交自评（旧实现把它 preventDefault 掉了）', async () => {
    mockedDue.mockResolvedValue({
      items: [makeQuiz({ question_type: 'short_answer' })],
      total: 1,
    });
    mockedSubmit.mockResolvedValueOnce(makePending()).mockResolvedValueOnce(
      makeResult({
        question_type: 'short_answer',
        grading_method: 'self_rating',
        self_rating: 4,
      }),
    );
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入你的回答...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));
    await screen.findByText('请对照答案，给自己的回忆程度打分');

    screen.getByText('想起来了').closest('button')?.focus();
    await userEvent.keyboard('{Enter}');

    // 第二次提交必须带上自评分 —— 也就是"回车真的激活了那个按钮"
    expect(mockedSubmit).toHaveBeenCalledTimes(2);
    // 第 5 个参数是语义判分开关。自评那一路（`useSelfRating` → hook 的 submit）
    // 不传它，所以是 `undefined` —— 后端在带自评时会直接跳过 LLM，传什么值都无所谓。
    // 这里刻意断言 `undefined` 而不是"有传"：它固定的是"自评路径不碰语义判分"，
    // 哪天有人在自评路径上也把开关传下去，这条会红。
    expect(mockedSubmit).toHaveBeenLastCalledWith(
      'q1',
      '恒压运行',
      expect.any(Number),
      4,
      undefined,
    );
  });

  it('★ 焦点在"查看原文语境"上时，回车展开原文而不是被带去下一题', async () => {
    mockedSubmit.mockResolvedValue(makeResult());
    mockedCard.mockResolvedValue({ source_text: '原文：浮充是恒压运行。' } as never);
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));
    await screen.findByText('回答正确!');

    screen.getByRole('button', { name: '查看原文语境' }).focus();
    await userEvent.keyboard('{Enter}');

    expect(mockedCard).toHaveBeenCalledWith('c1');
    expect(await screen.findByText('原文：浮充是恒压运行。')).toBeInTheDocument();
    // 关键：人被留在这一题上（旧实现会在这里推进到第 2 题）
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.queryByText('2 / 2')).not.toBeInTheDocument();
  });
});

/**
 * 想法 8：刷题页要能前后翻、能跳题
 *
 * ## 为什么组件测试之外还要页面级测试
 *
 * `QuizNavigator.test.tsx` 已经证明了"给什么状态就渲染什么"。但它证明不了
 * **页面把状态喂对了**：`navItems` 是页面从 `quizzes` 映射出来的，
 * 映射写错（比如把 `result` 漏掉、或用了错的字段）组件那边照样全绿。
 * 这类"接线错"只有渲染真页面才看得见。
 */
describe('刷题导航（想法 8）', () => {
  it('★ 题号条按题数渲染，且第一题时「上一题」是禁用的', async () => {
    renderPage();
    await loaded();

    const nav = screen.getByRole('group', { name: '题目导航' });
    expect(nav).toBeInTheDocument();
    // 夹具是 2 道题
    expect(screen.getByRole('button', { name: '跳到第 1 题，未作答' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跳到第 2 题，未作答' })).toBeInTheDocument();

    // 停在第 1 题：上一题不可点（而不是不渲染 —— 渲染了才知道边界在哪）
    expect(screen.getByRole('button', { name: '上一题' })).toBeDisabled();
  });

  it('★ 点题号能跳到那一题，且「上一题」随之可用', async () => {
    renderPage();
    await loaded();

    await userEvent.click(screen.getByRole('button', { name: '跳到第 2 题，未作答' }));

    // 真的换题了：进度文案与题目都变了
    expect(await screen.findByText('均充是什么？')).toBeInTheDocument();
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上一题' })).toBeEnabled();
  });

  it('★ 「上一题」能翻回已做过的题，并保留当时的作答与判分结果', async () => {
    mockedSubmit.mockResolvedValue(makeResult());
    renderPage();
    await loaded();

    // 第 1 题作答并提交
    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));
    await screen.findByText('回答正确!');

    // 走到第 2 题
    await userEvent.click(screen.getByRole('button', { name: '下一题' }));
    expect(await screen.findByText('均充是什么？')).toBeInTheDocument();

    // 翻回第 1 题 —— 这正是想法 8 的核心诉求："查看刚刚做过的题"
    await userEvent.click(screen.getByRole('button', { name: '上一题' }));

    expect(await screen.findByText('浮充是什么？')).toBeInTheDocument();
    // 作答内容与判分结果都还在（不是重置成空白）
    expect(screen.getByText('回答正确!')).toBeInTheDocument();
    // 题号条把它标成"已答对"，而不是"未作答"
    expect(screen.getByRole('button', { name: '跳到第 1 题，已答对' })).toBeInTheDocument();
  });
});

/**
 * 想法 8 阶段 3：进度持久化
 *
 * ## 为什么在这里断言 localStorage 而不是"真的刷新一次"
 *
 * jsdom 里没有"刷新"这个动作。持久化的**契约**就是"离开时把状态写进了
 * 那个键"，而"刷新后能读回来"由 `utils/quizSessionMemory.test.ts` 覆盖
 * （包括指纹不匹配、TTL 过期、坏数据那些分支）。两边合起来才是完整链路，
 * 各自断言自己那一半。
 */
describe('刷题进度持久化（想法 8 阶段 3）', () => {
  function stored(): Record<string, unknown> | null {
    const key = Object.keys(localStorage).find((k) => k.startsWith('engramnote:quiz:v1:review:'));
    if (!key) return null;
    try {
      return JSON.parse(localStorage.getItem(key) ?? 'null');
    } catch {
      return null;
    }
  }

  beforeEach(() => {
    // 清掉别的用例可能留下的记录，避免互相影响
    Object.keys(localStorage)
      .filter((k) => k.startsWith('engramnote:quiz:v1:'))
      .forEach((k) => localStorage.removeItem(k));
  });

  it('★ 提交后进度被写进 localStorage（刷新能接着做）', async () => {
    mockedSubmit.mockResolvedValue(makeResult());
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));
    await screen.findByText('回答正确!');

    const rec = stored();
    expect(rec, '提交后应当留下进度记录').not.toBeNull();
    const items = (rec as { items: Array<{ id: string; submitted: boolean; userAnswer: string }> })
      .items;
    expect(items).toHaveLength(2);
    expect(items[0].id).toBe('q1');
    expect(items[0].submitted).toBe(true);
    expect(items[0].userAnswer).toBe('恒压运行');
    // 本次统计也要带上，否则恢复后汇总页的数字接不上
    expect((rec as { sessionTotal: number }).sessionTotal).toBe(1);
  });

  it('★ 翻到第 2 题后，记录里的 currentIndex 跟着变', async () => {
    renderPage();
    await loaded();

    await userEvent.click(screen.getByRole('button', { name: '跳到第 2 题，未作答' }));
    expect(await screen.findByText('均充是什么？')).toBeInTheDocument();

    expect((stored() as { currentIndex: number } | null)?.currentIndex).toBe(1);
  });
});

/**
 * 想法 8 阶段 4：额度用尽时**提前**置灰
 *
 * 本页的加载量（`/review/due` 50 道）与每日可结算量（10 道）差 5 倍，
 * 所以"做到第 11 道才撞 429、页面上还剩 40 道能点的题"是必然发生的。
 */
describe('每日额度用尽时的表现（想法 8 阶段 4）', () => {
  it('★ 额度已满：提交按钮置灰 + 顶部说明，而不是等提交报 429', async () => {
    // today_done 已达 daily_limit
    mockedStats.mockResolvedValue(makeStats({ today_done: 10, daily_limit: 10 }));
    renderPage();
    await loaded();

    expect(screen.getByRole('button', { name: '提交答案' })).toBeDisabled();
    expect(screen.getByText('今日额度已用完（10 / 10）—— 仍可回看已答过的题')).toBeInTheDocument();
  });

  it('额度未满时不禁用、也不显示那句说明', async () => {
    mockedStats.mockResolvedValue(makeStats({ today_done: 3, daily_limit: 10 }));
    renderPage();
    await loaded();

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行');
    expect(screen.getByRole('button', { name: '提交答案' })).toBeEnabled();
    expect(
      screen.queryByText('今日额度已用完（3 / 10）—— 仍可回看已答过的题'),
    ).not.toBeInTheDocument();
  });

  it('★ 额度已满时仍能前后翻（回看不受额度约束）', async () => {
    mockedStats.mockResolvedValue(makeStats({ today_done: 10, daily_limit: 10 }));
    renderPage();
    await loaded();

    // 跳到第 2 题：允许
    await userEvent.click(screen.getByRole('button', { name: '跳到第 2 题，未作答' }));
    expect(await screen.findByText('均充是什么？')).toBeInTheDocument();
    // 再翻回来：也允许
    expect(screen.getByRole('button', { name: '上一题' })).toBeEnabled();
  });
});
