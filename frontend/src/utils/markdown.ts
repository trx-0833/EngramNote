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
 * 判断某个文本节点是否应跳过公式二次渲染
 * 跳过代码块、行内代码、脚本/样式以及已经由 KaTeX 渲染过的内容。
 */
function shouldSkipMathRender(node: Node): boolean {
  let parent = node.parentElement
  while (parent) {
    const tag = parent.tagName
    if (tag === 'CODE' || tag === 'PRE' || tag === 'SCRIPT' || tag === 'STYLE') {
      return true
    }
    if (parent.classList && parent.classList.contains('katex')) {
      return true
    }
    parent = parent.parentElement
  }
  return false
}

/**
 * 在 Markdown 渲染后的 HTML 上做一次 KaTeX 二次渲染。
 *
 * marked 默认会把原始 HTML 块（例如 MinerU 输出的 <table>）原样保留，
 * 因此其中的 $...$ / $$...$$ 不会被行内扩展处理。
 * 这里通过 DOM 遍历文本节点，对未被代码块/已有 KaTeX 包裹的公式再次渲染。
 *
 * @param html - marked.parse() 之后的 HTML 字符串
 * @returns 二次渲染后的 HTML 字符串
 */
export function renderMathInHtml(html: string): string {
  if (!html || !html.includes('$')) return html
  if (typeof document === 'undefined' || typeof NodeFilter === 'undefined') return html

  const container = document.createElement('div')
  container.innerHTML = html

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  let node: Node | null = walker.nextNode()
  while (node) {
    textNodes.push(node as Text)
    node = walker.nextNode()
  }

  const mathPattern = /\$\$([\s\S]+?)\$\$|\$([^\$\n]+?)\$/g

  for (const textNode of textNodes) {
    if (!textNode.data.includes('$')) continue
    if (shouldSkipMathRender(textNode)) continue

    const parent = textNode.parentNode
    if (!parent) continue

    const fragments: (Text | Node)[] = []
    let lastIndex = 0
    let match: RegExpExecArray | null

    mathPattern.lastIndex = 0
    while ((match = mathPattern.exec(textNode.data)) !== null) {
      if (match.index > lastIndex) {
        fragments.push(document.createTextNode(textNode.data.slice(lastIndex, match.index)))
      }

      const isDisplay = match[1] !== undefined
      const tex = (isDisplay ? match[1] : match[2]).trim()
      const rendered = renderKatex(tex, isDisplay)

      const template = document.createElement('template')
      template.innerHTML = rendered
      fragments.push(template.content.cloneNode(true))

      lastIndex = match.index + match[0].length
    }

    if (fragments.length === 0) continue

    if (lastIndex < textNode.data.length) {
      fragments.push(document.createTextNode(textNode.data.slice(lastIndex)))
    }

    for (const fragment of fragments) {
      parent.insertBefore(fragment, textNode)
    }
    parent.removeChild(textNode)
  }

  return container.innerHTML
}

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
  return renderMathInHtml(marked.parse(text) as string)
}
