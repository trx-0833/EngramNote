/**
 * @file 清洗操作面板组件
 * @description 提供笔记清洗相关的操作界面，包括：
 * 1. 触发清洗按钮（converted 状态时显示）
 * 2. 清洗进度提示（cleaning 状态时显示）
 * 3. 重复块列表与操作（恢复/删除）
 * 4. 清洗统计摘要
 */
import { useState } from 'react'
import {
  startCleaning,
  stopCleaning,
  restoreBlock,
  deleteBlock,
  type NoteDetail,
} from '../api/client'

interface DuplicateBlock {
  block_index: number
  duplicate_of: number
  similarity: number
  /** 重复块文本内容（旧版本清洗可能缺失，重新清洗后补齐） */
  content?: string
  /** 被重复的保留块文本内容（旧版本清洗可能缺失） */
  original_content?: string
}

interface CleaningPanelProps {
  /** 笔记详情 */
  note: NoteDetail
  /** 清洗状态变化后的回调（刷新笔记数据） */
  onStatusChange: () => void
}

/**
 * 清洗操作面板组件
 *
 * 根据笔记状态显示不同的操作界面：
 * - converted：显示"开始清洗"按钮
 * - cleaning：显示清洗进度提示
 * - cleaned：显示重复块列表和操作按钮
 */
export default function CleaningPanel({ note, onStatusChange }: CleaningPanelProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  /** 当前展开内容对比的重复块索引（null 表示全部收起） */
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null)

  /** 触发清洗 */
  async function handleStartCleaning() {
    setLoading(true)
    setError('')
    try {
      await startCleaning(note.id)
      onStatusChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : '触发清洗失败')
    } finally {
      setLoading(false)
    }
  }

  /** 停止清洗 */
  async function handleStopCleaning() {
    if (!confirm('确定停止清洗？当前进度将丢失。')) return
    setLoading(true)
    setError('')
    try {
      await stopCleaning(note.id)
      onStatusChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : '停止清洗失败')
    } finally {
      setLoading(false)
    }
  }

  /** 恢复重复块 */
  async function handleRestore(blockIndex: number) {
    setLoading(true)
    setError('')
    try {
      await restoreBlock(note.id, blockIndex)
      onStatusChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : '恢复失败')
    } finally {
      setLoading(false)
    }
  }

  /** 删除重复块 */
  async function handleDelete(blockIndex: number) {
    if (!confirm(`确定删除块 ${blockIndex}？此操作不可恢复。`)) return
    setLoading(true)
    setError('')
    try {
      await deleteBlock(note.id, blockIndex)
      onStatusChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setLoading(false)
    }
  }

  // 从元数据中提取重复块信息
  const metadata = note.metadata_ as Record<string, unknown> | null
  const duplicatesDetail = (metadata?.duplicates_detail as DuplicateBlock[]) || []
  const duplicateBlocks = (metadata?.duplicate_blocks as number) || 0
  const totalChunks = (metadata?.total_chunks as number) || 0
  const cleanStats = metadata?.clean_stats as Record<string, number> | null

  return (
    <div className="cleaning-panel">
      {/* 错误提示 */}
      {error && (
        <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.875rem', marginBottom: 'var(--space-sm)' }}>
          {error}
        </p>
      )}

      {/* converted 状态：显示"开始清洗"按钮 */}
      {note.status === 'converted' && (
        <div style={{ textAlign: 'center', padding: 'var(--space-md)' }}>
          <p style={{ color: 'var(--color-text-secondary)', marginBottom: 'var(--space-md)', fontSize: '0.875rem' }}>
            笔记已转换完成，可以开始 AI 清洗
          </p>
          <button
            className="btn btn-primary"
            onClick={handleStartCleaning}
            disabled={loading}
          >
            {loading ? '正在触发...' : '开始清洗'}
          </button>
        </div>
      )}

      {/* cleaning 状态：显示进度提示 + 停止按钮 */}
      {note.status === 'cleaning' && (
        <div style={{ textAlign: 'center', padding: 'var(--space-md)' }}>
          <div className="cleaning-progress">
            <div className="cleaning-progress-bar" />
          </div>
          <p style={{ color: 'var(--color-warning)', marginTop: 'var(--space-sm)', fontSize: '0.875rem' }}>
            正在进行 AI 清洗，请稍候...
          </p>
          <button
            className="btn btn-danger"
            style={{ marginTop: 'var(--space-sm)', fontSize: '0.8rem' }}
            onClick={handleStopCleaning}
            disabled={loading}
          >
            {loading ? '正在停止...' : '停止清洗'}
          </button>
        </div>
      )}

      {/* cleaning_failed 状态：显示错误信息 + 重新清洗按钮 */}
      {note.status === 'cleaning_failed' && (
        <div style={{ textAlign: 'center', padding: 'var(--space-md)' }}>
          <p style={{ color: 'var(--color-error)', marginBottom: 'var(--space-sm)', fontSize: '0.875rem' }}>
            清洗失败{note.error_message ? `：${note.error_message}` : ''}
          </p>
          <button
            className="btn btn-primary"
            onClick={handleStartCleaning}
            disabled={loading}
          >
            {loading ? '正在触发...' : '重新清洗'}
          </button>
        </div>
      )}

      {/* cleaned 状态：显示统计和重复块操作 */}
      {note.status === 'cleaned' && (
        <>
          {/* 清洗统计摘要 */}
          <div className="cleaning-stats">
            <h4 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>清洗统计</h4>
            <div style={{ display: 'flex', gap: 'var(--space-md)', fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              <span>总分块: {totalChunks}</span>
              <span>重复块: {duplicateBlocks}</span>
              {cleanStats && (
                <>
                  <span>去空行: {cleanStats.empty_lines_removed || 0}</span>
                  <span>去页眉页脚: {cleanStats.headers_footers_removed || 0}</span>
                  <span>去水印: {cleanStats.watermarks_removed || 0}</span>
                </>
              )}
            </div>
          </div>

          {/* 重新清洗按钮 */}
          <div style={{ marginTop: 'var(--space-sm)', marginBottom: 'var(--space-sm)' }}>
            <button
              className="btn btn-secondary"
              style={{ fontSize: '0.8rem' }}
              onClick={handleStartCleaning}
              disabled={loading}
            >
              {loading ? '正在触发...' : '重新清洗'}
            </button>
          </div>

          {/* 重复块列表 */}
          {duplicatesDetail.length > 0 && (
            <div className="duplicate-blocks">
              <h4 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
                重复块（{duplicatesDetail.length} 个）
              </h4>
              {duplicatesDetail.map((dup) => {
                const expanded = expandedIndex === dup.block_index
                const hasContent = !!dup.content || !!dup.original_content
                return (
                  <div key={dup.block_index} className="duplicate-block">
                    <div className="duplicate-block-header">
                      <div className="duplicate-block-info">
                        <span className="duplicate-block-index">块 {dup.block_index}</span>
                        <span className="duplicate-block-similarity">
                          与块 {dup.duplicate_of} 相似度 {(dup.similarity * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="duplicate-block-actions">
                        <button
                          className="btn btn-secondary"
                          style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                          onClick={() => setExpandedIndex(expanded ? null : dup.block_index)}
                          disabled={loading}
                        >
                          {expanded ? '收起内容' : '查看内容'}
                        </button>
                        <button
                          className="btn btn-secondary"
                          style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                          onClick={() => handleRestore(dup.block_index)}
                          disabled={loading}
                        >
                          恢复
                        </button>
                        <button
                          className="btn btn-danger"
                          style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                          onClick={() => handleDelete(dup.block_index)}
                          disabled={loading}
                        >
                          删除
                        </button>
                      </div>
                    </div>

                    {/* 块内容对比（供人工核对重复判断是否准确） */}
                    {expanded && (
                      <div className="duplicate-block-compare">
                        {hasContent ? (
                          <>
                            <div className="duplicate-block-text">
                              <div className="duplicate-block-text-label">
                                保留的块 {dup.duplicate_of}（首次出现）
                              </div>
                              <pre className="duplicate-block-text-content">
                                {dup.original_content || '（内容缺失）'}
                              </pre>
                            </div>
                            <div className="duplicate-block-text">
                              <div className="duplicate-block-text-label">
                                重复的块 {dup.block_index}
                              </div>
                              <pre className="duplicate-block-text-content">
                                {dup.content || '（内容缺失）'}
                              </pre>
                            </div>
                          </>
                        ) : (
                          <p className="duplicate-block-text-empty">
                            该笔记清洗时未保存块内容（旧版本清洗），点击"重新清洗"后即可查看
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {duplicatesDetail.length === 0 && (
            <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem', textAlign: 'center' }}>
              未检测到重复内容
            </p>
          )}
        </>
      )}
    </div>
  )
}
