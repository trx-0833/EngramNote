/**
 * @file 原文语境（阶段 3.13）
 * @description 复习答错时一键展开这道题所属卡片的**原文出处**，并可跳到笔记原文
 *
 * ## 为什么需要它
 *
 * 复习答错之后用户最想做的事是"回去看一眼原文"，而改造前的复习页只给
 * 「正确答案」四个字 —— 用户要么凭记忆去翻笔记，要么就此放弃。
 * 卡片上的 `source_text` 正是 LLM 理解时记下的原文段落，它一直在库里，
 * 只是从来没有出现在复习页上。
 *
 * ## 为什么是**懒加载**而不是随题目一起下发
 *
 * 一次复习有 10~50 道题，而用户大多数题是答对的、不会展开原文。
 * 把 `source_text` 塞进到期列表意味着每次复习都多传几十段正文，
 * 换来的却是一个大多数时候用不到的字段。所以这里按需请求
 * （`getKnowledgeCard` 是已有接口）。
 *
 * ## 失败路径
 *
 * 加载失败**不静默**：显示错误与重试按钮。一个永远转圈的折叠区
 * 比没有这个功能更糟 —— 用户会以为原文不存在。
 */
import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { getKnowledgeCard } from '../../api/qa'

interface SourceContextProps {
  /** 卡片 ID（`source_text` 存在卡片上） */
  cardId?: string | null
  /** 来源笔记 ID；有值时提供"在笔记中查看原文"链接 */
  noteId?: string | null
}

export default function SourceContext({ cardId, noteId }: SourceContextProps) {
  const [expanded, setExpanded] = useState(false)
  const [sourceText, setSourceText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!cardId) return
    setLoading(true)
    setError('')
    try {
      const card = await getKnowledgeCard(cardId)
      setSourceText((card.source_text || '').trim())
    } catch (e) {
      setError(e instanceof Error ? e.message : '原文加载失败')
    } finally {
      setLoading(false)
    }
  }, [cardId])

  // 加载由**点击**触发，而不是放在 effect 里同步 setState ——
  // 后者会引发级联渲染（eslint 的 react-hooks/set-state-in-effect 正是查这个），
  // 而且这里本来就只有"用户展开"这一个触发点，没有必要用 effect 去观察状态。
  const toggle = useCallback(() => {
    setExpanded(prev => {
      const next = !prev
      if (next && sourceText === null && !loading) void load()
      return next
    })
  }, [sourceText, loading, load])

  if (!cardId) return null

  return (
    <div style={{ marginBottom: 'var(--space-md)' }}>
      <button
        className="btn btn-link"
        onClick={toggle}
        aria-expanded={expanded}
        style={{
          background: 'none',
          border: 'none',
          padding: 0,
          color: 'var(--color-primary)',
          cursor: 'pointer',
          fontSize: '0.9rem',
          textDecoration: 'underline',
        }}
      >
        {expanded ? '收起原文语境' : '查看原文语境'}
      </button>

      {expanded && (
        <div
          style={{
            marginTop: 'var(--space-sm)',
            padding: 'var(--space-sm) var(--space-md)',
            background: 'var(--color-bg)',
            borderLeft: '3px solid var(--color-border)',
            borderRadius: 4,
            fontSize: '0.9rem',
            lineHeight: 1.7,
          }}
        >
          {loading && (
            <span style={{ color: 'var(--color-text-secondary)' }}>正在读取原文...</span>
          )}
          {!loading && error && (
            <div>
              {/* `var(--color-error)` 而不是字面量 `#f44336`：后者白底 3.68:1，
                  压在 `--color-bg`（#faf9f7）上更低，而这行是 0.9rem 的错误文案
                  （要求 4.5:1）。同一语义在 base.css 里有取值，不再各写一份。 */}
              <p style={{ color: 'var(--color-error)' }}>{error}</p>
              <button className="btn btn-link" onClick={() => void load()}>
                重试
              </button>
            </div>
          )}
          {!loading && !error && sourceText === '' && (
            <span style={{ color: 'var(--color-text-secondary)' }}>
              这张卡片的生成过程没有记下原文段落。
            </span>
          )}
          {!loading && !error && sourceText && (
            <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{sourceText}</p>
          )}
          {noteId && (
            <p style={{ marginTop: 'var(--space-sm)', marginBottom: 0 }}>
              <Link to={`/notes/${noteId}?view=clean`}>在笔记中查看完整原文 →</Link>
            </p>
          )}
        </div>
      )}
    </div>
  )
}
