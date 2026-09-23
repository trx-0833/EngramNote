/**
 * @file Markdown HTML 白名单消毒
 * @description 在 Markdown 渲染为 HTML 后、进入 DOM 前，用 DOMPurify 做白名单消毒，
 * 阻止 <script>、<iframe>、事件属性（onerror 等）等原始 HTML 注入。
 * 全局限定：a 强制 rel="noopener noreferrer"；img 仅允许 http(s) 源。
 */
import DOMPurify from 'dompurify';

// 允许标签最小集（不含 script / iframe / 可绑定事件属性的任意标签）
const ALLOWED_TAGS = [
  'p',
  'br',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'blockquote',
  'pre',
  'code',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
  'a',
  'img',
  'strong',
  'em',
  'del',
  'hr',
  'div',
  'span',
  'sub',
  'sup',
];

// 白名单属性：href/src/alt/title 承载内容，class/style 用于代码高亮与 KaTeX 布局
const ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'class', 'style'];

// 仅允许 http(s) 图片源（排除 data: / javascript: 等）
const HTTP_SRC_RE = /^https?:\/\//i;

// a 强制加 rel，防止经 target=_blank 打开新页时的 window.opener 反向注入
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('rel', 'noopener noreferrer');
  }
  if (node.tagName === 'IMG') {
    const src = node.getAttribute('src') || '';
    if (!HTTP_SRC_RE.test(src)) {
      node.removeAttribute('src');
    }
  }
});

/**
 * 对 Markdown 渲染后的 HTML 字符串做白名单消毒。
 *
 * @param dirty - marked.parse() 之后的 HTML 字符串
 * @returns 消毒后的 HTML 字符串
 */
export function sanitizeHtml(dirty: string): string {
  if (!dirty) return '';
  return DOMPurify.sanitize(dirty, { ALLOWED_TAGS, ALLOWED_ATTR });
}
