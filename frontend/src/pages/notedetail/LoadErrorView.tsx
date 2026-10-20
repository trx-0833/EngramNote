/**
 * @file 加载失败 / 笔记不存在 视图
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 文案为 `error || '笔记不存在'`（走 `t('noteDetail.notFound')`），返回按钮跳到 /notes。
 */
import ErrorDisplay from '../../components/ErrorDisplay';
import { useTranslation } from '../../i18n';

interface LoadErrorViewProps {
  /** 具体错误信息（为空则显示 `t('noteDetail.notFound')` 的文案） */
  error: string;
  /** 返回笔记列表 */
  onBack: () => void;
}

/** 错误或笔记不存在状态 */
export default function LoadErrorView({ error, onBack }: LoadErrorViewProps) {
  const { t } = useTranslation();
  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <ErrorDisplay message={error || t('noteDetail.notFound')} />
      <button className="btn btn-secondary" onClick={onBack}>
        {t('noteDetail.backToList')}
      </button>
    </div>
  );
}
