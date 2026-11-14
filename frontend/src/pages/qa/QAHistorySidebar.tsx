import { useTranslation } from '../../i18n';
import type { QASessionSummary } from '../../api/qaHistory';
import { groupSessions } from './sessionGroups';
import styles from './QAHistorySidebar.module.css';

export interface QAHistorySidebarProps {
  sessions: QASessionSummary[];
  loading: boolean;
  error?: string;
  activeSessionId?: string;
  /** 搜索词（受控）；后端 `keyword` 已做转义与长度校验 */
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  onOpenSession: (sessionId: string) => void;
  onNewChat: () => void;
  /**
   * 「管理」= 打开现有的历史抽屉
   *
   * 阶段 1 里侧栏只做**浏览与切换**：删除/清空涉及确认框与"删掉当前会话后清 `?s=`"
   * 那一整套逻辑，已经被 `QAHistoryDrawer` 的 20 多条用例盯着。与其复制一份
   * （两份迟早分叉），不如让侧栏把"管理"交给抽屉；阶段 2 做重命名时再把行内操作搬来。
   */
  onOpenManage: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  /** 服务端总数：与 `sessions.length` 不等时说明只显示了一页 */
  total: number;
}

const GROUP_LABEL = {
  today: 'qa.groupToday',
  week: 'qa.groupWeek',
  month: 'qa.groupMonth',
  older: 'qa.groupOlder',
} as const;

/**
 * 左侧常驻会话栏（DeepSeek 网页版范式，宽屏用）
 *
 * 与抽屉的分工：
 * - **宽屏**：这里常驻，随时切换会话（不用先点「历史」再点一条）；
 * - **窄屏**：CSS 隐藏本组件，`PageHeader` 上的「历史」按钮打开抽屉。
 *   两者读同一份 `useQuery` 结果，不存在两套状态。
 *
 * 无障碍：整块是 `<nav aria-label>`；折叠按钮带 `aria-expanded` / `aria-controls`；
 * 当前会话用 `aria-current="page"` 标注；搜索框有显式的 `<label>`（不是只靠 placeholder）。
 */
export default function QAHistorySidebar({
  sessions,
  loading,
  error,
  activeSessionId,
  keyword,
  onKeywordChange,
  onOpenSession,
  onNewChat,
  onOpenManage,
  collapsed,
  onToggleCollapsed,
  total,
}: QAHistorySidebarProps) {
  const { t } = useTranslation();
  const groups = groupSessions(sessions);

  return (
    <nav
      className={`${styles.sidebar} ${collapsed ? styles.collapsed : ''}`}
      aria-label={t('qa.historyTitle')}
      id="qa-history-sidebar"
    >
      <div className={styles.head}>
        <button
          type="button"
          className={`btn btn-secondary ${styles.newButton}`}
          onClick={onNewChat}
          // 展开时有可见文字，就不再叠 `aria-label`：否则可访问名会与页头那个同名按钮
          // 撞在一起（测试与读屏都分不清"两个新建对话"到底点哪个）。
          // `＋` 也不加 `aria-hidden`：可访问名因此是「＋ 新建对话」，
          // 与页头的「新建对话」天然区分（读屏念出来仍可懂）。
          aria-label={collapsed ? t('qa.newChat') : undefined}
        >
          <span>＋</span>
          {!collapsed && <span>{t('qa.newChat')}</span>}
        </button>
        <button
          type="button"
          className={`btn btn-ghost ${styles.collapse}`}
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          aria-controls="qa-history-sidebar"
          aria-label={t(collapsed ? 'qa.sidebarExpand' : 'qa.sidebarCollapse')}
          title={t(collapsed ? 'qa.sidebarExpand' : 'qa.sidebarCollapse')}
        >
          <span aria-hidden="true">{collapsed ? '»' : '«'}</span>
        </button>
      </div>

      {!collapsed && (
        <>
          <div className={styles.search}>
            <label className={styles.searchLabel} htmlFor="qa-sidebar-search">
              {t('qa.searchLabel')}
            </label>
            <input
              id="qa-sidebar-search"
              type="search"
              className="input"
              placeholder={t('qa.searchPlaceholder')}
              value={keyword}
              onChange={(e) => onKeywordChange(e.target.value)}
              maxLength={100}
            />
          </div>

          <div className={styles.list}>
            {loading && <p className={styles.hint}>{t('qa.historyLoading')}</p>}
            {!loading && error && <p className={`${styles.hint} ${styles.hintError}`}>{error}</p>}
            {!loading && !error && groups.length === 0 && (
              <p className={styles.hint}>
                {keyword ? t('qa.searchNoMatch') : t('qa.historyEmpty')}
              </p>
            )}

            {groups.map((group) => (
              <section key={group.key} className={styles.group}>
                <h2 className={styles.groupTitle}>{t(GROUP_LABEL[group.key])}</h2>
                <ul className={styles.items}>
                  {group.items.map((session) => {
                    const active = session.id === activeSessionId;
                    const label = session.title || session.last_question || t('qa.untitledSession');
                    return (
                      <li key={session.id}>
                        <button
                          type="button"
                          className={`${styles.item} ${active ? styles.itemActive : ''}`}
                          onClick={() => onOpenSession(session.id)}
                          aria-current={active ? 'page' : undefined}
                          title={label}
                        >
                          <span className={styles.itemTitle}>{label}</span>
                          <span className={styles.itemMeta}>
                            {t('qa.turnsCount', { count: session.turn_count })}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>

          <div className={styles.foot}>
            {total > sessions.length && (
              <p className={styles.hint}>
                {t('qa.sidebarTruncated', { shown: sessions.length, total })}
              </p>
            )}
            <button type="button" className="btn btn-ghost" onClick={onOpenManage}>
              {t('qa.manageHistory')}
            </button>
          </div>
        </>
      )}
    </nav>
  );
}
