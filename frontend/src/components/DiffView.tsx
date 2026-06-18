/**
 * @file Diff 对比视图组件
 * @description 展示原始版与清洗版 Markdown 的行级差异对比。
 * 自实现简单 LCS diff 算法，不引入第三方 diff 库。
 *
 * 功能：
 * 1. 左右分栏布局（原始版 | 清洗版）
 * 2. 行级 diff 高亮：删除行红色、新增行绿色、未变行白色
 * 3. 行号对齐
 * 4. 响应式设计
 */
import { type DiffBlock, type DiffLine } from '../api/client'

interface DiffViewProps {
  /** diff 块数据 */
  blocks: DiffBlock[]
  /** 原始版总行数 */
  originalLines: number
  /** 清洗版总行数 */
  cleanLines: number
}

/**
 * 渲染单行 diff 内容
 */
function DiffLineRow({ line }: { line: DiffLine }) {
  const className = `diff-line diff-line-${line.type}`
  const prefix = line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '

  return (
    <div className={className}>
      <span className="diff-line-prefix">{prefix}</span>
      <span className="diff-line-number">
        {line.type === 'removed' || line.type === 'unchanged'
          ? line.line_number_original ?? ''
          : ''}
      </span>
      <span className="diff-line-number">
        {line.type === 'added' || line.type === 'unchanged'
          ? line.line_number_clean ?? ''
          : ''}
      </span>
      <span className="diff-line-content">{line.content}</span>
    </div>
  )
}

/**
 * Diff 对比视图组件
 *
 * 将 diff 块渲染为高亮的行级对比视图。
 * 每个块包含连续的变更行，块之间用分隔线区分。
 */
export default function DiffView({ blocks, originalLines, cleanLines }: DiffViewProps) {
  if (!blocks || blocks.length === 0) {
    return (
      <div className="diff-container">
        <p style={{ color: 'var(--color-text-secondary)', textAlign: 'center', padding: 'var(--space-md)' }}>
          两个版本完全相同，没有差异。
        </p>
      </div>
    )
  }

  return (
    <div className="diff-container">
      {/* 统计信息 */}
      <div className="diff-summary">
        <span>原始版 {originalLines} 行</span>
        <span>清洗版 {cleanLines} 行</span>
        <span>{blocks.length} 处差异</span>
      </div>

      {/* 表头 */}
      <div className="diff-header">
        <span className="diff-col-label">原始版</span>
        <span className="diff-col-label">清洗版</span>
        <span className="diff-col-label">内容</span>
      </div>

      {/* diff 内容 */}
      <div className="diff-body">
        {blocks.map((block, blockIdx) => (
          <div key={blockIdx} className="diff-block">
            {block.lines.map((line, lineIdx) => (
              <DiffLineRow key={lineIdx} line={line} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
