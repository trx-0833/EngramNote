/**
 * @file 回收站相关弹窗组件
 * @description 提供两个弹窗：
 * 1. DeleteNoteDialog —— 移入回收站确认弹窗：展示笔记关联统计
 *    （卡片数 / 核心卡片数 / 双向链接数），说明"关联暂不可见但可随时恢复"。
 * 2. PurgeNoteDialog —— 彻底删除确认弹窗：警示不可恢复，说明悬挂引用
 *    策略，高级选项支持"将核心卡片提升为独立节点"。
 */
import { useEffect, useState } from 'react'
import { getNoteTrashInfo, type Note, type TrashInfoResponse } from '../api/client'

/** 弹窗遮罩 + 卡片容器的公共 inline 样式（与 NoteDetail 关联资料弹窗保持一致） */
const overlayStyle: React.CSSProperties = {
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
}

const cardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 520,
  maxHeight: '80vh',
  overflowY: 'auto',
}

const statRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 'var(--space-md)',
  marginBottom: 'var(--space-md)',
}

const statItemStyle: React.CSSProperties = {
  flex: 1,
  textAlign: 'center' as const,
  padding: 'var(--space-sm) var(--space-xs)',
  background: 'var(--color-primary-light)',
  borderRadius: 'var(--radius-md)',
}

const footerStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'flex-end',
  gap: 'var(--space-sm)',
  marginTop: 'var(--space-lg)',
}

interface DeleteNoteDialogProps {
  /** 要移入回收站的笔记 */
  note: Note
  /** 关闭弹窗（不执行任何操作） */
  onClose: () => void
  /** 确认移入回收站 */
  onConfirm: () => void
}

/**
 * 移入回收站确认弹窗
 *
 * 打开时异步加载笔记的关联统计（trash-info），展示：
 * - 卡片数 / 核心卡片数 / 双向链接数
 * - "关联暂不可见，但可在回收站中整体恢复"的说明
 */
export function DeleteNoteDialog({ note, onClose, onConfirm }: DeleteNoteDialogProps) {
  const [info, setInfo] = useState<TrashInfoResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getNoteTrashInfo(note.id)
      .then((res) => {
        if (!cancelled) setInfo(res)
      })
      .catch(() => {
        // 统计加载失败不阻塞删除流程，仅展示基础提示
        if (!cancelled) setInfo(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [note.id])

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div className="card" style={cardStyle} onClick={(e) => e.stopPropagation()}>
        <h3>移入回收站</h3>
        <p style={{ marginBottom: 'var(--space-md)' }}>
          确定将「{note.title}」移入回收站吗？
        </p>

        {loading && <p style={{ color: 'var(--color-text-secondary)' }}>正在统计关联内容…</p>}

        {!loading && info && (
          <>
            <div style={statRowStyle}>
              <div style={statItemStyle}>
                <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{info.card_count}</div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)' }}>知识卡片</div>
              </div>
              <div style={statItemStyle}>
                <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{info.key_card_count}</div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)' }}>核心卡片</div>
              </div>
              <div style={statItemStyle}>
                <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{info.link_count}</div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary)' }}>双向链接</div>
              </div>
            </div>
            <p
              style={{
                fontSize: '0.875rem',
                color: 'var(--color-text-secondary)',
                background: 'var(--color-accent-light)',
                padding: 'var(--space-sm) var(--space-md)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              该笔记与 {info.card_count} 张知识卡片、{info.link_count} 个原始资料存在双向链接。
              移入回收站后，这些关联将暂不可见，但不影响其他笔记的引用。
              笔记及其全部内容（卡片、题目、复习记录）将作为整体保存，可随时在回收站中恢复。
            </p>
          </>
        )}

        {!loading && !info && (
          <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
            笔记及其全部内容（卡片、题目、复习记录）将作为整体移入回收站，可随时恢复。
          </p>
        )}

        <div style={footerStyle}>
          <button className="btn btn-secondary" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" onClick={onConfirm}>
            确认移入
          </button>
        </div>
      </div>
    </div>
  )
}

interface PurgeNoteDialogProps {
  /** 要彻底删除的笔记 */
  note: Note
  /** 关闭弹窗（不执行任何操作） */
  onClose: () => void
  /**
   * 确认彻底删除
   *
   * @param promoteKeyCards - 是否将核心卡片提升为独立节点（图谱中保留）
   */
  onConfirm: (promoteKeyCards: boolean) => void
}

/**
 * 彻底删除确认弹窗（物理删除，悬挂引用策略）
 *
 * 高级选项："将本笔记中的核心卡片自动提升为独立节点"——
 * 笔记删除后，勾选的核心卡片在知识图谱中依然存活，不会成为信息孤岛。
 */
export function PurgeNoteDialog({ note, onClose, onConfirm }: PurgeNoteDialogProps) {
  const [promote, setPromote] = useState(false)

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div className="card" style={cardStyle} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ color: 'var(--color-error)' }}>彻底删除</h3>
        <p style={{ marginBottom: 'var(--space-md)' }}>
          确定彻底删除「{note.title}」吗？此操作<strong>不可恢复</strong>。
        </p>
        <p
          style={{
            fontSize: '0.875rem',
            color: 'var(--color-text-secondary)',
            background: 'var(--color-error-light)',
            padding: 'var(--space-sm) var(--space-md)',
            borderRadius: 'var(--radius-md)',
            marginBottom: 'var(--space-md)',
          }}
        >
          笔记及其卡片、题目、复习记录将被永久删除。其他笔记对该笔记的引用将以
          「[已删除的笔记]」占位符保留，不会破坏其他笔记的内容结构。
        </p>

        <label
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 'var(--space-sm)',
            padding: 'var(--space-sm) var(--space-md)',
            background: 'var(--color-accent-light)',
            borderRadius: 'var(--radius-md)',
            cursor: 'pointer',
            fontSize: '0.875rem',
          }}
        >
          <input
            type="checkbox"
            checked={promote}
            onChange={(e) => setPromote(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <span>
            <strong>高级选项：将本笔记中的核心卡片自动提升为独立节点</strong>
            <br />
            <span style={{ color: 'var(--color-text-secondary)' }}>
              勾选后，标记为核心（is_key_point）的卡片在知识图谱中依然存活，
              不会因父级笔记删除而变成信息孤岛。
            </span>
          </span>
        </label>

        <div style={footerStyle}>
          <button className="btn btn-secondary" onClick={onClose}>
            取消
          </button>
          <button
            className="btn"
            style={{ background: 'var(--color-error)', color: '#fff' }}
            onClick={() => onConfirm(promote)}
          >
            彻底删除
          </button>
        </div>
      </div>
    </div>
  )
}
