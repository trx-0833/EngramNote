/**
 * @file 引用回跳的纯函数测试（阶段 2.7）
 *
 * ⚠️ 改造前 `citationJump.ts` **完全没有测试**，而它是"引用可回跳"
 * （原则 P4）的关键一环。更麻烦的是它的失败模式是**静默**的：
 * 指纹搜不到时不会报错，只是不高亮 —— 用户看到的是"点了没反应"。
 *
 * 这里锁住三类行为：
 * 1. 纯文本化：Markdown 标记必须被剥掉（否则永远搜不到渲染后的文本）
 * 2. 指纹构造：非法区间返回空串、过长时在句末截断
 * 3. 退化路径：拿不到容器时不得抛错
 */
import { describe, expect, it } from 'vitest';
import { buildFingerprint, clearHighlight, highlightCitation, stripMarkdown } from './citationJump';

describe('stripMarkdown', () => {
  it('剥掉会影响文本连续性的标记', () => {
    expect(stripMarkdown('# 标题')).toBe('标题');
    expect(stripMarkdown('**粗体**与*斜体*')).toBe('粗体与斜体');
    expect(stripMarkdown('`code` 保留内容')).toBe('code 保留内容');
    expect(stripMarkdown('- 列表项')).toBe('列表项');
    expect(stripMarkdown('> 引用')).toBe('引用');
  });

  it('链接保留文字、图片整体丢弃', () => {
    expect(stripMarkdown('见[文档](http://x/y)')).toBe('见文档');
    expect(stripMarkdown('前![图](a.png)后')).toBe('前 后');
  });

  it('压缩空白：渲染后的换行会变成空格', () => {
    // 实测踩过：编号列表在源文里是两行，渲染后是同一段
    expect(stripMarkdown('第一行\n第二行')).toBe('第一行 第二行');
    expect(stripMarkdown('  多   空格  ')).toBe('多 空格');
  });

  it('围栏代码块整体丢弃', () => {
    expect(stripMarkdown('前\n```js\nconst a = 1\n```\n后')).toBe('前 后');
  });
});

describe('buildFingerprint', () => {
  const md = '一二三四五六七八九十。后面还有很多内容需要被截断掉才行不然太长了。';

  it('区间内文本较短时原样返回（已纯文本化）', () => {
    expect(buildFingerprint(md, 0, 11)).toBe('一二三四五六七八九十。');
  });

  it('★ 区间非法时返回空串，而不是抛错或用错误的内容去搜', () => {
    // 给出错误的高亮比不给更糟 —— 这是本模块的设计原则
    expect(buildFingerprint(md, -1, 5)).toBe('');
    expect(buildFingerprint(md, 5, 5)).toBe('');
    expect(buildFingerprint(md, 8, 3)).toBe('');
    expect(buildFingerprint(md, md.length + 10, md.length + 20)).toBe('');
    expect(buildFingerprint(md, undefined, 10)).toBe('');
    expect(buildFingerprint(md, 0, null)).toBe('');
  });

  it('过长时在句末标点处截断（避免停在半个词上）', () => {
    const long = '甲'.repeat(30) + '。' + '乙'.repeat(80);
    const fp = buildFingerprint(long, 0, long.length);
    expect(fp.length).toBeLessThanOrEqual(60);
    expect(fp.endsWith('。')).toBe(true);
  });

  it('纯标记区间（剥完是空）返回空串', () => {
    expect(buildFingerprint('---\n\n', 0, 5)).toBe('');
  });
});

describe('highlightCitation 的退化路径', () => {
  it('容器缺失时返回"未高亮、未滚动"，不抛错', () => {
    expect(highlightCitation(null, 'x', 0, 1)).toEqual({ highlighted: false, scrolled: false });
  });

  it('指纹为空时退化为只滚动，不乱标高亮', () => {
    const container = document.createElement('div');
    container.textContent = '正文';
    const result = highlightCitation(container, '正文', 0, 0);
    expect(result.highlighted).toBe(false);
  });

  it('能在渲染文本里定位并包一层 mark', () => {
    const container = document.createElement('div');
    const p = document.createElement('p');
    p.textContent = '拉哇水电站装设多台水轮发电机组。';
    container.appendChild(p);
    const md = '拉哇水电站装设多台水轮发电机组。';
    const result = highlightCitation(container, md, 0, md.length);
    expect(result.highlighted).toBe(true);
    expect(container.querySelector('mark.citation-highlight')).not.toBeNull();
  });

  it('clearHighlight 把 mark 拆回纯文本（不残留空标签）', () => {
    const container = document.createElement('div');
    const p = document.createElement('p');
    p.textContent = '一段话';
    container.appendChild(p);
    const md = '一段话';
    highlightCitation(container, md, 0, md.length);
    clearHighlight(container);
    expect(container.querySelector('mark')).toBeNull();
    expect(container.textContent).toBe('一段话');
  });
});
