/**
 * @file 阅读位置记忆的存储层测试（`utils/scrollMemory.ts`）
 *
 * ## 为什么这一层要单独测
 *
 * 它面对的是 `localStorage` —— 一个**用户可编辑、跨版本残留、可能根本不可用**
 * 的存储。这里每一条失败路径在真机上都会表现成「功能静默不生效」，
 * 而不是报错：脏 JSON 会让 `JSON.parse` 抛、过期条目会把用户丢到一个
 * 早已忘记的位置、无界增长会撑爆 5MB 配额。
 * 这些都不会让页面变红，所以只能在这里钉住。
 *
 * ## 用例顺序无关
 *
 * 每条用例自己清 `localStorage`（`beforeEach`），不依赖上一条留下的状态
 * —— 与 `DailyMaterials.test.tsx:16-22` 那种"顺序是有意的"不同，这里没有
 * 模块作用域的共享状态。
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  clearScrollMemory,
  readScrollMemory,
  SCROLL_MEMORY_LIMITS,
  writeScrollMemory,
} from './scrollMemory';

const { KEY_PREFIX, TTL_MS, MAX_ENTRIES } = SCROLL_MEMORY_LIMITS;

/** 固定一个"现在"，避免用例依赖真实时间 */
const NOW = 1_800_000_000_000;

beforeEach(() => {
  localStorage.clear();
});

describe('写入与读回', () => {
  it('写进去再读出来是同一份（位置、模式、时间戳）', () => {
    writeScrollMemory('note-1', 1234, 'clean', NOW);
    expect(readScrollMemory('note-1', NOW)).toEqual({ y: 1234, mode: 'clean', at: NOW });
  });

  it('没有记忆时读出 null（而不是抛错）', () => {
    expect(readScrollMemory('从未读过的笔记', NOW)).toBeNull();
  });

  it('按笔记各记各的，互不干扰', () => {
    writeScrollMemory('note-a', 100, 'clean', NOW);
    writeScrollMemory('note-b', 200, 'original', NOW);
    expect(readScrollMemory('note-a', NOW)?.y).toBe(100);
    expect(readScrollMemory('note-b', NOW)?.y).toBe(200);
  });

  it('clear 之后读不到（恢复成功后清掉待恢复值时用它）', () => {
    writeScrollMemory('note-1', 500, 'clean', NOW);
    clearScrollMemory('note-1');
    expect(readScrollMemory('note-1', NOW)).toBeNull();
  });

  it('拒绝非法位置：负数与 NaN 不落盘', () => {
    writeScrollMemory('note-1', -1, 'clean', NOW);
    writeScrollMemory('note-1', Number.NaN, 'clean', NOW);
    expect(readScrollMemory('note-1', NOW)).toBeNull();
  });

  it('空 noteId 不落盘（不产生一个没有归属的键）', () => {
    writeScrollMemory('', 100, 'clean', NOW);
    expect(localStorage.length).toBe(0);
  });
});

describe('脏数据与过期', () => {
  it('★ 脏 JSON 读出 null 且不抛错，并顺手清掉', () => {
    localStorage.setItem(`${KEY_PREFIX}note-1`, '{ 这不是 JSON');
    expect(() => readScrollMemory('note-1', NOW)).not.toThrow();
    expect(readScrollMemory('note-1', NOW)).toBeNull();
    expect(localStorage.getItem(`${KEY_PREFIX}note-1`)).toBeNull();
  });

  it('★ 形状不对的值（数字 / null / 缺字段 / y 是字符串）一律读不出', () => {
    const bad = ['42', 'null', '"字符串"', '{}', '{"y":"abc","mode":"clean","at":1}', '{"y":1}'];
    for (const raw of bad) {
      localStorage.setItem(`${KEY_PREFIX}note-1`, raw);
      expect(readScrollMemory('note-1', NOW)).toBeNull();
    }
  });

  it('★ 超过 30 天的记忆不恢复，且被清掉', () => {
    writeScrollMemory('note-1', 900, 'clean', NOW - TTL_MS - 1);
    expect(readScrollMemory('note-1', NOW)).toBeNull();
    // 顺手清掉：否则每次读都要再判一次，且它会一直占着一个键位
    expect(localStorage.getItem(`${KEY_PREFIX}note-1`)).toBeNull();
  });

  it('刚好在有效期内仍然恢复（边界不能取反）', () => {
    writeScrollMemory('note-1', 900, 'clean', NOW - TTL_MS + 1000);
    expect(readScrollMemory('note-1', NOW)?.y).toBe(900);
  });
});

describe('容量上限', () => {
  it(`★ 超过 ${MAX_ENTRIES} 条时淘汰最旧的，且不动刚写进来的那一条`, () => {
    // 先塞满，时间戳递增：note-0 最旧
    for (let i = 0; i < MAX_ENTRIES; i += 1) {
      writeScrollMemory(`note-${i}`, i, 'clean', NOW + i);
    }
    expect(localStorage.length).toBe(MAX_ENTRIES);

    // 再写一条新的，触发淘汰
    writeScrollMemory('note-new', 777, 'clean', NOW + MAX_ENTRIES);

    expect(localStorage.length).toBe(MAX_ENTRIES);
    // 最旧的被淘汰
    expect(readScrollMemory('note-0', NOW + MAX_ENTRIES)).toBeNull();
    // 刚写进来的必须还在（protectedKey 那条不变量）
    expect(readScrollMemory('note-new', NOW + MAX_ENTRIES)?.y).toBe(777);
    // 次旧的还在
    expect(readScrollMemory('note-1', NOW + MAX_ENTRIES)?.y).toBe(1);
  });

  it('上限之内的写入不触发淘汰', () => {
    for (let i = 0; i < 5; i += 1) writeScrollMemory(`note-${i}`, i, 'clean', NOW + i);
    expect(localStorage.length).toBe(5);
    expect(readScrollMemory('note-0', NOW + 4)?.y).toBe(0);
  });

  it('不碰别人家的键（只淘汰本模块前缀下的条目）', () => {
    localStorage.setItem('engramnote:token', '别人的东西');
    for (let i = 0; i < MAX_ENTRIES + 3; i += 1) {
      writeScrollMemory(`note-${i}`, i, 'clean', NOW + i);
    }
    expect(localStorage.getItem('engramnote:token')).toBe('别人的东西');
  });
});
