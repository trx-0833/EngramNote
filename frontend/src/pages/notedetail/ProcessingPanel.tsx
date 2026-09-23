/**
 * @file 转换/上传中状态面板
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 展示**真实**进度与阶段名，并可取消任务（阶段 5.11）。
 * 改造前这里只有一句"正在转换中，请稍候..."，而任务侧其实一直在
 * 上报 progress/stage（后端从阶段 1′ 起就有这个契约），拆分不得回退。
 */
import TaskProgress from '../../components/TaskProgress';

interface ProcessingPanelProps {
  /** 笔记 ID（任务进度按笔记维度查询） */
  noteId: string;
  /** 笔记状态：uploading 显示上传文案，其余显示转换文案 */
  status: string;
  /** 取消任务后刷新笔记数据 */
  onCancelled: () => void;
}

/** 转换中 / 上传中的进度面板 */
export default function ProcessingPanel({ noteId, status, onCancelled }: ProcessingPanelProps) {
  return (
    <div className="card" style={{ padding: 'var(--space-xl)' }}>
      <TaskProgress
        noteId={noteId}
        fallbackText={status === 'uploading' ? '正在上传...' : '正在转换中，请稍候...'}
        onCancelled={onCancelled}
      />
    </div>
  );
}
