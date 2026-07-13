/**
 * @file 共享 Markdown 渲染工具
 * @description 提取自 NoteDetail.tsx 的模块级 marked 配置，供多个页面复用。
 * 包含 KaTeX 数学公式扩展（blockMath/inlineMath）和 highlight.js 代码高亮。
 * 渲染失败时通过 try/catch 兜底，返回带 katex-error 样式的 <span>，不抛异常。
 */
import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js'
import katex from 'katex'

// 配置 marked 使用 highlight.js 进行代码块语法高亮
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
}))

// HTML 转义工具（用于 renderKatex 失败时的原始文本展示）
const escapeHtml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/**
 * 渲染 LaTeX 公式为 HTML
 * 使用 KaTeX 渲染，失败时返回带 katex-error 样式的 <span>，不抛异常。
 *
 * @param tex - LaTeX 源文本
 * @param displayMode - 是否为块级展示模式（$$...$），否则为行内模式（$...$）
 * @returns 渲染后的 HTML 字符串
 */
export const renderKatex = (tex: string, displayMode: boolean): string => {
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      errorColor: '#cc0000',
      strict: false,
    })
  } catch (e) {
    console.warn('KaTeX render failed:', e)
    return `<span class="katex-error" title="${escapeHtml(tex)}">${escapeHtml(tex)}</span>`
  }
}

// 块级公式 $$...$$ 与行内公式 $...$
marked.use({
  extensions: [
    {
      name: 'blockMath',
      level: 'block',
      start(src: string) { return src.indexOf('$$') },
      tokenizer(src: string) {
        const match = /^\$\$([\s\S]+?)\$\$/.exec(src)
        if (match) {
          return { type: 'blockMath', raw: match[0], text: match[1].trim() }
        }
        return undefined
      },
      renderer(token: any) {
        return `<p class="katex-block">${renderKatex(token.text, true)}</p>`
      },
    },
    {
      name: 'inlineMath',
      level: 'inline',
      start(src: string) { return src.indexOf('$') },
      tokenizer(src: string) {
        const match = /^\$([^\$\n]+?)\$/.exec(src)
        if (match) {
          return { type: 'inlineMath', raw: match[0], text: match[1].trim() }
        }
        return undefined
      },
      renderer(token: any) {
        return renderKatex(token.text, false)
      },
    },
  ],
})

export { marked }

/**
 * 将 Markdown 文本渲染为 HTML 字符串
 * 内部使用已配置 KaTeX 扩展和代码高亮的 marked 实例。
 * 空字符串或假值返回空字符串。
 *
 * @param text - Markdown 源文本
 * @returns 渲染后的 HTML 字符串
 */
export function renderMarkdown(text: string): string {
  if (!text) return ''
  return marked.parse(text) as string
}
