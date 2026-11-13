/**
 * @file 问答历史抽屉（docs/qa-history-plan.md §5.2）
 *
 * 抽出组件的理由：`QA.tsx` 已经 500+ 行，而抽屉有自己的一整套内容
 * （列表 / 空态 / 每项操作 / 清空全部 / 保留期提示）。放进页面只会让
 * "对话渲染"与"历史管理"两件事搅在一起。
 *
 * ## 三条与后端对齐的约束
 *
 * 1. **保留期提示必须在抽屉顶部**（计划 §5.4 第 2 句）：用户只有点开这个抽屉
 *    才会想到"删了是不是就没了"，提示放在这里才有用。
 * 2. **删除是逐条 + 清空全部两个动作**：前者用于"这段不想留"，后者用于
 *    "整个主题都不想留"；两者的确认文案不同，因为后者更重。
 * 3. **数字从接口/配置来**：`30 天`（软删宽限）与保留期来自后端契约说明，
 *    这里用文案占位符注入，不在组件里写死。
 */
import { useMemo, useState } from 'react';

import { clearQaSessions, deleteQaSession, type QASessionSummary } from '../../api/qaHistory';
import Dialog from '../../components/Dialog';
import Icon from '../../components/Icon';
import { useTranslation } from '../../i18n';
import { formatDateTime } from '../../utils/datetime';
import styles from './QAHistoryDrawer.module.css';

export interface QAHistoryDrawerProps {
  open: boolean;
  onClose: () => void;
  sessions: QASessionSummary[];
  loading: boolean;
  error?: string;
  /** 当前正在看的会话（高亮） */
  activeSessionId?: string;
  /** 点一条：载入该会话并接着追问 */
  onOpenSession: (sessionId: string) => void;
  /** 删除/清空之后让上层刷新列表 */
  onChanged: () => void;
  /** 保留期（天）与备份周数：由调用方从后端配置读，写死会与配置漂移 */
  retentionDays: number;
  backupWeeks: number;
}

export default function QAHistoryDrawer({
  open,
  onClose,
  sessions,
  loading,
  error,
  activeSessionId,
  onOpenSession,
  onChanged,
  retentionDays,
  backupWeeks,
}: QAHistoryDrawerProps) {
  const { t } = useTranslation();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  const total = sessions.length;

  /** 删除一条：失败就让上层的错误提示接管（这里不吞异常） */
  async function handleDelete(session: QASessionSummary) {
    setBusyId(session.id);
    try {
      await deleteQaSession(session.id);
      onChanged();
    } finally {
      setBusyId(null);
    }
  }

  async function handleClearAll() {
    setClearing(true);
    try {
      await clearQaSessions();
      onChanged();
    } finally {
      setClearing(false);
    }
  }

  const body = useMemo(() => {
    if (loading) return <p className={styles.hint}>{t('shared.loading')}</p>;
    if (error) return <p className={styles.error}>{error}</p>;
    if (total === 0) return <p className={styles.hint}>{t('qa.historyEmpty')}</p>;
    return (
      <ul className={styles.list}>
        {sessions.map((session) => {
          const isActive = session.id === activeSessionId;
          return (
            <li key={session.id} className={styles.item}>
              <button
                type="button"
                className={`${styles.itemMain} ${isActive ? styles.itemActive : ''}`}
                onClick={() => onOpenSession(session.id)}
                aria-current={isActive ? 'true' : undefined}
              >
                <span className={styles.itemTitle}>{session.title || t('qa.historyUntitled')}</span>
                <span className={styles.itemMeta}>
                  {formatDateTime(session.updated_at)} · {session.turn_count}
                </span>
              </button>
              <button
                type="button"
                className={styles.itemDelete}
                onClick={() => handleDelete(session)}
                disabled={busyId === session.id}
                aria-label={t('qa.deleteSession')}
                title={t('qa.deleteSession')}
              >
                <Icon name="delete" size={16} />
              </button>
            </li>
          );
        })}
      </ul>
    );
    // `handleDelete` / `onOpenSession` 是每次渲染新建的闭包，但它们的**行为**只依赖
    // 上面列出的这些值（busyId / sessions / activeSessionId）；
    // 放进依赖只会让 memo 每次都失效，所以这里显式豁免。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, error, total, sessions, activeSessionId, busyId, t]);
  return (
    <Dialog open={open} onClose={onClose} title={t('qa.historyTitle')}>
      {/* 保留期提示（计划 §5.4 第 2 句）：数字来自配置，不写死 */}
      <p className={styles.note}>
        {t('qa.retentionHint', { days: retentionDays, weeks: backupWeeks })}
      </p>

      {body}

      {total > 0 && (
        <div className={styles.footer}>
          <button
            type="button"
            className="btn btn-danger-outline"
            onClick={handleClearAll}
            disabled={clearing}
          >
            {t('qa.clearAll')}
          </button>
        </div>
      )}
    </Dialog>
  );
}
