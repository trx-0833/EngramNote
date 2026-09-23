/**
 * @file 清洗操作面板组件
 * @description 提供笔记清洗相关的操作界面，包括：
 * 1. 触发清洗按钮（converted 状态时显示）
 * 2. 清洗进度提示（cleaning 状态时显示）
 * 3. 重复块列表与操作（恢复/删除）
 * 4. 清洗统计摘要
 */
import { useState } from 'react';
import {
  startCleaning,
  stopCleaning,
  restoreBlock,
  deleteBlock,
  type NoteDetail,
} from '../api/client';
import Icon from './Icon';
import TaskProgress from './TaskProgress';
// 统一确认框（批次 D3）：本文件有**两处**确认（停止清洗 / 删除重复块），
// 各自一个 `useState`，但只有一个 `block_index` 需要记住，所以删除那处用
// `number | null` 承载"打开着呢，等确认的是第几块"（详见 render 段注释）。
import ConfirmDialog from './ConfirmDialog';
// 清洗面板样式（overhaul-plan 5.6）：原 src/styles/cleaning.css 整表迁到这里
import styles from './CleaningPanel.module.css';

interface DuplicateBlock {
  block_index: number;
  duplicate_of: number;
  similarity: number;
  /** 重复块文本内容（旧版本清洗可能缺失，重新清洗后补齐） */
  content?: string;
  /** 被重复的保留块文本内容（旧版本清洗可能缺失） */
  original_content?: string;
}

interface CleaningPanelProps {
  /** 笔记详情 */
  note: NoteDetail;
  /** 清洗状态变化后的回调（刷新笔记数据） */
  onStatusChange: () => void;
  /** 块操作（恢复/删除）进行中回调（true=开始，false=结束），供父组件 suspend 状态轮询 */
  onMutatingChange?: (mutating: boolean) => void;
}

/**
 * 清洗操作面板组件
 *
 * 根据笔记状态显示不同的操作界面：
 * - converted：显示"开始清洗"按钮
 * - cleaning：显示清洗进度提示
 * - cleaned：显示重复块列表和操作按钮
 */
export default function CleaningPanel({
  note,
  onStatusChange,
  onMutatingChange,
}: CleaningPanelProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  /** 当前展开内容对比的重复块索引（null 表示全部收起） */
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
  /** 停止清洗的确认框是否打开（批次 D3：原来是同步的 `confirm()`） */
  const [confirmingStop, setConfirmingStop] = useState(false);
  /**
   * 待删除的重复块索引（`null` = 删除确认框关着）。
   *
   * 这里**不能**用"是否打开 + 另一个 `blockIndex` state"两个变量：
   * 两处 state 分别更新会先渲染出"框开着但不知道该删哪块"的一帧。
   * 也不用 `false` 当"关着"——`block_index` 是数字，用 `-1` 当哨兵值
   * 会让 `confirmDeleteIndex !== null` 这类判断在别的编号下失效。
   */
  const [confirmDeleteIndex, setConfirmDeleteIndex] = useState<number | null>(null);

  /** 触发清洗 */
  async function handleStartCleaning() {
    setLoading(true);
    setError('');
    try {
      await startCleaning(note.id);
      onStatusChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : '触发清洗失败');
    } finally {
      setLoading(false);
    }
  }

  /**
   * 真正执行"停止清洗"（批次 D3：原来这段紧跟在同步的 `confirm()` 之后，
   * 现在由确认框的 `onConfirm` 调用 —— 逐字保留，含 loading 与失败提示）
   */
  async function performStopCleaning() {
    setLoading(true);
    setError('');
    try {
      await stopCleaning(note.id);
      onStatusChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : '停止清洗失败');
    } finally {
      setLoading(false);
    }
  }

  /** 停止清洗：先开确认框，不碰数据 */
  function handleStopCleaning() {
    setConfirmingStop(true);
  }

  /** 恢复重复块 */
  async function handleRestore(blockIndex: number) {
    onMutatingChange?.(true);
    setLoading(true);
    setError('');
    try {
      await restoreBlock(note.id, blockIndex);
      onStatusChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : '恢复失败');
    } finally {
      setLoading(false);
      onMutatingChange?.(false);
    }
  }

  /**
   * 真正执行"删除重复块"（批次 D3：原来这段紧跟在同步的 `confirm()` 之后）
   *
   * `blockIndex` 从确认框那处 state 来 —— 不再是入参：原来 `handleDelete(index)`
   * 是点击时直接调用的，现在中间隔了一次用户点击，索引必须先存起来。
   */
  async function performDelete(blockIndex: number) {
    onMutatingChange?.(true);
    setLoading(true);
    setError('');
    try {
      await deleteBlock(note.id, blockIndex);
      onStatusChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setLoading(false);
      onMutatingChange?.(false);
    }
  }

  /** 点重复块上的「删除」：先开确认框，不碰数据 */
  function handleDelete(blockIndex: number) {
    setConfirmDeleteIndex(blockIndex);
  }

  // 从元数据中提取重复块信息
  const metadata = note.metadata_ as Record<string, unknown> | null;
  const duplicatesDetail = (metadata?.duplicates_detail as DuplicateBlock[]) || [];
  const duplicateBlocks = (metadata?.duplicate_blocks as number) || 0;
  const totalChunks = (metadata?.total_chunks as number) || 0;
  const cleanStats = metadata?.clean_stats as Record<string, number> | null;

  return (
    <div className={styles.cleaningPanel}>
      {/* 错误提示 */}
      {error && (
        <p
          role="alert"
          style={{
            color: 'var(--color-error)',
            fontSize: '0.875rem',
            marginBottom: 'var(--space-sm)',
          }}
        >
          {error}
        </p>
      )}

      {/* converted 状态：显示"开始清洗"按钮 */}
      {note.status === 'converted' && (
        <div style={{ textAlign: 'center', padding: 'var(--space-md)' }}>
          <p
            style={{
              color: 'var(--color-text-secondary)',
              marginBottom: 'var(--space-md)',
              fontSize: '0.875rem',
            }}
          >
            笔记已转换完成，可以开始 AI 清洗
          </p>
          <button className="btn btn-primary" onClick={handleStartCleaning} disabled={loading}>
            {loading ? '正在触发...' : '开始清洗'}
          </button>
        </div>
      )}

      {/* cleaning 状态：显示**真实**进度提示 + 停止按钮（阶段 5.11）
          ⚠️ 这里刻意不显示 TaskProgress 自带的"取消任务"按钮：
          下面的"停止清洗"是**产品级**动作（把笔记标记为 cleaning_failed，
          让任务在阶段边界自行退出），与"取消 Celery 任务"并不相同。
          两个功能不同、外观一样的按钮放在一起，用户无法判断该点哪个。 */}
      {note.status === 'cleaning' && (
        <div style={{ padding: 'var(--space-md)' }}>
          <TaskProgress
            noteId={note.id}
            fallbackText="正在进行 AI 清洗，请稍候..."
            allowCancel={false}
            onCancelled={onStatusChange}
          />
          <div style={{ textAlign: 'center', marginTop: 'var(--space-sm)' }}>
            <button
              className="btn btn-danger"
              style={{ fontSize: '0.8rem' }}
              onClick={handleStopCleaning}
              disabled={loading}
            >
              {loading ? '正在停止...' : '停止清洗'}
            </button>
          </div>
        </div>
      )}

      {/* cleaning_failed 状态：显示错误信息 + 重新清洗按钮 */}
      {note.status === 'cleaning_failed' && (
        <div style={{ textAlign: 'center', padding: 'var(--space-md)' }}>
          <p
            style={{
              color: 'var(--color-error)',
              marginBottom: 'var(--space-sm)',
              fontSize: '0.875rem',
            }}
          >
            清洗失败{note.error_message ? `：${note.error_message}` : ''}
          </p>
          <button className="btn btn-primary" onClick={handleStartCleaning} disabled={loading}>
            {loading ? '正在触发...' : '重新清洗'}
          </button>
        </div>
      )}

      {/* cleaned 状态：显示统计和重复块操作 */}
      {note.status === 'cleaned' && (
        <>
          {/* 清洗统计摘要 */}
          <div className={styles.cleaningStats}>
            {/* `h2` 而不是 `h4`（a11y-audit **F-08** 的真实成因）。
                这一页的大纲是 h1（笔记标题，`NoteDetailHeader`）→ 本区块 → 正文。
                原来「清洗统计」与「重复块」都写成 `h4`，于是 h1 之后直接出现 h4，
                axe 的 `heading-order` 报的就是**这一个节点** —— 它来自页面自己的
                元信息区块，**不是**用户的 Markdown（正文里的标题层级另外算，
                见 docs/a11y-audit.md 里对 F-08 成因的更正）。
                字号 0.875rem / 字重 600 本来就显式钉着，所以**一个像素都没动**
                （与 F-18 / F-14/F-15 / trash / card-detail 的做法逐字相同）。 */}
            <h2 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
              清洗统计
            </h2>
            <div
              style={{
                display: 'flex',
                gap: 'var(--space-md)',
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
              }}
            >
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
            <div className={styles.duplicateBlocks}>
              {/* 与「清洗统计」同级的第二个区块 → 同为 `h2`（同一个 F-08 修复的
                  第二条渲染路径；它们只在重复块非空时渲染）。字数/字重不变。 */}
              <h2
                style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}
              >
                重复块（{duplicatesDetail.length} 个）
              </h2>
              {duplicatesDetail.map((dup) => {
                const expanded = expandedIndex === dup.block_index;
                const hasContent = !!dup.content || !!dup.original_content;
                return (
                  <div key={dup.block_index} className={styles.duplicateBlock}>
                    <div className={styles.duplicateBlockHeader}>
                      <div className={styles.duplicateBlockInfo}>
                        <span className={styles.duplicateBlockIndex}>块 {dup.block_index}</span>
                        <span className={styles.duplicateBlockSimilarity}>
                          与块 {dup.duplicate_of} 相似度 {(dup.similarity * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className={styles.duplicateBlockActions}>
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
                          {/* 批次 B3：「删除」此前是全站纯文字按钮 —— 补 `delete` 图标 */}
                          <Icon name="delete" size={16} />
                          删除
                        </button>
                      </div>
                    </div>

                    {/* 块内容对比（供人工核对重复判断是否准确） */}
                    {expanded && (
                      <div className={styles.duplicateBlockCompare}>
                        {hasContent ? (
                          <>
                            {/* 这一层 div 原本挂着 `duplicate-block-text`，但**全项目
                                14 个样式表都没有定义它** —— 死类名，本轮随迁移删除
                                （与试点轮删 `.feedback-pending` 同一处理）。
                                嵌套保留：删掉它会改变 DOM 层级，那超出"纯搬家"。 */}
                            <div>
                              <div className={styles.duplicateBlockTextLabel}>
                                保留的块 {dup.duplicate_of}（首次出现）
                              </div>
                              <pre className={styles.duplicateBlockTextContent}>
                                {dup.original_content || '（内容缺失）'}
                              </pre>
                            </div>
                            <div>
                              <div className={styles.duplicateBlockTextLabel}>
                                重复的块 {dup.block_index}
                              </div>
                              <pre className={styles.duplicateBlockTextContent}>
                                {dup.content || '（内容缺失）'}
                              </pre>
                            </div>
                          </>
                        ) : (
                          <p className={styles.duplicateBlockTextEmpty}>
                            该笔记清洗时未保存块内容（旧版本清洗），点击"重新清洗"后即可查看
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {duplicatesDetail.length === 0 && (
            <p
              style={{
                color: 'var(--color-text-secondary)',
                fontSize: '0.8rem',
                textAlign: 'center',
              }}
            >
              未检测到重复内容
            </p>
          )}
        </>
      )}

      {/* ── 两处统一确认框（批次 D3）──
          文案逐字保留原来的 `confirm()` 参数。两处的 `onConfirm` 都是
          "先关框、再执行"（见 `ConfirmDialog` 文件头）：原来 `confirm()`
          是同步的，用户点完弹窗立刻消失、按钮才进入 loading，
          "先关后执行"才是逐字保留这个观感。取消则什么都不做。 */}

      {/* ① 停止清洗 */}
      <ConfirmDialog
        open={confirmingStop}
        title="确定停止清洗？"
        message="当前进度将丢失。"
        confirmText="停止清洗"
        danger
        onConfirm={() => {
          setConfirmingStop(false);
          void performStopCleaning();
        }}
        onCancel={() => setConfirmingStop(false)}
      />

      {/* ② 删除重复块。`block_index` 进标题（原来就是插值进 `confirm()` 文案的） */}
      <ConfirmDialog
        open={confirmDeleteIndex !== null}
        title={`确定删除块 ${confirmDeleteIndex ?? ''}？`}
        message="此操作不可恢复。"
        confirmText="删除"
        danger
        onConfirm={() => {
          const blockIndex = confirmDeleteIndex;
          setConfirmDeleteIndex(null);
          if (blockIndex === null) return;
          void performDelete(blockIndex);
        }}
        onCancel={() => setConfirmDeleteIndex(null)}
      />
    </div>
  );
}
