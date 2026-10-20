/**
 * @file 外壳与通用组件的双语测试（i18n **阶段 1** 的验收用例）
 *
 * ## 覆盖什么
 *
 * 阶段 1 把"每个页面都会看到的东西"接了 `t()`：侧边栏导航、汉堡菜单、
 * 404 页、渲染异常兜底、Toast 关闭按钮、确认框默认按钮、加载中、重试。
 *
 * 这些组件的**中文**行为由各自的既有测试守着（`Sidebar.test.tsx` 等，
 * 它们跑在 `zh-CN` 下 —— 见 `src/test/setup.ts`）。本文件补的是
 * **英文路径**：那些既有测试一条都不会因为切语言而覆盖到英文。
 *
 * ## 为什么用"整页不含中文字符"这种兜底断言
 *
 * 逐条断言只能覆盖"我记得的那几句"。有人后来往侧边栏加一句硬编码中文时，
 * 逐条断言不会红 —— 那条兜底会。这个手法在阶段 0 的认证页用例里已经用过，
 * 实测有效（当时正是它先报出来的）。
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LocaleProvider } from './index';
import { LOCALE_STORAGE_KEY } from './locales';
import ConfirmDialog from '../components/ConfirmDialog';
import ErrorDisplay from '../components/ErrorDisplay';
import LoadingSpinner from '../components/LoadingSpinner';
import Sidebar from '../components/Sidebar';
import NotFound from '../pages/NotFound';

// 侧边栏要用 useAuth（只为拿 logout）；本文件不测认证
const auth = vi.hoisted(() => ({ logout: vi.fn() }));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ logout: auth.logout }),
}));

/** 收集容器里所有含中日韩字符的文本节点（兜底断言用） */
function cjkTextNodes(container: HTMLElement): string[] {
  const cjk = /[\u4e00-\u9fff]/;
  const found: string[] = [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const text = (node.textContent ?? '').trim();
    // 「中文」「English」是语言切换器的按钮 —— 它们**刻意**不翻译
    // （语言名自指，见 messages.zh-CN.ts 的说明），所以从兜底里排除。
    if (text && cjk.test(text) && text !== '中文') found.push(text);
    node = walker.nextNode();
  }
  return found;
}

function renderSidebar() {
  return render(
    <LocaleProvider>
      <MemoryRouter initialEntries={['/']}>
        <Sidebar
          collapsed={false}
          onToggleCollapse={vi.fn()}
          mobileOpen={false}
          onMobileClose={vi.fn()}
        />
      </MemoryRouter>
    </LocaleProvider>,
  );
}

/** 把语言设成英文 —— Provider 的初始化函数读 localStorage */
function useEnglish() {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
}

beforeEach(() => {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
});

describe('侧边栏导航：中文（不能回归）', () => {
  it('全部导航目的地是中文', () => {
    renderSidebar();
    for (const label of ['仪表盘', '今日学习', '卡片复习', '笔记列表', '回收站', '知识卡片']) {
      expect(screen.getByRole('button', { name: label }), label).toBeInTheDocument();
    }
  });

  it('分组标题与可访问名是中文', () => {
    renderSidebar();
    expect(screen.getByText('学习')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收起侧边栏' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上传资料' })).toBeInTheDocument();
  });
});

describe('侧边栏导航：英文（阶段 1 验收）', () => {
  it('★ 全部导航目的地是英文', () => {
    useEnglish();
    renderSidebar();
    for (const label of ['Dashboard', 'Today', 'Card review', 'All notes', 'Trash', 'Cards']) {
      expect(screen.getByRole('button', { name: label }), label).toBeInTheDocument();
    }
  });

  it('★ 分组标题与可访问名是英文', () => {
    useEnglish();
    renderSidebar();
    expect(screen.getByText('Learning')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload material' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
  });

  it('★★ 整条侧边栏不含中文字符（兜住"漏了一条没走 t()"）', () => {
    useEnglish();
    const { container } = renderSidebar();
    const offenders = cjkTextNodes(container);
    expect(offenders, `英文界面下侧边栏仍有中文：${JSON.stringify(offenders)}`).toEqual([]);
  });

  it('★ 切换语言后导航**立刻**变英文（不是只改了 localStorage）', async () => {
    // 这条守的是"NAV_SECTIONS 存的是键名而不是文案"这个决定：
    // 若那份模块级常量里存的是中文字符串，它在模块加载时就固化了，
    // 切语言时导航**不会变** —— 而这条用例正是为了钉住那个坑。
    renderSidebar();
    expect(screen.getByRole('button', { name: '仪表盘' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'English' }));

    expect(screen.getByRole('button', { name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '仪表盘' })).not.toBeInTheDocument();
  });
});

describe('通用组件：中文默认值', () => {
  it('LoadingSpinner 默认文案是「加载中…」', () => {
    render(
      <LocaleProvider>
        <LoadingSpinner />
      </LocaleProvider>,
    );
    expect(screen.getByText('加载中…')).toBeInTheDocument();
  });

  it('ErrorDisplay 的重试按钮是中文', () => {
    render(
      <LocaleProvider>
        <ErrorDisplay message="出错了" onRetry={vi.fn()} />
      </LocaleProvider>,
    );
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('ConfirmDialog 的默认按钮是「取消」「确认」', () => {
    render(
      <LocaleProvider>
        <ConfirmDialog open title="确定吗" onConfirm={vi.fn()} onCancel={vi.fn()} />
      </LocaleProvider>,
    );
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认' })).toBeInTheDocument();
  });
});

describe('通用组件：英文默认值（阶段 1 验收）', () => {
  it('★ LoadingSpinner 默认文案是英文', () => {
    useEnglish();
    render(
      <LocaleProvider>
        <LoadingSpinner />
      </LocaleProvider>,
    );
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('★ `text` prop 显式传入时优先（组件不翻译调用方的文案）', () => {
    useEnglish();
    render(
      <LocaleProvider>
        {/* 调用方给的是自己 t() 过的成品文案 —— 组件必须原样渲染 */}
        <LoadingSpinner text="Generating questions…" />
      </LocaleProvider>,
    );
    expect(screen.getByText('Generating questions…')).toBeInTheDocument();
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
  });

  it('★ ErrorDisplay 的重试按钮是英文，但 message 原样显示', () => {
    useEnglish();
    render(
      <LocaleProvider>
        <ErrorDisplay message="boom" onRetry={vi.fn()} />
      </LocaleProvider>,
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    // `message` 由调用方给（通常是后端 detail），组件不碰它
    expect(screen.getByRole('alert')).toHaveTextContent('boom');
  });

  it('★ ConfirmDialog 的默认按钮是 Cancel / Confirm', () => {
    useEnglish();
    render(
      <LocaleProvider>
        <ConfirmDialog open title="Sure?" onConfirm={vi.fn()} onCancel={vi.fn()} />
      </LocaleProvider>,
    );
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
  });

  it('★ 调用方显式传的按钮文案优先于默认值', () => {
    useEnglish();
    render(
      <LocaleProvider>
        <ConfirmDialog
          open
          title="Sure?"
          confirmText="Move to trash"
          cancelText="Keep it"
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />
      </LocaleProvider>,
    );
    expect(screen.getByRole('button', { name: 'Move to trash' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Keep it' })).toBeInTheDocument();
  });
});

describe('404 页：双语', () => {
  it('中文', () => {
    render(
      <LocaleProvider>
        <MemoryRouter>
          <NotFound />
        </MemoryRouter>
      </LocaleProvider>,
    );
    expect(screen.getByRole('heading', { name: '404' })).toBeInTheDocument();
    expect(screen.getByText('页面不存在，可能是链接已失效。')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回首页' })).toBeInTheDocument();
  });

  it('★ 英文', () => {
    useEnglish();
    render(
      <LocaleProvider>
        <MemoryRouter>
          <NotFound />
        </MemoryRouter>
      </LocaleProvider>,
    );
    // `404` 两种语言相同 —— 它仍然是 `<h1>`（`page-has-heading-one` 靠它）
    expect(screen.getByRole('heading', { name: '404' })).toBeInTheDocument();
    expect(
      screen.getByText('This page does not exist. The link may have expired.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to home' })).toBeInTheDocument();
  });

  it('★ 「返回首页」在两种语言下都是**链接**而不是按钮', () => {
    // 这条守的是 `NotFound.tsx` 里那条"不要换成 <button>"的注释：
    // 读屏与 `a11y.spec.ts` 的 not-found 场景都按链接认它。
    useEnglish();
    render(
      <LocaleProvider>
        <MemoryRouter>
          <NotFound />
        </MemoryRouter>
      </LocaleProvider>,
    );
    const link = screen.getByRole('link', { name: 'Back to home' });
    expect(link).toHaveAttribute('href', '/');
    expect(screen.queryByRole('button', { name: 'Back to home' })).not.toBeInTheDocument();
  });
});
