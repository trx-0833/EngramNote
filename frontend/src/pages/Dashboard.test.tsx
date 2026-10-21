/**
 * @file 仪表盘的「无活跃目标」分流（走查报告 §4 的 🟠 之一）
 *
 * ## 这条用例盯的是什么
 *
 * `GET /api/goals/daily-plan` 在**用户没有活跃目标**时返回 400
 * `GOAL_NONE_ACTIVE`。那是**正常业务状态**，不是加载失败 —— 新用户第一次
 * 打开仪表盘必然撞到它。所以它不能被计进 `failedCount`，否则每次打开都会
 * 看到一条「部分数据加载失败（1 项）」的假告警（走查报告 §3.3 记的就是这个：
 * 控制台红字 + 页内提示条）。
 *
 * ## 为什么这条用例值得单独存在
 *
 * 改造前这里的分流依据是 **对英文文案做子串匹配**：
 *
 * ```ts
 * const msg = e instanceof Error ? e.message : '';
 * if (!msg.includes('active goals')) failedCount++;
 * ```
 *
 * 后端把文案改一个字、或走 `core/error_messages.py` 的双语表把中文界面的
 * 文案换成中文，这个分支就**静默失效** —— 而失效的样子是"控制台红字又回来了"，
 * 不是任何形式的报错。这正是 overhaul-plan 的 F-19 记的形态。
 *
 * 现在按结构化的 `error_code` 分流。本文件同时钉住**两个方向**：
 *   - `GOAL_NONE_ACTIVE` → **不算**失败（不出现提示条）
 *   - 其它错误码（或没有码） → **算**失败（出现提示条）
 *
 * 只钉前一个方向是不够的：把 `errorCodeOf` 写成恒返回 `'GOAL_NONE_ACTIVE'`
 * 也能让第一条绿，但那会把**真的**加载失败也一起吞掉。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getDailyPlan,
  getDailyReport,
  getGoals,
  getNotes,
  getReviewStats,
  getUserReminderSettings,
  getWeakPoints,
  getWeeklyTrend,
  updateUserReminderSettings,
} from '../api/client';
import Dashboard from './Dashboard';

vi.mock('../api/client', () => ({
  getNotes: vi.fn(),
  getReviewStats: vi.fn(),
  getDailyReport: vi.fn(),
  getWeeklyTrend: vi.fn(),
  getWeakPoints: vi.fn(),
  getGoals: vi.fn(),
  getDailyPlan: vi.fn(),
  getUserReminderSettings: vi.fn(),
  updateUserReminderSettings: vi.fn(),
}));
vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// 横幅与邮件开关各自会发请求；本用例不关心它们，桩掉避免拉真网络
vi.mock('../components/ReminderBanner', () => ({ default: () => null }));

const mockedNotes = vi.mocked(getNotes);
const mockedStats = vi.mocked(getReviewStats);
const mockedReport = vi.mocked(getDailyReport);
const mockedTrend = vi.mocked(getWeeklyTrend);
const mockedWeak = vi.mocked(getWeakPoints);
const mockedGoals = vi.mocked(getGoals);
const mockedPlan = vi.mocked(getDailyPlan);
const mockedReminderSettings = vi.mocked(getUserReminderSettings);
const mockedUpdateReminder = vi.mocked(updateUserReminderSettings);

/** 把除 `getDailyPlan` 之外的六个接口都桩成"成功但空" */
function stubEverythingElseOk() {
  mockedNotes.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 5 });
  mockedStats.mockResolvedValue({
    due_count: 0,
    today_done: 0,
    today_correct: 0,
    today_accuracy: 0,
    total_reviews: 0,
    total_correct: 0,
    total_accuracy: 0,
    total_quizzes: 0,
    daily_limit: 10,
  });
  mockedReport.mockResolvedValue({
    date: '2026-10-21',
    new_mastered: 0,
    question_type_accuracy: [],
    today_accuracy: 0,
    total_review_time_ms: 0,
    total_reviews: 0,
    weak_point_count: 0,
  });
  mockedTrend.mockResolvedValue({ items: [], avg_accuracy: 0, total_reviews: 0 });
  mockedWeak.mockResolvedValue({ items: [], total: 0 });
  mockedGoals.mockResolvedValue({ goals: [], total: 0 });
  // 邮件提醒开关是页内一个独立组件，挂载时会读设置 —— 不桩会给 undefined
  mockedReminderSettings.mockResolvedValue({ email_reminder_enabled: false });
  mockedUpdateReminder.mockResolvedValue({ email_reminder_enabled: false });
}

/** 后端 400 的形状：`ApiError` 带 `code` 字段（结构读字段，不依赖类） */
function apiError(code: string | null, message: string): Error {
  const err = new Error(message) as Error & { code?: string | null };
  if (code !== null) err.code = code;
  return err;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

const PARTIAL_FAILURE = /部分数据加载失败/;

describe('仪表盘：无活跃目标不算加载失败（走查报告 §4）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stubEverythingElseOk();
  });

  it('★ getDailyPlan 抛 GOAL_NONE_ACTIVE → 不出现「部分数据加载失败」提示条', async () => {
    mockedPlan.mockRejectedValue(
      apiError('GOAL_NONE_ACTIVE', 'GOAL_NONE_ACTIVE: No active goals, please create a goal first'),
    );

    renderPage();
    // 等页面把数据渲染出来（欢迎语来自 PageHeader，不依赖任何接口）
    await screen.findByText('欢迎使用 EngramNote');

    expect(screen.queryByText(PARTIAL_FAILURE)).toBeNull();
  });

  it('★ 反向：其它错误码 → 仍然要报「部分数据加载失败」', async () => {
    // 这条是反空转：把 errorCodeOf 写成恒返回 'GOAL_NONE_ACTIVE' 也能让上一条绿，
    // 但会把真的失败一起吞掉 —— 那正是走查报告 §3.3 的另一半（F-24 的关切）。
    mockedPlan.mockRejectedValue(apiError('INTERNAL_ERROR', 'INTERNAL_ERROR: 服务器内部错误'));

    renderPage();
    await screen.findByText('欢迎使用 EngramNote');

    expect(await screen.findByText(PARTIAL_FAILURE)).toBeInTheDocument();
  });

  it('反向之二：连 error_code 都没有的异常，也按失败计', async () => {
    mockedPlan.mockRejectedValue(new Error('Failed to fetch'));

    renderPage();
    await screen.findByText('欢迎使用 EngramNote');

    expect(await screen.findByText(PARTIAL_FAILURE)).toBeInTheDocument();
  });
});
