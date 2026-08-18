/**
 * @file 回收站页面
 * @description 展示已移入回收站的笔记列表，支持：
 * 1. 查看每项笔记的附属统计（卡片/题目/批注/版本/双向链接数）
 * 2. 恢复笔记（原子包整体还原，同名冲突自动改名并提示）
 * 3. 彻底删除单条笔记（PurgeNoteDialog，悬挂引用策略 + 可选提升核心卡片）
 * 4. 清空回收站（物理删除全部，二次确认）
 */
import { useEffect, useState } from 'react'
import {
  getTrashedNotes,
  restoreNote,
  purgeNote,
  purgeAllTrash,
  type TrashNoteItem,
} from '../api/client'
import { PurgeNoteDialog } from '../components/DeleteNoteDialog'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { sourceTypeLabels } from '../utils/labels'
import { formatDateTime } from '../utils/datetime'

export default function Trash() {
  /** 回收站列表 */
  const [items, setItems] = useState<TrashNoteItem[]>([])
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true)
  /** 错误信息 */
  const [error, setError] = useState('')
  /** 操作进行中的笔记 ID（恢复/彻底删除），用于按钮禁用 */
  const [operatingId, setOperatingId] = useState<string | null>(null)
  /** 待彻底删除的笔记（打开 PurgeNoteDialog） */
  const [noteToPurge, setNoteToPurge] = useState<TrashNoteItem | null>(null)
  /** 清空回收站确认弹窗 */
  const [showPurgeAll, setShowPurgeAll] = useState(false)

  /** 加载回收站列表 */
  async function fetchTrash() {
    setLoading(true)
    setError('')
    try {
      const res = await getTrashedNotes()
      setItems(res.items)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchTrash()
  }, [])

  /**
   * 恢复笔记
   * 原子包整体还原；若原位置已有同名新文件，后端自动加序号后缀，
   * 此处以 alert 提示 renamed_to。
   */
  async function handleRestore(item: TrashNoteItem) {
    setOperatingId(item.note.id)
    try {
      const res = await restoreNote(item.note.id)
      if (res.renamed_to) {
        alert(`原位置已存在同名文件，恢复后已自动重命名为「${res.renamed_to}」`)
      }
      setItems((prev) => prev.filter((it) => it.note.id !== item.note.id))
    } catch (err) {
      alert(err instanceof Error ? err.message : '恢复失败')
    } finally {
      setOperatingId(null)
    }
  }

  /** 确认彻底删除单条笔记 */
  async function confirmPurge(promoteKeyCards: boolean) {
    if (!noteToPurge) return
    setOperatingId(noteToPurge.note.id)
    try {
      await purgeNote(noteToPurge.note.id, promoteKeyCards)
      setItems((prev) => prev.filter((it) => it.note.id !== noteToPurge.note.id))
      setNoteToPurge(null)
    } catch (err) {
      alert(err instanceof Error ? err.message : '彻底删除失败')
    } finally {
      setOperatingId(null)
    }
  }

  /** 确认清空回收站 */
  async function confirmPurgeAll() {
    try {
      const res = await purgeAllTrash()
      setShowPurgeAll(false)
      setItems([])
      if (res.failed > 0) {
        alert(`已彻底删除 ${res.purged} 条，${res.failed} 条删除失败，请重试`)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : '清空回收站失败')
    }
  }

  return (
    <div className="page-enter">
      {/* 头部：标题 + 清空按钮 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-lg)' }}>
        <div>
          <h1 className="heading-serif" style={{ fontSize: '1.5rem' }}>回收站</h1>
          <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
            笔记及其全部内容（卡片、题目、复习记录、关系）作为整体保存，可随时整体恢复
          </p>
        </div>
        {items.length > 0 && (
          <button
            className="btn"
            style={{ background: 'var(--color-error)', color: '#fff' }}
            onClick={() => setShowPurgeAll(true)}
          >
            清空回收站
          </button>
        )}
      </div>

      {error && <ErrorDisplay message={error} onRetry={fetchTrash} />}

      {loading ? (
        <LoadingSpinner />
      ) : items.length === 0 ? (
        <EmptyState
          message="回收站是空的"
          description="被删除的笔记会在这里保留，随时可以恢复"
        />
      ) : (
        <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
          {items.map((item) => (
            <article key={item.note.id} className="card" style={{ padding: 'var(--space-md)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-md)' }}>
                {/* 左侧：标题 + 元信息 + 附属统计 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-xs)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.note.title}
                  </h3>
                  <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-sm)' }}>
                    {sourceTypeLabels[item.note.source_type] || item.note.source_type}
                    {' · '}
                    删除于 {item.note.trashed_at ? formatDateTime(item.note.trashed_at) : '—'}
                  </div>
                  {/* 附属统计：恢复时可还原的内容 */}
                  <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap', fontSize: '0.75rem' }}>
                    {[
                      `${item.card_count} 张卡片`,
                      `${item.quiz_count} 道题目`,
                      `${item.annotation_count} 条批注`,
                      `${item.version_count} 个版本`,
                      `${item.link_count} 个双向链接`,
                    ].map((text) => (
                      <span
                        key={text}
                        style={{
                          padding: '2px 8px',
                          background: 'var(--color-primary-light)',
                          borderRadius: 'var(--radius-sm)',
                          color: 'var(--color-text-secondary)',
                        }}
                      >
                        {text}
                      </span>
                    ))}
                  </div>
                </div>
                {/* 右侧：操作按钮 */}
                <div style={{ display: 'flex', gap: 'var(--space-sm)', flexShrink: 0 }}>
                  <button
                    className="btn btn-primary"
                    disabled={operatingId === item.note.id}
                    onClick={() => handleRestore(item)}
                  >
                    恢复
                  </button>
                  <button
                    className="btn btn-danger"
                    disabled={operatingId === item.note.id}
                    onClick={() => setNoteToPurge(item)}
                  >
                    彻底删除
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      {/* 彻底删除单条确认弹窗 */}
      {noteToPurge && (
        <PurgeNoteDialog
          note={noteToPurge.note}
          onClose={() => setNoteToPurge(null)}
          onConfirm={confirmPurge}
        />
      )}

      {/* 清空回收站确认弹窗 */}
      {showPurgeAll && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={() => setShowPurgeAll(false)}
        >
          <div className="card" style={{ maxWidth: 460, width: '100%', padding: 24 }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ color: 'var(--color-error)' }}>清空回收站</h3>
            <p style={{ marginBottom: 'var(--space-md)' }}>
              确定彻底删除回收站中的全部 {items.length} 条笔记吗？此操作<strong>不可恢复</strong>。
            </p>
            <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
              其他笔记对这些笔记的引用将以「[已删除的笔记]」占位符保留，不会影响其他笔记的内容。
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-sm)', marginTop: 'var(--space-lg)' }}>
              <button className="btn btn-secondary" onClick={() => setShowPurgeAll(false)}>
                取消
              </button>
              <button
                className="btn"
                style={{ background: 'var(--color-error)', color: '#fff' }}
                onClick={confirmPurgeAll}
              >
                清空
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
