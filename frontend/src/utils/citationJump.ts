/**
 * @file 引用回跳：在渲染后的正文里定位并高亮被引用的段落
 * @description 阶段 2.7 的前端一半
 *
 * ## 为什么需要它
 *
 * 后端给出的定位是 `char_start/char_end`（在**笔记 Markdown 源文**里的字符下标）。
 * 但正文在页面上是 `renderMarkdown(md)` 产出的 **HTML**：Markdown 语法符号被
 * 去掉了、段落被包进了标签。于是"源文里的第 1000~1500 个字符"**无法**直接
 * 换算成 DOM 里的位置 —— 这是本功能真正的难点，不能用"直接 slice HTML"糊过去。
 *
 * ## 采用的办法：源文切片 → 渲染文本里搜索
 *
 * 1. 用 `char_start/char_end` 从 Markdown 源文里切出 chunk 文本（这一步是精确的，
 *    后端保证了 `md.slice(char_start, char_end) === chunk 内容`）
 * 2. 把 chunk 首段规范化成"适合在渲染文本里搜索"的指纹（去 Markdown 标记、
 *    压空白），因为渲染后这些标记不存在了
 * 3. 在容器的纯文本里找这个指纹，用 `Range` 包一层 `<mark>` 并滚动过去
 *
 * 第 2 步是必要妥协：指纹可能因行内格式（粗体/链接）而搜不到。因此**必须
 * 有失败路径** —— 找不到时退回"滚动到章节/笔记开头"，而不是静默失败或
 * 乱标一个位置。给出错误的高亮比不给更糟。
 */

/** 高亮元素的类名（样式见 styles/） */
export const HIGHLIGHT_CLASS = 'citation-highlight';

/** 指纹最大长度：太长会因中途的格式差异而搜不到，太短会误命中多处 */
const FINGERPRINT_MAX = 60;

/**
 * 去掉 Markdown 标记，得到用于在渲染文本里搜索的纯文本
 *
 * 只处理会影响"文本连续性"的标记：行内代码、粗斜体、链接、图片、标题号、
 * 引用号、列表号。表格竖线与多级空白一并压缩。
 */
export function stripMarkdown(text: string): string {
  return (
    text
      .replace(/```[\s\S]*?```/g, ' ') // 围栏代码块
      .replace(/`([^`]*)`/g, '$1') // 行内代码：保留内容
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ') // 图片：整体丢弃
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // 链接：保留文字
      .replace(/^\s{0,3}#{1,6}\s+/gm, '') // 标题号
      .replace(/^\s{0,3}>\s?/gm, '') // 引用号
      // 分隔线（`---` / `***` / `___` / `- - -`）：渲染后是一个 <hr>，
      // **不产生任何文本**，留着它只会造出一个永远搜不到的指纹。
      // 必须放在表格竖线替换之前 —— 否则 `|---|---|` 会被压成 ` --- `
      // 而被误判成分隔线（它不是）。
      .replace(/^[ \t]{0,3}(?:[-*_][ \t]*){3,}$/gm, ' ')
      .replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+/gm, '') // 列表号
      .replace(/[*_~]{1,3}/g, '') // 粗体/斜体/删除线
      .replace(/\|/g, ' ') // 表格竖线
      .replace(/\s+/g, ' ') // 压缩空白（渲染后换行变空格）
      .trim()
  );
}

/**
 * 由 Markdown 源文与字符区间得出"可在渲染文本里搜索的指纹"
 *
 * @returns 指纹；区间无效或内容为空时返回空串
 */
export function buildFingerprint(
  markdown: string,
  charStart?: number | null,
  charEnd?: number | null,
): string {
  if (
    typeof charStart !== 'number' ||
    typeof charEnd !== 'number' ||
    charStart < 0 ||
    charEnd <= charStart ||
    charStart >= markdown.length
  ) {
    return '';
  }
  const slice = markdown.slice(charStart, Math.min(charEnd, markdown.length));
  const plain = stripMarkdown(slice);
  if (!plain) return '';

  if (plain.length <= FINGERPRINT_MAX) return plain;
  // 优先在句末标点处截断，避免指纹中间恰好停在半个词上
  const head = plain.slice(0, FINGERPRINT_MAX);
  const lastStop = Math.max(
    head.lastIndexOf('。'),
    head.lastIndexOf('；'),
    head.lastIndexOf('！'),
    head.lastIndexOf('？'),
    head.lastIndexOf('. '),
  );
  return lastStop >= FINGERPRINT_MAX / 2 ? head.slice(0, lastStop + 1) : head;
}

/** 在元素的所有文本节点里找 `needle`，返回起止 (节点, 偏移) */
function findTextRange(root: HTMLElement, needle: string): { node: Text; offset: number } | null {
  if (!needle) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode() as Text | null;
  while (node) {
    const idx = (node.nodeValue || '').indexOf(needle);
    if (idx >= 0) return { node, offset: idx };
    node = walker.nextNode() as Text | null;
  }
  return null;
}

export interface HighlightResult {
  /** 是否成功高亮到引用段落（false = 退化为只滚动） */
  highlighted: boolean;
  /** 是否至少完成了滚动（元素存在） */
  scrolled: boolean;
}

/**
 * 在容器里定位并高亮引用段落，然后滚动到可见位置
 *
 * @param container 正文容器（`markdown-body` 那个元素）
 * @param markdown  当前显示的 Markdown 源文（**必须与 char_start 同一份**）
 * @param charStart/charEnd 后端给出的字符区间
 * @returns 结果标记；调用方据此决定是否提示"未能定位"
 */
export function highlightCitation(
  container: HTMLElement | null,
  markdown: string,
  charStart?: number | null,
  charEnd?: number | null,
): HighlightResult {
  if (!container) return { highlighted: false, scrolled: false };

  clearHighlight(container);

  const fingerprint = buildFingerprint(markdown, charStart, charEnd);
  // 逐级退化的候选：完整指纹 → 逐步缩短
  const candidates = fingerprint
    ? [fingerprint, fingerprint.slice(0, 40), fingerprint.slice(0, 20)]
    : [];

  for (const candidate of candidates) {
    if (candidate.length < 4) continue;
    const hit = findTextRange(container, candidate);
    if (!hit) continue;

    try {
      const range = document.createRange();
      range.setStart(hit.node, hit.offset);
      range.setEnd(hit.node, hit.offset + candidate.length);
      const mark = document.createElement('mark');
      mark.className = HIGHLIGHT_CLASS;
      range.surroundContents(mark);
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return { highlighted: true, scrolled: true };
    } catch {
      // surroundContents 在区间跨越多个元素时抛错 —— 改为整段滚动
      break;
    }
  }

  // 退化：滚动到章节标题或容器开头（**不**乱标高亮）
  container.scrollIntoView({ behavior: 'smooth', block: 'start' });
  return { highlighted: false, scrolled: true };
}

/** 清除容器内已有的高亮（把 `<mark>` 拆回纯文本） */
export function clearHighlight(container: HTMLElement | null): void {
  if (!container) return;
  container.querySelectorAll(`mark.${HIGHLIGHT_CLASS}`).forEach((mark) => {
    const parent = mark.parentNode;
    if (!parent) return;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  });
}
