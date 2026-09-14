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
// diff 视图样式（overhaul-plan 5.6）：原 src/styles/diff.css 整表迁到这里
import styles from './DiffView.module.css'

interface DiffViewProps {
  /** diff 块数据 */
  blocks: DiffBlock[]
  /** 原始版总行数 */
  originalLines: number
  /** 清洗版总行数 */
  cleanLines: number
}

/**
 * 行类型 → 模块类名的查表
 *
 * 原来是 `` `diff-line diff-line-${line.type}` `` 拼字符串。类名哈希之后
 * 拼出来的名字不是产物里的类名（`._diffLineRemoved_<hash>`），
 * 于是行高亮会静默失效 —— 而且是**构建期看不出、规则清单也看不出**的那种失效。
 * 写成查表后，类型少一个键 TypeScript 就会报错（`Record<DiffLine['type'], string>`）。
 */
const LINE_TYPE_CLASS: Record<DiffLine['type'], string> = {
  added: styles.diffLineAdded,
  removed: styles.diffLineRemoved,
  unchanged: styles.diffLineUnchanged,
}

/**
 * 渲染单行 diff 内容
 */
function DiffLineRow({ line }: { line: DiffLine }) {
  const className = `${styles.diffLine} ${LINE_TYPE_CLASS[line.type]}`
  const prefix = line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '

  return (
    <div className={className}>
      <span className={styles.diffLinePrefix}>{prefix}</span>
      <span className={styles.diffLineNumber}>
        {line.type === 'removed' || line.type === 'unchanged'
          ? line.line_number_original ?? ''
          : ''}
      </span>
      <span className={styles.diffLineNumber}>
        {line.type === 'added' || line.type === 'unchanged'
          ? line.line_number_clean ?? ''
          : ''}
      </span>
      <span className={styles.diffLineContent}>{line.content}</span>
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
      <div className={styles.diffContainer}>
        <p style={{ color: 'var(--color-text-secondary)', textAlign: 'center', padding: 'var(--space-md)' }}>
          两个版本完全相同，没有差异。
        </p>
      </div>
    )
  }

  return (
    <div className={styles.diffContainer}>
      {/* 统计信息 */}
      <div className={styles.diffSummary}>
        <span>原始版 {originalLines} 行</span>
        <span>清洗版 {cleanLines} 行</span>
        <span>{blocks.length} 处差异</span>
      </div>

      {/* 表头 */}
      <div className={styles.diffHeader}>
        <span className={styles.diffColLabel}>原始版</span>
        <span className={styles.diffColLabel}>清洗版</span>
        <span className={styles.diffColLabel}>内容</span>
      </div>

      {/* diff 内容 */}
      <div className={styles.diffBody}>
        {blocks.map((block, blockIdx) => (
          <div key={blockIdx} className={styles.diffBlock}>
            {block.lines.map((line, lineIdx) => (
              <DiffLineRow key={lineIdx} line={line} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
