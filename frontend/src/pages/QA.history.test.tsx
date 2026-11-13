/**
 * @file 问答页的「历史 / 新建对话 / 追问提示」+ 2026-11-13 独立审计的回归
 *
 * ## 为什么这几条要放在一个文件里
 *
 * 它们围绕同一件事：**用户能不能在这一页上弄清楚"我在看哪个对话、删掉的东西去哪了"**。
 * 追问提示回答"AI 怎么不记得"，保留期提示回答"删了怎么还在"，历史抽屉回答
 * "上次那个对话呢"；而审计的 M1–M4 说的正是"这套解释在接口/错误路径上失效了"
 * —— 请求没带 `surface` 于是抽屉里混进浮层会话、删除一点就执行、失败轮次永远
 * 停在"AI 正在思考..."、会话被后端删掉后页面留一行红字卡死。
 *
 * ## 断言的三条口径（都是踩过坑才这么写的）
 *
 * 1. **只断言用户能看到/听到的东西**：文案、可访问名、URL（`?s=`）、接口调用次数。
 *    不查类名、不读组件内部 state —— 它们会随实现走形，而用户看不到它们。
 * 2. **数字必须真的来自 `QA_LIMITS`**：这里把常量桩成 **9 轮 / 77 天 / 5 周**
 *    （与真实默认值 6 / 90 / 2 全不相同）。计划 §5.4 要求"调了配置文案跟着变"，
 *    只有让桩值与默认值不同，这条才能在测试里真的失败一次；否则"写死 6"和
 *    "读常量"两种实现都是绿的（那是假绿）。
 * 3. **两个 `role=dialog` 并存**（抽屉 + 确认框）时取 `findAllByRole` 的**最后一个**：
 *    确认框是后挂载的那个。`ConfirmDialog` 的按钮顺序是「取消」在前、「确认」在后
 *    （见该组件文件头：这是行为，不是排版），所以确认按钮取最后一个按钮。
 *
 * ## 每个 describe 对应一条审计结论
 *
 * | describe（文件里的原话） | 审计条目 |
 * |---|---|
 * | `追问提示（计划 §5.4 第 1 句）` | 计划 §5.4 第 1 句（含 `aria-describedby` 关联） |
 * | `审计 M1：历史列表请求` | **M1**（`surface: 'global'` + `page_size: 100`） |
 * | `历史抽屉（计划 §5.2 / §5.4 第 2 句）` | 计划 §5.2 / §5.4 第 2 句 + **m6**（截断提示） |
 * | `审计 M2：删除与会话清空都要二次确认` | **M2**（二次确认）、**m4**（失败 catch + toast）、**m5**（`{deleted}`） |
 * | `新建对话` | 计划 §5.5（清 `?s=` 与当前轮次） |
 * | `审计 M4：失败/中止的轮次说结论…` | **M4**（failed / aborted 说结论，含无 `error_code` 的兜底） |
 * | `审计 M3：追问时会话已经不在了` | **M3**（404 `QA_SESSION_NOT_FOUND` → 清 `?s=`） |
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { QueryWrapper } from '../test/render';
import { ApiError } from '../api/client';
import type {
  QASessionDetail,
  QASessionList,
  QASessionSummary,
  QATurnItem,
} from '../api/qaHistory';
import QA from './QA';

/**
 * ★ `QA_LIMITS` 的桩值刻意与真实默认值不同（9 / 77 / 5 vs 6 / 90 / 2）
 *
 * 理由见文件头第 2 条：只有桩值不同，"文案里的数字来自常量"才是一条**能失败**的断言。
 * 页面对它的用法是 `t('qa.multiturnHint', { turns: QA_LIMITS.contextTurns })` 与
 * 抽屉的 `retentionDays` / `backupWeeks` 两个 prop，所以桩在这里即可。
 */
const LIMITS = vi.hoisted(() => ({ contextTurns: 9, retentionDays: 77, backupWeeks: 5 }));
vi.mock('../constants/qaHistory', () => ({ QA_LIMITS: LIMITS }));

/**
 * toast 桩：页面上所有"发生了什么"的提示都从 `useToast()` 出去。
 *
 * 桩掉它而不是断言 DOM 里的提示条：审计 **m4 / m5 / M3** 要的是"这条提示有没有被发出、
 * 里面是不是那句话"，而不是"提示条的 DOM 长什么样"（那是 `Toast` 自己的用例）。
 */
const toast = vi.hoisted(() => ({
  show: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}));
vi.mock('../components/Toast', async () => {
  const actual = await vi.importActual<typeof import('../components/Toast')>('../components/Toast');
  return { ...actual, useToast: () => toast };
});

// 历史列表四个接口全部打桩：这个文件测的是"界面怎么用这些数据"，不是网络。
// `isSessionGoneError` 必须留**真实现**（M3 靠它按 `QA_SESSION_NOT_FOUND` 分流），
// 所以用 importActual 展开后只替换这四个。
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

// 流式问答：M3 的用例要让它抛 404，其余用例不该走到这里
const askQuestionStream = vi.fn();
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return { ...actual, askQuestionStream: (...args: unknown[]) => askQuestionStream(...args) };
});

// ── fixtures ───────────────────────────────────────────────────────────────

/** 有标题的会话 */
const SESSION_1: QASessionSummary = {
  id: 'sess-1',
  surface: 'global',
  note_id: null,
  title: '浮充和均充的区别？',
  turn_count: 2,
  last_question: '那它对寿命有影响吗？',
  created_at: '2026-11-12T10:00:00Z',
  updated_at: '2026-11-12T10:05:00Z',
};

/** `title` 为 null 的会话：后端允许（标题生成失败 / 只有一轮），界面不能留空白行 */
const SESSION_2: QASessionSummary = {
  id: 'sess-2',
  surface: 'global',
  note_id: null,
  title: null,
  turn_count: 1,
  last_question: '什么是浮充',
  created_at: '2026-11-11T09:00:00Z',
  updated_at: '2026-11-11T09:01:00Z',
};

/** 一条"成功"的轮次；失败/中止态由 `turn({ status })` 派生，避免三份 fixture 漂移 */
function turn(overrides: Partial<QATurnItem> = {}): QATurnItem {
  return {
    id: 'turn-1',
    question: '浮充和均充的区别？',
    answer: '浮充电压更低，用于长期保持满电；均充电压更高，用于均衡。',
    status: 'ok',
    context_turns: 0,
    created_at: '2026-11-12T10:05:00Z',
    ...overrides,
  };
}

function detail(turns: QATurnItem[]): QASessionDetail {
  return { session: SESSION_1, turns };
}

/** 列表响应；`total` 与 `items.length` 故意可以不等 —— 那正是 m6 的截断场景 */
function sessionList(items: QASessionSummary[], total = items.length): QASessionList {
  return { items, total, page: 1, page_size: 100 };
}

/** 断言用的答案片段：唯一、短、不会与提示文案撞 */
const ANSWER_SNIPPET = /浮充电压更低/;

beforeEach(() => {
  // `reset`（不是 `clear`）：M3 那条用例给 `askQuestionStream` 装了 rejection 实现，
  // 留着会漏到后面的用例里 —— 那种泄漏的表现是"单独跑绿、一起跑红"
  vi.resetAllMocks();
  getQaSessions.mockResolvedValue(sessionList([SESSION_1, SESSION_2]));
  getQaSession.mockResolvedValue(detail([turn()]));
  deleteQaSession.mockResolvedValue(undefined);
  clearQaSessions.mockResolvedValue({ deleted: 2 });
});

// ── 渲染助手 ───────────────────────────────────────────────────────────────

/** 用当前路由的 search 做断言（"用户看到的地址"），而不是去读 history */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-search">{location.search}</span>;
}

function currentSearch(): string {
  return screen.getByTestId('location-search').textContent ?? '';
}

function renderPage(entry = '/qa') {
  return render(
    <QueryWrapper>
      <MemoryRouter initialEntries={[entry]}>
        <LocationProbe />
        <QA />
      </MemoryRouter>
    </QueryWrapper>,
  );
}

/** 打开历史抽屉，返回它的那个 dialog */
async function openDrawer(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByRole('button', { name: '历史' }));
  return lastDialog();
}

/** 最后挂载的那个 dialog：两个并存时它就是确认框 */
async function lastDialog(): Promise<HTMLElement> {
  const dialogs = await screen.findAllByRole('dialog');
  return dialogs[dialogs.length - 1];
}

/** 确认框里的确认按钮：`ConfirmDialog` 先渲染「取消」、后渲染「确认」（见该组件文件头） */
function confirmButton(dialog: HTMLElement): HTMLElement {
  const buttons = within(dialog).getAllByRole('button');
  return buttons[buttons.length - 1];
}

/** 所有 toast 调用拼成一段文本，用于"某句话有没有被提示过"的断言 */
function toastLog(fn: (typeof toast)['info']): string {
  return fn.mock.calls.map((args) => args.join(' ')).join(' | ');
}

// ── 计划 §5.4 第 1 句：追问提示 ────────────────────────────────────────────

describe('追问提示（计划 §5.4 第 1 句）', () => {
  it('常驻显示、轮数来自 QA_LIMITS，且输入框用 aria-describedby 指向它', async () => {
    renderPage();

    const hint = await screen.findByText(/追问会带上最近 \d+ 轮对话/);
    // 桩值 9：文案若写死 6（当前默认值）或写死任何别的数，这一条红
    expect(hint).toHaveTextContent(String(LIMITS.contextTurns));

    // 读屏用户在输入框上就能听到这句：关联必须真的挂上，而不是只渲染在旁边
    const input = screen.getByRole('textbox', { name: '你的问题' });
    expect(input).toHaveAttribute('aria-describedby', 'qa-multiturn-hint');
    expect(document.getElementById('qa-multiturn-hint')).toBe(hint);
  });
});

// ── 审计 M1：历史列表请求 ──────────────────────────────────────────────────

describe('审计 M1：历史列表请求', () => {
  it('带 surface=global（只列问答页的会话）且 page_size 用后端上限 100', async () => {
    renderPage();

    await waitFor(() => expect(getQaSessions).toHaveBeenCalled());

    const [params] = getQaSessions.mock.calls[0] as [
      { surface?: string; page_size?: number } | undefined,
    ];
    // 不带 surface 时后端会把浮层（surface=note）的会话也混进来 —— 审计 M1 的原始现象
    expect(params?.surface).toBe('global');
    // 拉满一页才谈得上"还有更多"（m6 的截断提示也依赖这个上限）
    expect(params?.page_size).toBe(100);
  });
});

// ── 计划 §5.2 / §5.4 第 2 句 + 审计 m6：抽屉 ───────────────────────────────

describe('历史抽屉（计划 §5.2 / §5.4 第 2 句）', () => {
  it('列出标题；title 为 null 时显示「未命名对话」；保留期提示的数字来自 QA_LIMITS', async () => {
    const user = userEvent.setup();
    renderPage();

    const dialog = await openDrawer(user);

    expect(within(dialog).getByText('浮充和均充的区别？')).toBeInTheDocument();
    // 空白行看起来像坏了：没有标题的会话必须有兜底文案
    expect(within(dialog).getByText('未命名对话')).toBeInTheDocument();
    // 桩值 77 天 / 5 周：写死 90 / 2 在这里红
    expect(within(dialog).getByText(new RegExp(`${LIMITS.retentionDays} 天`))).toBeInTheDocument();
    expect(within(dialog).getByText(new RegExp(`${LIMITS.backupWeeks}周`))).toBeInTheDocument();
  });

  it('审计 m6：只拉了一页时说出「共 N 个对话，这里显示最近 M 个」', async () => {
    // 服务端 137 条、这一页只有 2 条 —— 用户必须知道"还有 135 条没显示"
    getQaSessions.mockResolvedValue(sessionList([SESSION_1, SESSION_2], 137));
    const user = userEvent.setup();
    renderPage();

    const dialog = await openDrawer(user);

    expect(within(dialog).getByText(/共 137 个对话/)).toBeInTheDocument();
    expect(within(dialog).getByText(/这里显示最近 2 个/)).toBeInTheDocument();
  });

  it('点一条历史 → 读详情、渲染答案、URL 带上 ?s=<id>', async () => {
    const user = userEvent.setup();
    renderPage();

    const dialog = await openDrawer(user);
    await user.click(within(dialog).getByText('浮充和均充的区别？'));

    await waitFor(() => expect(getQaSession).toHaveBeenCalledWith('sess-1'));
    // 载入的轮次要真的渲染出来，否则"点开了"只是 URL 变了
    expect(await screen.findByText(ANSWER_SNIPPET)).toBeInTheDocument();
    // 会话 id 必须在 URL 上（刷新/分享/返回都回到同一段对话）
    expect(currentSearch()).toContain('s=sess-1');
  });
});

// ── 审计 M2 / m4 / m5：删除与清空全部 ──────────────────────────────────────

describe('审计 M2：删除与会话清空都要二次确认', () => {
  it('点删除图标只打开确认框：确认前不删，确认后才删并让列表重新拉一次', async () => {
    const user = userEvent.setup();
    renderPage();

    const drawer = await openDrawer(user);
    const before = getQaSessions.mock.calls.length;

    await user.click(within(drawer).getAllByRole('button', { name: '删除对话' })[0]);

    // 审计 M2 的原始缺陷：这一步原来直接发 DELETE，点错没有第二次机会
    expect(deleteQaSession).not.toHaveBeenCalled();

    const confirm = await lastDialog();
    expect(within(confirm).getByText(/确定删除这个对话/)).toBeInTheDocument();
    expect(deleteQaSession).not.toHaveBeenCalled();

    await user.click(confirmButton(confirm));

    await waitFor(() => expect(deleteQaSession).toHaveBeenCalledTimes(1));
    expect(deleteQaSession.mock.calls[0][0]).toBe('sess-1');
    // 删掉之后列表必须重新拉（否则被删的那条还挂在抽屉里）
    await waitFor(() => expect(getQaSessions.mock.calls.length).toBeGreaterThan(before));
  });

  it('审计 m4：删除失败要 catch 成 toast.error，且不把 DELETE 再发一遍', async () => {
    deleteQaSession.mockRejectedValue(new ApiError('对话不存在或已被删除', 'QA_SESSION_NOT_FOUND'));
    const user = userEvent.setup();
    renderPage();

    const drawer = await openDrawer(user);
    await user.click(within(drawer).getAllByRole('button', { name: '删除对话' })[0]);
    await user.click(confirmButton(await lastDialog()));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // 后端那句话要说出来：原来这条 rejection 没人接，用户只看到列表没变
    expect(toastLog(toast.error)).toContain('对话不存在或已被删除');
    // 破坏性请求只发一次 —— `api/qaHistory` 的 m4 正是"为了复用错误本地化又发了一次"
    expect(deleteQaSession).toHaveBeenCalledTimes(1);
  });

  it('「清空全部」先确认，文案带服务端 total，确认后才清并提示条数', async () => {
    const user = userEvent.setup();
    renderPage();

    const drawer = await openDrawer(user);
    await user.click(within(drawer).getByRole('button', { name: '清空全部' }));

    expect(clearQaSessions).not.toHaveBeenCalled();

    const confirm = await lastDialog();
    // fixture 的服务端 total=2：文案要说"全部 2 个对话"
    expect(within(confirm).getByText(/全部 2 个对话/)).toBeInTheDocument();

    await user.click(confirmButton(confirm));

    await waitFor(() => expect(clearQaSessions).toHaveBeenCalledTimes(1));
    // 审计 m5：返回值 {deleted} 原来被丢弃，用户点完没有任何反馈
    expect(toastLog(toast.info)).toContain('已清空 2 个对话');
  });

  it('审计 m5：提示里的条数取自 {deleted}，不是列表条数、也不是服务端 total', async () => {
    // 这一页 2 条、服务端 5 条、真删掉 3 条 —— 三个数字都不同，取错就红
    getQaSessions.mockResolvedValue(sessionList([SESSION_1, SESSION_2], 5));
    clearQaSessions.mockResolvedValue({ deleted: 3 });
    const user = userEvent.setup();
    renderPage();

    const drawer = await openDrawer(user);
    await user.click(within(drawer).getByRole('button', { name: '清空全部' }));
    const confirm = await lastDialog();
    expect(within(confirm).getByText(/全部 5 个对话/)).toBeInTheDocument();

    await user.click(confirmButton(confirm));

    await waitFor(() => expect(toast.info).toHaveBeenCalled());
    expect(toastLog(toast.info)).toContain('已清空 3 个对话');
  });
});

// ── 计划 §5.5：新建对话 ───────────────────────────────────────────────────

describe('新建对话', () => {
  it('载入 ?s=sess-1 后点「新建对话」→ 答案消失、search 不含 s=、回到空态', async () => {
    const user = userEvent.setup();
    renderPage('/qa?s=sess-1');

    expect(await screen.findByText(ANSWER_SNIPPET)).toBeInTheDocument();
    expect(currentSearch()).toContain('s=sess-1');

    await user.click(screen.getByRole('button', { name: '新建对话' }));

    await waitFor(() => expect(screen.queryByText(ANSWER_SNIPPET)).toBeNull());
    // "新建对话"就是把 URL 上的会话清空：否则刷新还会回到刚才那段
    expect(currentSearch()).not.toContain('s=');
    expect(screen.getByText('输入问题开始问答')).toBeInTheDocument();
  });
});

// ── 审计 M4：失败 / 中止的轮次 ────────────────────────────────────────────

describe('审计 M4：失败/中止的轮次说结论，而不是永远「AI 正在思考...」', () => {
  it('status=failed 且带 error_code → 「这一轮没有生成成功（RuntimeError）」', async () => {
    getQaSession.mockResolvedValue(
      detail([turn({ status: 'failed', answer: null, error_code: 'RuntimeError' })]),
    );
    renderPage('/qa?s=sess-1');

    expect(await screen.findByText(/这一轮没有生成成功.*RuntimeError/)).toBeInTheDocument();
    // 原始缺陷：终态落到"思考中"分支，那句话永远不会自己结束
    expect(screen.queryByText(/AI 正在思考/)).toBeNull();
  });

  it('status=failed 但没有 error_code → 无参版本（不留一对空括号）', async () => {
    getQaSession.mockResolvedValue(
      detail([turn({ status: 'failed', answer: null, error_code: null })]),
    );
    renderPage('/qa?s=sess-1');

    // 精确匹配：多出 `（）` 或后缀都会红
    expect(await screen.findByText('这一轮没有生成成功')).toBeInTheDocument();
    expect(screen.queryByText(/这一轮没有生成成功（/)).toBeNull();
    expect(screen.queryByText(/AI 正在思考/)).toBeNull();
  });

  it('status=aborted → 「已停止生成」', async () => {
    getQaSession.mockResolvedValue(detail([turn({ status: 'aborted', answer: null })]));
    renderPage('/qa?s=sess-1');

    expect(await screen.findByText('已停止生成')).toBeInTheDocument();
    expect(screen.queryByText(/AI 正在思考/)).toBeNull();
  });
});

// ── 审计 M3：追问时会话已被删 ─────────────────────────────────────────────

describe('审计 M3：追问时会话已经不在了', () => {
  it('404 QA_SESSION_NOT_FOUND → 清 ?s=、旧轮次消失、回到空态并提示', async () => {
    askQuestionStream.mockRejectedValue(
      new ApiError('对话不存在或已被删除', 'QA_SESSION_NOT_FOUND'),
    );
    const user = userEvent.setup();
    renderPage('/qa?s=sess-1');

    expect(await screen.findByText(ANSWER_SNIPPET)).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: '你的问题' }), '那它对寿命有影响吗？');
    await user.click(screen.getByRole('button', { name: '提问' }));

    // 会话没了：URL 上的 ?s= 必须清掉（否则刷新一次还是同一个死会话）
    await waitFor(() => expect(currentSearch()).not.toContain('s='));
    expect(screen.queryByText(ANSWER_SNIPPET)).toBeNull();
    // 不是留一行红字：回到空态，用户可以立刻重新提问
    expect(screen.getByText('输入问题开始问答')).toBeInTheDocument();
    expect(toastLog(toast.info)).toContain('对话已被删除');
  });
});
