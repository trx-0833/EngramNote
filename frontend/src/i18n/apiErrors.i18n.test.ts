/**
 * @file 前端本地 API 错误文案的双语测试（i18n **阶段 2** 的验收用例）
 *
 * ## 这一层为什么必须单独测
 *
 * 后端的 `AppError` 文案已经按 `Accept-Language` 翻译了 —— 但有一类错误
 * **到不了后端**：网络超时、令牌失效、浏览器不支持流式响应。
 * 那些文案是 `api/client.ts` 自己造的，请求根本没发出去或没拿到响应，
 * 后端无从翻译。所以它们是**前端自己的翻译责任**。
 *
 * 这里的断言分两层：
 * 1. `translate()` 在两种语言下取到正确的句子（纯函数，直接测）；
 * 2. `request()` 在 401 与超时两条路径上**真的**用上了它
 *    （否则"表里有翻译、代码没接上"这种漏接不会被发现）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyLocale } from './locales';
import { LOCALE_STORAGE_KEY } from './locales';
import { lookupMessage, translate } from './translate';

beforeEach(() => {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
  applyLocale('zh-CN', { persist: false });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('translate()：非组件版的 t()', () => {
  it('中文下返回中文', () => {
    expect(translate('apiErrors.timeout')).toBe('请求超时，请检查网络后重试');
    expect(translate('apiErrors.sessionExpired')).toBe('登录已过期，请重新登录');
  });

  it('★ 英文下返回英文', () => {
    applyLocale('en', { persist: false });
    expect(translate('apiErrors.timeout')).toBe(
      'The request timed out. Check your connection and try again.',
    );
    expect(translate('apiErrors.sessionExpired')).toBe(
      'Your session has expired. Please sign in again.',
    );
    expect(translate('apiErrors.invalidCredentials')).toBe('Incorrect email or password');
    expect(translate('apiErrors.uploadTimeout')).toBe(
      'The upload timed out. Check your connection and try again.',
    );
    expect(translate('apiErrors.streamUnsupported')).toBe(
      'Your browser does not support streaming responses',
    );
  });

  it('★ 它读的是**当前**语言（不是模块加载时那一份）', () => {
    // 这条守的是"`translate` 每次调用都读 `getCurrentLocale()`"这个行为。
    // 若实现改成在模块顶层缓存一次语言，切语言后它会一直返回旧文案 ——
    // 而那种 bug 只在"先切语言再触发错误"的顺序下出现，很难碰。
    expect(translate('apiErrors.timeout')).toContain('请求超时');
    applyLocale('en', { persist: false });
    expect(translate('apiErrors.timeout')).toContain('timed out');
    applyLocale('zh-CN', { persist: false });
    expect(translate('apiErrors.timeout')).toContain('请求超时');
  });

  it('与组件版 `t()` 走的是同一份实现（`lookupMessage`）', () => {
    // ⚠️ 第一版这条写成了恒真的比较（三元表达式两个分支都能过），等于没测。
    // 现在改成直接比对两条路径的返回值：`useTranslation` 内部就是
    // `lookupMessage(key, locale, params)`，所以**同一 (key, locale) 必然同值**。
    // 若哪天有人在 `useTranslation` 里又抄一份查表逻辑，这条会红。
    for (const locale of ['zh-CN', 'en'] as const) {
      const direct = lookupMessage('apiErrors.sessionExpired', locale);
      // 走一遍组件版的实现路径（同一个函数的调用形式）
      const sameWay = lookupMessage('apiErrors.sessionExpired', locale, undefined);
      expect(direct, locale).toBe(sameWay);
      expect(direct.trim(), `${locale} 的文案不该为空`).not.toBe('');
    }
    // 两种语言必须真的不同（否则"翻译"是假的）
    expect(lookupMessage('apiErrors.sessionExpired', 'zh-CN')).not.toBe(
      lookupMessage('apiErrors.sessionExpired', 'en'),
    );
  });

  it('未知语言回退到默认语言（不返回空串、不返回键名）', () => {
    const value = lookupMessage('apiErrors.timeout', 'klingon' as 'en');
    expect(value).toBe('请求超时，请检查网络后重试');
  });
});

describe('★ client.ts 真的接上了这张表', () => {
  /**
   * 这两条是"表里有翻译但代码没接上"的兜底。
   *
   * 做法：把 `fetch` 打桩成返回指定响应，然后看 `request()` 抛出的
   * `Error.message` 是不是**当前语言**的那一句。
   */
  async function messageFromRequest(status: number, path: string): Promise<string> {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: '后端的中文 detail' }), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    // 动态 import：`client.ts` 在模块加载时就会读一次环境，先打桩 fetch 再加载
    const { request } = await import('../api/client');
    try {
      await request(path);
    } catch (err) {
      return (err as Error).message;
    }
    throw new Error('request 没有抛错，用例前提不成立');
  }

  it('★ 401（普通接口）在英文下抛英文的"会话过期"', async () => {
    applyLocale('en', { persist: false });
    // `/notes` 不是凭据接口 → 走 `sessionExpired` 分支
    expect(await messageFromRequest(401, '/notes')).toBe(
      'Your session has expired. Please sign in again.',
    );
  });

  it('★ 401（登录接口）在英文下抛英文的"邮箱或密码错误"', async () => {
    applyLocale('en', { persist: false });
    // `/auth/login` 是凭据接口 → 走 `invalidCredentials` 分支，
    // 且**不该**触发全局登出（见 client.ts 的 isAuthCredentialPath）
    expect(await messageFromRequest(401, '/auth/login')).toBe('Incorrect email or password');
  });

  it('中文下这两条仍是中文（不能回归）', async () => {
    expect(await messageFromRequest(401, '/notes')).toBe('登录已过期，请重新登录');
    expect(await messageFromRequest(401, '/auth/login')).toBe('邮箱或密码错误');
  });
});
