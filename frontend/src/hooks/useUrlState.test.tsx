/**
 * @file 列表状态挂在 URL 上的两组 Hook（2026-11-10）
 *
 * ## 守的是什么
 *
 * 1. **读**：缺省参数 = 默认值（URL 上什么都没有时不该报错，也不该把默认值写回去）；
 * 2. **写**：默认值不落进 URL（否则地址栏与分享链接会被无意义参数污染）；
 * 3. **一次导航**：多个筛选字段同时改只引发**一次** `setSearchParams` ——
 *    连续调用会互相覆盖（`NotesList` 真切到过：搜索框打字了、列表却没变）。
 *
 * 探针组件同时记录"hook 的返回值"与"当前 location.search"，
 * 这样每次断言都能看到"屏幕上是什么"与"地址栏是什么"是否一致。
 */
import { act, render } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { useUrlFlag, useUrlNumber, useUrlPatch, useUrlState } from './useUrlState';

/** 在 MemoryRouter 里跑一个 hook，返回 { value, search, set } 三个读取器 */
function mountHook<T>(hook: () => T, initialUrl = '/notes') {
  const box: { value?: T; search: string } = { search: '' };

  function Probe() {
    box.search = useLocation().search;
    box.value = hook();
    return null;
  }

  render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <Probe />
    </MemoryRouter>,
  );

  return {
    value: () => box.value as T,
    search: () => box.search,
  };
}

describe('useUrlState / useUrlNumber / useUrlFlag', () => {
  it('缺省参数读成默认值', () => {
    const { value } = mountHook(() => useUrlState('q', ''));
    expect(value()[0]).toBe('');
  });

  it('从 URL 读到值（深链接 / 后退回来都走这里）', () => {
    const { value } = mountHook(() => useUrlState('q', ''), '/notes?q=%E7%94%B5%E6%B1%A0');
    expect(value()[0]).toBe('电池');
  });

  it('写入非默认值时进 URL；写回默认值时**删掉**该参数', () => {
    const { value, search } = mountHook(() => useUrlState('q', ''));
    act(() => value()[1]('电池'));
    expect(decodeURIComponent(search())).toContain('q=电池');
    act(() => value()[1](''));
    expect(search()).not.toContain('q=');
  });

  it('非法页码退回默认值（手改 URL 不该把页面弄崩）', () => {
    expect(mountHook(() => useUrlNumber('p', 1), '/notes?p=abc').value()[0]).toBe(1);
    expect(mountHook(() => useUrlNumber('p', 1), '/notes?p=0').value()[0]).toBe(1);
    expect(mountHook(() => useUrlNumber('p', 1), '/notes?p=3').value()[0]).toBe(3);
  });

  it('布尔参数用 1/0 表示，缺省取默认', () => {
    expect(mountHook(() => useUrlFlag('arch')).value()[0]).toBe(false);
    expect(mountHook(() => useUrlFlag('arch'), '/notes?arch=1').value()[0]).toBe(true);
  });
});

describe('useUrlPatch：多字段只导航一次', () => {
  it('★ 同时改关键词与页码：关键词留在 URL，页码回到默认（参数消失）', () => {
    const { value, search } = mountHook(() => useUrlPatch());

    // 这正是 NotesList 的用法：打字 → `patch({ q, p: '' })`
    act(() => value()({ q: '电池', p: '' }));

    expect(decodeURIComponent(search())).toContain('q=电池');
    expect(search()).not.toContain('p=');
  });

  it('值为空串表示"删掉这个参数"，没点名的键不受影响', () => {
    const { value, search } = mountHook(() => useUrlPatch(), '/notes?q=x&role=personal_note');
    act(() => value()({ q: '' }));
    expect(search()).not.toContain('q=');
    // 换关键词不该把角色筛选一起清掉
    expect(search()).toContain('role=personal_note');
  });
});
