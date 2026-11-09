/**
 * ADHD Reader 首屏挂载：设置默认开启时，正文容器晚于 hook 挂载也必须接上
 *
 * 真实缺陷：`useAdhdReader` 首次 effect 跑时 `markdownRef.current` 还是 null
 * （笔记数据未到），effect 直接 return；React 不会因为 ref 里后来有 DOM 而
 * 重跑 effect，于是按钮是开启态、正文没有遮罩。这里模拟“ref 后挂载”。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useAdhdReader } from './useAdhdReader';
import { setDevicePref } from '../utils/devicePrefs';

describe('useAdhdReader 默认开启', () => {
  beforeEach(() => {
    setDevicePref('adhdDefault', true);
  });

  afterEach(() => {
    setDevicePref('adhdDefault', false);
  });

  it('ref 在首次渲染后才有 DOM：仍会给正文挂上 active 与遮罩', async () => {
    const ref: { current: HTMLElement | null } = { current: null };
    const { result } = renderHook(() => useAdhdReader(ref));

    expect(result.current.enabled).toBe(true);

    const root = document.createElement('article');
    const paragraph = document.createElement('p');
    paragraph.textContent = '一段用于验证遮罩的正文。';
    root.appendChild(paragraph);

    await act(async () => {
      ref.current = root;
      document.body.appendChild(root);
    });

    await waitFor(() => {
      expect(root.className).toContain('adhdReaderActive');
      expect(paragraph.className).toContain('adhdBlock');
      expect(paragraph.style.filter).toBe('none');
    });

    root.remove();
  });
});
