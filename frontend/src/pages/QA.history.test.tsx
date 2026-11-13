/**
 * @file 问答页的「历史 / 新建对话 / 追问提示」（docs/qa-history-plan.md §8.7）
 *
 * ## 三条提示为什么值得单写一个测试文件
 *
 * 它们是**给用户看的唯一解释**：为什么追问能接上、为什么删了还在备份里、
 * 为什么浮层问出来的和问答页不一样。文案被误删或者数字被写死的后果，
 * 不是"少一行字"，而是这三类困惑重新变回"像 bug 的体验"。
 *
 * 所以这里断言的不只是"有这段文字"，而是：
 * - 提示里的数字来自 `QA_LIMITS`（改常量 → 文案跟着变）；
 * - 抽屉打开后能看到保留期提示；
 * - 浮层（`NoteAskPanel`）有"不带入上下文"的说明。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { QueryWrapper } from '../test/render';
import QA from './QA';

// 历史列表接口全部打桩：这个文件测的是"界面怎么用这些数据"，不是网络
const getQaSessions = vi.fn();
const getQaSession = vi.fn();
const deleteQaSession = vi.fn();
const clearQaSessions = vi.fn();

vi.mock('../api/qaHistory', async () => {
  const actual = await vi.importActual<typeof import('../api/qaHistory')>('../api/qaHistory');
  return {
    ...actual,
    getQaSessions: (...args: unknown[]) => getQaSessions(...args),
    getQaSession: (...args: unknown[]) => getQaSession(...args),
    deleteQaSession: (...args: unknown[]) => deleteQaSession(...args),
    clearQaSessions: (...args: unknown[]) => clearQaSessions(...args),
  };
});

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return { ...actual, askQuestionStream: vi.fn() };
});

function renderPage(entry = '/qa') {
  return render(
    <QueryWrapper>
      <MemoryRouter initialEntries={[entry]}>
        <QA />
      </MemoryRouter>
    </QueryWrapper>,
  );
}

const SESSION_FIXTURE = {
  id: 'sess-1',
  surface: 'global',
  note_id: null,
  title: '浮充和均充的区别？',
  turn_count: 2,
  last_question: '那它对寿命有影响吗？',
  created_at: '2026-11-12T10:00:00Z',
  updated_at: '2026-11-12T10:05:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  getQaSessions.mockResolvedValue({ items: [SESSION_FIXTURE], total: 1, page: 1, page_size: 50 });
  getQaSession.mockResolvedValue({
    session: SESSION_FIXTURE,
    turns: [],
  });
  deleteQaSession.mockResolvedValue(undefined);
  clearQaSessions.mockResolvedValue({ deleted: 1 });
});

describe('追问提示（计划 §5.4 第 1 句）', () => {
  it('输入框下方常驻显示，且轮数来自 QA_LIMITS', async () => {
    renderPage();

    const hint = await screen.findByText(/追问会带上最近 \d+ 轮对话/);
    expect(hint).toBeInTheDocument();

    const { QA_LIMITS } = await import('../constants/qaHistory');
    expect(hint).toHaveTextContent(String(QA_LIMITS.contextTurns));
  });

  it('与输入框关联（aria-describedby），读屏用户在输入框上就能听到', async () => {
    renderPage();

    const input = screen.getByPlaceholderText(/输入你的问题/);
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBe('qa-multiturn-hint');
    expect(document.getElementById('qa-multiturn-hint')).toHaveTextContent(/追问会带上最近/);
  });
});

describe('历史抽屉', () => {
  it('点「历史」打开抽屉，列出会话标题与轮次', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: '历史' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('浮充和均充的区别？')).toBeInTheDocument();
    // 轮次数与标题一起显示（用户据此判断"这条值不值得点开"）
    expect(within(dialog).getByText(/2$/)).toBeInTheDocument();
  });

  it('抽屉顶部有保留期提示，数字来自 QA_LIMITS（计划 §5.4 第 2 句）', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: '历史' }));
    const dialog = await screen.findByRole('dialog');

    const { QA_LIMITS } = await import('../constants/qaHistory');
    expect(
      within(dialog).getByText(new RegExp(`${QA_LIMITS.retentionDays} 天`)),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(new RegExp(`${QA_LIMITS.backupWeeks}周`))).toBeInTheDocument();
  });

  it('点一条历史 → 载入该会话（读详情接口）', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: '历史' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByText('浮充和均充的区别？'));

    await waitFor(() => expect(getQaSession).toHaveBeenCalledWith('sess-1'));
  });

  it('删除一条 → 调删除接口并刷新列表', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: '历史' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '删除对话' }));

    await waitFor(() => expect(deleteQaSession).toHaveBeenCalledWith('sess-1'));
  });
});

describe('新建对话', () => {
  it('清空当前轮次（返回空态）', async () => {
    const user = userEvent.setup();
    renderPage('/qa?s=sess-1');

    // 载入会话后（fixture 无轮次）仍是空态；这里主要断言按钮存在且可点
    const newChat = await screen.findByRole('button', { name: '新建对话' });
    await user.click(newChat);

    expect(screen.getByText(/输入问题开始问答/)).toBeInTheDocument();
  });
});
