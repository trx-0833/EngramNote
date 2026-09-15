/**
 * @file `retrievalNotice` 的判据锁
 *
 * 这个文件守两件事：
 *
 * 1. **`hybrid` 必须有提示** —— 这是本轮修的那个缺口：实际观测到的是 `hybrid`
 *    （全链路 e2e 里上传 48 秒后提问），而提示只挂在 `bm25_only` 上。
 *    没有这条用例，将来有人"顺手精简"掉 `hybrid` 分支不会有人发现。
 * 2. **`bm25_only` 的文案逐字不变** —— 它是改造前就有的提示，本轮只是搬家，
 *    不能借机改字（用户已经见过这句话）。
 */
import { describe, expect, it } from 'vitest';

import { retrievalNotice } from './retrievalNotice';

describe('retrievalNotice', () => {
  it('★ hybrid 有提示（本轮修的缺口：实际发生的就是它）', () => {
    const notice = retrievalNotice('hybrid');
    expect(notice).toBeTruthy();
    expect(notice).toContain('关键词检索');
  });

  it('bm25_only 的文案与改造前逐字一致', () => {
    expect(retrievalNotice('bm25_only')).toBe('已降级为关键词检索（向量服务不可用）');
  });

  it('full_vector 不提示（正常路径不该出现警告）', () => {
    expect(retrievalNotice('full_vector')).toBeNull();
  });

  it('缺省与未知取值都不提示（不编造警告）', () => {
    expect(retrievalNotice(undefined)).toBeNull();
    expect(retrievalNotice('')).toBeNull();
    expect(retrievalNotice('something_new_from_backend')).toBeNull();
  });

  it('hybrid 的文案不断言单一成因（语料没向量 / 没过阈值 都会得到 hybrid）', () => {
    const notice = retrievalNotice('hybrid') ?? '';
    // 说"可能"而不是"就是"：界面上不写自己没验证过的因果
    expect(notice).toContain('可能');
  });
});
