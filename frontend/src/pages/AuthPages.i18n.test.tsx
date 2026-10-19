/**
 * @file 认证页的双语测试（i18n 阶段 0 的**验收用例**）
 *
 * ## 为什么单独一个文件
 *
 * 阶段 0 的验收标准是"**切到英文后登录页全英文**"。而全仓 502 条测试断言
 * 在按中文文案查询，它们都跑在 `zh-CN` 下（`src/test/setup.ts` 钉住的）。
 * 所以英文路径需要**显式切语言**的用例来覆盖 —— 否则"英文界面"这件事
 * 一行测试都没有，而它是这一整轮的目标。
 *
 * ## 为什么不参数化那 502 条
 *
 * 把每条断言都改成"两种语言各跑一遍"会让测试量翻倍、耗时翻倍，
 * 而绝大多数断言与语言无关（按钮禁用、接口调用、状态流转）。
 * 真正需要双语覆盖的是**文案本身**，那是本文件与 `i18n.test.tsx` 的职责。
 */

import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../contexts/AuthContext';
import { LocaleProvider } from '../i18n';
import { LOCALE_STORAGE_KEY } from '../i18n/locales';
import Login from './Login';
import Register from './Register';

// 认证 API 打桩：本文件只关心**文案**，不关心接口
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    login: vi.fn(),
    register: vi.fn(),
  };
});

function renderLogin() {
  return render(
    <LocaleProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={['/login']}>
          <Login />
        </MemoryRouter>
      </AuthProvider>
    </LocaleProvider>,
  );
}

function renderRegister() {
  return render(
    <LocaleProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={['/register']}>
          <Register />
        </MemoryRouter>
      </AuthProvider>
    </LocaleProvider>,
  );
}

/** 把语言设成英文（写 localStorage —— Provider 的初始化函数读它） */
function useEnglish() {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
}

beforeEach(() => {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
});

describe('登录页：中文（默认，不能回归）', () => {
  it('★ 全部可见文案是中文', () => {
    renderLogin();

    expect(screen.getByRole('heading', { name: '登录 EngramNote' })).toBeInTheDocument();
    expect(screen.getByLabelText('邮箱')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '注册' })).toBeInTheDocument();
    expect(screen.getByText('没有账号？')).toBeInTheDocument();
  });

  it('占位符也是中文', () => {
    renderLogin();
    expect(screen.getByLabelText('密码')).toHaveAttribute('placeholder', '至少6位');
  });
});

describe('登录页：英文（阶段 0 的验收标准）', () => {
  it('★★ 全部可见文案是英文 —— 一个中文都不该剩', () => {
    useEnglish();
    renderLogin();

    expect(screen.getByRole('heading', { name: 'Sign in to EngramNote' })).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign up' })).toBeInTheDocument();
    expect(screen.getByText("Don't have an account?")).toBeInTheDocument();
  });

  it('占位符也是英文', () => {
    useEnglish();
    renderLogin();
    expect(screen.getByLabelText('Password')).toHaveAttribute(
      'placeholder',
      'At least 6 characters',
    );
  });

  it('★ 整页不含中文字符（兜住"漏了一条没走 t()"）', () => {
    // 这条是**兜底**：即使某天有人加了一句硬编码中文，这里也会红。
    // 逐条断言只能覆盖"我记得的那几句"，这条覆盖"整页"。
    useEnglish();
    const { container } = renderLogin();

    const cjk = /[\u4e00-\u9fff]/;
    const textNodes: string[] = [];
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const text = node.textContent ?? '';
      if (text.trim()) textNodes.push(text.trim());
      node = walker.nextNode();
    }
    const offenders = textNodes.filter((t) => cjk.test(t));
    expect(offenders, `英文界面下仍有中文文案：${JSON.stringify(offenders)}`).toEqual([]);
  });

  it('`<html lang>` 跟着切（读屏按它选发音）', () => {
    useEnglish();
    renderLogin();
    expect(document.documentElement.lang).toBe('en');
  });
});

describe('注册页：双语', () => {
  it('中文', () => {
    renderRegister();
    expect(screen.getByRole('heading', { name: '注册 EngramNote' })).toBeInTheDocument();
    expect(screen.getByLabelText('用户名')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '注册' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '登录' })).toBeInTheDocument();
  });

  it('★ 英文', () => {
    useEnglish();
    renderRegister();
    expect(
      screen.getByRole('heading', { name: 'Create your EngramNote account' }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign up' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('★ 本地校验的错误提示也走语言（不是硬编码中文）', async () => {
    useEnglish();
    const { container } = renderRegister();

    await userEvent.type(screen.getByLabelText('Username'), 'bad-name');
    // ⚠️ 必须**直接派发 submit**，不能 `click` 提交按钮。
    //
    // 真浏览器与 jsdom 都会先跑 HTML5 约束校验，而
    // `pattern="[a-zA-Z0-9]+"` 与 `handleSubmit` 里的正则**判据完全相同**
    // —— `bad-name` 带 `-`，原生校验先拦下，表单根本提交不出去。
    // 实测（真 Chromium）：`validity.patternMismatch=true`、点提交后
    // 页面上**没有**我们自己的 `role="alert"`。
    //
    // 也就是说这个 JS 分支是**防御性**的（见 `Register.tsx` 的注释）。
    // 要测它的文案，只能绕过原生校验直接派发事件。
    fireEvent.submit(container.querySelector('form') as HTMLFormElement);

    expect(
      await screen.findByText('Username may contain letters and digits only'),
    ).toBeInTheDocument();
  });

  it('中文下的同一条校验提示是中文', async () => {
    const { container } = renderRegister();
    await userEvent.type(screen.getByLabelText('用户名'), 'bad-name');
    fireEvent.submit(container.querySelector('form') as HTMLFormElement);

    expect(await screen.findByText('用户名只能包含英文字母和数字')).toBeInTheDocument();
  });
});
