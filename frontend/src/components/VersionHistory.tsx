/**
 * @file 版本历史组件
 * @description 以模态浮层形式展示笔记的版本历史，支持：
 * 1. 版本列表（按版本号倒序，含来源徽章、大小、变更摘要）
 * 2. 任意两个版本之间的行级 diff 对比
 * 3. 单个版本内容的预览
 * 4. 恢复到指定历史版本（恢复前会弹窗确认）
 *
 * diff 数据来自后端 NoteVersionDiffResponse，与 DiffView 组件的 DiffBlock 不同，
 * 这里使用扁平的 NoteVersionDiffLine[] 结构。
 */
import { useEffect, useState, useCallback } from 'react'
import {
  listVersions,
  getVersion,
  diffVersions,
  restoreVersion,
  type NoteVersion,
  type NoteVersionDiffResponse,
} from '../api/client'

interface VersionHistoryProps {
  /** 笔记 ID */
  noteId: string;
  /** 关闭面板回调 */
  onClose: () => void;
  /** 恢复成功后回调，用于刷新笔记详情 */
  onRestored: () => void;
}

/** 来源徽章配置：标签 + 背景色 */
function getSourceBadge(source: string): { label: string; bg: string } {
  switch (source) {
    case 'user_edit':
      // 手动编辑：主色（蓝色）
      return { label: '手动编辑', bg: 'var(--color-primary)' }
    case 'auto_clean':
      // 自动清洗：次要文字色（灰色）
      return { label: '自动清洗', bg: 'var(--color-text-secondary)' }
    case 'system':
      // 系统：成功色（绿色）
      return { label: '系统', bg: 'var(--color-success)' }
    default:
      return { label: source, bg: 'var(--color-text-secondary)' }
  }
}

/** 格式化 ISO 时间为 YYYY-MM-DD HH:mm */
function formatDate(isoStr: string): string {
  const d = new Date(isoStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 格式化字节数为 KB 字符串 */
function formatSize(size: number): string {
  return `${(size / 1024).toFixed(1)} KB`
}

export default function VersionHistory({ noteId, onClose, onRestored }: VersionHistoryProps) {
  const [versions, setVersions] = useState<NoteVersion[]>([])
  /** 选中的两个版本号（用于 diff），null 表示未选 */
  const [selectedVersions, setSelectedVersions] = useState<[number | null, number | null]>([null, null])
  const [diffResult, setDiffResult] = useState<NoteVersionDiffResponse | null>(null)
  const [previewContent, setPreviewContent] = useState<{ version_number: number; content: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [restoring, setRestoring] = useState(false)

  /** 加载版本列表 */
  const loadVersions = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await listVersions(noteId)
      // 按版本号倒序排列
      const sorted = [...(data.versions || [])].sort((a, b) => b.version_number - a.version_number)
      setVersions(sorted)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载版本列表失败')
    } finally {
      setLoading(false)
    }
  }, [noteId])

  // 组件挂载时加载版本列表
  useEffect(() => {
    loadVersions()
  }, [loadVersions])

  /** 切换版本选中状态（最多选 2 个用于 diff） */
  function handleSelectVersion(versionNumber: number) {
    setSelectedVersions(prev => {
      const [a, b] = prev
      // 已选中则取消
      if (a === versionNumber) return [null, b]
      if (b === versionNumber) return [a, null]
      // 优先填第一个空位
      if (a === null) return [versionNumber, b]
      if (b === null) return [a, versionNumber]
      // 两个都满了，替换第二个
      return [a, versionNumber]
    })
    // 切换选择时清除旧的 diff 结果
    setDiffResult(null)
  }

  // 两个版本都选中时自动加载 diff
  useEffect(() => {
    const [v1, v2] = selectedVersions
    if (v1 === null || v2 === null) {
      setDiffResult(null)
      return
    }
    // 保证 v1 < v2，传给后端的参数顺序一致
    const [a, b] = v1 < v2 ? [v1, v2] : [v2, v1]
    let cancelled = false
    diffVersions(noteId, a, b)
      .then(res => {
        if (!cancelled) setDiffResult(res)
      })
      .catch(err => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载对比失败')
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedVersions, noteId])

  /** 预览指定版本的原始内容 */
  async function handlePreview(versionNumber: number) {
    setError('')
    try {
      const data = await getVersion(noteId, versionNumber)
      setPreviewContent({ version_number: data.version_number, content: data.content })
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载版本内容失败')
    }
  }

  /** 恢复到指定历史版本（先确认，再调用接口） */
  async function handleRestore(versionNumber: number) {
    if (!window.confirm('确定恢复到此版本吗？当前内容将被保存为新版本。')) return
    setRestoring(true)
    setError('')
    try {
      await restoreVersion(noteId, versionNumber)
      // 恢复成功后通知父组件刷新笔记详情，并关闭版本历史面板
      onRestored()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : '恢复版本失败')
    } finally {
      setRestoring(false)
    }
  }

  /** 已选中的版本数量 */
  const selectedCount = selectedVersions.filter(v => v !== null).length

  return (
    // 模态浮层：半透明遮罩 + 居中卡片
    <div
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        background: 'rgba(0,0,0,0.5)', zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{
          maxWidth: '900px', width: '90%', maxHeight: '85vh', overflowY: 'auto',
          padding: '1.5rem',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* 头部：标题 + 关闭按钮 */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-md)' }}>
          <h2 className="heading-serif" style={{ fontSize: '1.25rem', margin: 0 }}>版本历史</h2>
          <button className="btn btn-secondary" onClick={onClose} disabled={restoring}>关闭</button>
        </div>

        {error && (
          <p style={{ color: 'var(--color-error)', marginBottom: 'var(--space-sm)', fontSize: '0.875rem' }}>{error}</p>
        )}

        {loading ? (
          <p style={{ color: 'var(--color-text-secondary)', textAlign: 'center', padding: 'var(--space-lg)' }}>加载中...</p>
        ) : versions.length === 0 ? (
          <p style={{ color: 'var(--color-text-secondary)', textAlign: 'center', padding: 'var(--space-lg)' }}>暂无版本历史</p>
        ) : (
          <>
            {/* 版本列表 */}
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <p style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-xs)' }}>
                选择两个版本进行对比（已选 {selectedCount}/2）
              </p>
              {versions.map(v => {
                const badge = getSourceBadge(v.source)
                const isSelected = selectedVersions[0] === v.version_number || selectedVersions[1] === v.version_number
                return (
                  <div
                    key={v.id}
                    className="card"
                    style={{
                      padding: '0.75rem 1rem', marginBottom: '0.5rem',
                      border: isSelected ? '1px solid var(--color-primary)' : '1px solid var(--color-border)',
                      display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap',
                    }}
                  >
                    {/* 选择复选框 */}
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => handleSelectVersion(v.version_number)}
                      disabled={restoring}
                    />
                    {/* 版本号 */}
                    <span style={{ fontWeight: 600, minWidth: '40px' }}>v{v.version_number}</span>
                    {/* 来源徽章 */}
                    <span style={{ background: badge.bg, color: 'white', fontSize: '0.7rem', padding: '2px 8px', borderRadius: '9999px', whiteSpace: 'nowrap' }}>
                      {badge.label}
                    </span>
                    {/* 创建时间 */}
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>{formatDate(v.created_at)}</span>
                    {/* 内容大小 */}
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>{formatSize(v.content_size)}</span>
                    {/* 变更摘要 */}
                    {v.change_summary && (
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', flex: 1, minWidth: '120px' }}>
                        {v.change_summary}
                      </span>
                    )}
                    {/* 操作按钮 */}
                    <div style={{ display: 'flex', gap: '0.25rem', marginLeft: 'auto' }}>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                        onClick={() => handlePreview(v.version_number)}
                        disabled={restoring}
                      >
                        预览
                      </button>
                      <button
                        className="btn btn-primary"
                        style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                        onClick={() => handleRestore(v.version_number)}
                        disabled={restoring}
                      >
                        恢复此版本
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>

            {/* 对比面板：两个版本都选中时显示 */}
            {diffResult && (
              <div className="card" style={{ padding: '1rem', marginBottom: 'var(--space-md)' }}>
                <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>
                  版本对比: v{diffResult.v1_number} → v{diffResult.v2_number}
                </h3>
                {diffResult.diff_lines.length === 0 ? (
                  <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>两个版本完全相同，没有差异。</p>
                ) : (
                  <div style={{ fontFamily: 'monospace', fontSize: '0.85rem', lineHeight: 1.6 }}>
                    {diffResult.diff_lines.map((line, idx) => {
                      // 行前缀：added=+, removed=-, unchanged=空格
                      const prefix = line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '
                      // 行背景色：added=绿，removed=红，unchanged=透明
                      const bg = line.type === 'added'
                        ? 'rgba(34, 197, 94, 0.15)'
                        : line.type === 'removed'
                          ? 'rgba(239, 68, 68, 0.15)'
                          : 'transparent'
                      // 行文字色
                      const color = line.type === 'added'
                        ? 'var(--color-success)'
                        : line.type === 'removed'
                          ? 'var(--color-error)'
                          : 'var(--color-text)'
                      return (
                        <div key={idx} style={{ background: bg, color, padding: '1px 8px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          <span style={{ marginRight: '0.5rem', userSelect: 'none' }}>{prefix}</span>
                          {line.content}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}

            {/* 预览面板：点击预览后显示 */}
            {previewContent && (
              <div className="card" style={{ padding: '1rem', marginBottom: 'var(--space-md)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <h3 style={{ fontSize: '1rem', margin: 0 }}>版本内容预览 (v{previewContent.version_number})</h3>
                  <button
                    className="btn btn-secondary"
                    style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                    onClick={() => setPreviewContent(null)}
                  >
                    关闭预览
                  </button>
                </div>
                <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '0.85rem', lineHeight: 1.6, margin: 0, maxHeight: '400px', overflowY: 'auto' }}>
                  {previewContent.content}
                </pre>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
