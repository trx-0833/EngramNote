/**
 * @file 原始版 / 清洗版 / 对比视图 切换控件
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 按钮顺序、类名拼接、禁用条件均与拆分前一致。
 */
import type { ViewMode } from './types';

interface ViewModeTabsProps {
  /** 当前视图模式 */
  viewMode: ViewMode;
  /** 切换视图模式 */
  onViewModeChange: (mode: ViewMode) => void;
  /** 是否可切换到清洗版（无清洗内容时禁用） */
  canShowClean: boolean;
  /** 是否可切换到对比视图（未清洗状态禁用） */
  canShowDiff: boolean;
}

/** 视图模式切换按钮 */
export default function ViewModeTabs({
  viewMode,
  onViewModeChange,
  canShowClean,
  canShowDiff,
}: ViewModeTabsProps) {
  return (
    <div className="segment-control" style={{ marginTop: 'var(--space-sm)' }}>
      <button
        className={`segment-btn ${viewMode === 'original' ? 'segment-btn-active' : ''}`}
        onClick={() => onViewModeChange('original')}
      >
        原始版
      </button>
      <button
        className={`segment-btn ${viewMode === 'clean' ? 'segment-btn-active' : ''}`}
        onClick={() => onViewModeChange('clean')}
        disabled={!canShowClean}
      >
        清洗版
      </button>
      <button
        className={`segment-btn ${viewMode === 'diff' ? 'segment-btn-active' : ''}`}
        onClick={() => onViewModeChange('diff')}
        disabled={!canShowDiff}
      >
        对比视图
      </button>
    </div>
  );
}
