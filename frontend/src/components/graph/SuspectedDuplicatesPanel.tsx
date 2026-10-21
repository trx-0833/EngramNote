/**
 * 疑似重复卡片面板（A1 P0-3）
 *
 * ## 它暴露的是什么问题
 *
 * 图谱的「同名闸」规定：标题相同的两张卡**不建边** —— 因为实测同名对的
 * 相似度**普遍 ~1.000**、盲判过的 3 条**全部是真重复**。它们不是"错的边"，
 * 是**重复的卡片**（同一个知识点被反复理解成了多张卡）。
 *
 * 但闸门只做了"不建边"，于是这个数据问题从"图谱里看得见"变成了
 * **"只有日志里看得见"**。这个面板把它变成用户能看见的。
 *
 * ## ⚠️ 只报不处理（已拍板）
 *
 * 合并/删除要另开一轮单独批 —— 它会动 `review_states`（复习历史变孤儿行），
 * 属产品语义变更。所以这里**没有任何操作按钮**，只有清单与说明。
 * 少了这个约束，用户第一反应是"那帮我合并吧"，而那个动作的代价
 * 根本不在这个面板里。
 *
 * ## 为什么是**按需加载**而不是跟统计一起拉
 *
 * 这份清单要读向量并两两算余弦，而统计的其余部分都是计数。
 * 挂在常规轮询上会让每次 `GET /graph/stats` 都付这个代价。
 * 所以它只在用户点开这个面板时拉一次（`useEffect` 依赖 `open`）。
 */
import { useEffect, useState } from 'react';

import { getGraphStats, type SuspectedDuplicatePair } from '../../api/graph';
import { useTranslation } from '../../i18n';
import styles from './Graph.module.css';

/** 加载状态由 `loaded` 派生（见组件内说明），这里只留类型注释 */

export function SuspectedDuplicatesPanel({ open }: { open: boolean }) {
  const { t } = useTranslation();
  /**
   * `loaded` 区分"还没请求过"与"请求完了但结果是 null"。
   *
   * ⚠️ 不能用 `pairs === null` 同时表达这两件事 —— 那会把"正在加载"
   * 与"后端没统计到"混成一个状态，界面上就会先闪一句"暂时无法统计"
   * 再变成"有 N 对"，用户看到的是**自相矛盾**的两次渲染。
   */
  const [loaded, setLoaded] = useState(false);
  const [pairs, setPairs] = useState<SuspectedDuplicatePair[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // ⚠️ 这里**不**同步 `setState('loading')`：在 effect 体里同步 setState
    // 会触发级联渲染（`react-hooks` 的 set-state-in-effect 规则会拦）。
    // "加载中"由 `!loaded` **派生**出来（见下面的渲染分支），
    // 状态只在异步回调里更新。
    getGraphStats({ includeDuplicates: true })
      .then((stats) => {
        if (cancelled) return;
        // `suspected_duplicates` 是 `null` = **没统计到**，**不是**"没有重复"。
        // 这两种状态在界面上必须分开：混起来会让"后端算挂了"看起来像
        // "你的数据很干净" —— 那是假的好消息。
        setPairs(stats.suspected_duplicates ?? null);
        setLoaded(true);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : t('graphPage.loadFailed'));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [open, t]);

  return (
    <div className={styles.graphPanel}>
      <div className={styles.graphPanelTitle}>{t('graphPage.duplicatesTitle')}</div>

      <p className={styles.graphHint}>{t('graphPage.duplicatesHint')}</p>

      {!loaded && <p className={styles.graphHint}>{t('graphPage.loading')}</p>}

      {loaded && error && (
        <p role="alert" className={styles.graphError}>
          {error}
        </p>
      )}

      {/* `null` 且没有错误 ⇒ 后端**没统计到**（它那边算挂了但有兜底）。
          这一支不能并进下面那个"没有重复"的分支 —— 那是假的好消息。 */}
      {loaded && !error && pairs === null && (
        <p className={styles.graphHint}>{t('graphPage.duplicatesUnavailable')}</p>
      )}

      {loaded && !error && pairs !== null && pairs.length === 0 && (
        <p className={styles.graphHint}>{t('graphPage.duplicatesNone')}</p>
      )}

      {loaded && !error && pairs !== null && pairs.length > 0 && (
        <>
          <p className={styles.graphHint}>
            {t('graphPage.duplicatesCount', { count: pairs.length })}
          </p>
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {pairs.map((p) => (
              <li
                key={`${p.card_id_1}-${p.card_id_2}`}
                style={{
                  padding: 'var(--space-xs) 0',
                  borderBottom: '1px solid var(--color-border)',
                  fontSize: '0.8rem',
                }}
              >
                <div style={{ fontWeight: 600 }}>{p.title}</div>
                <div style={{ color: 'var(--color-text-secondary)' }}>
                  {t('graphPage.duplicatesSimilarity', {
                    value: (p.similarity * 100).toFixed(1),
                  })}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* 下一步说明：告诉用户"这事该找谁"，因为本面板**故意**不给操作 */}
      <p className={styles.graphHint} style={{ marginTop: 'var(--space-sm)' }}>
        {t('graphPage.duplicatesNextStep')}
      </p>
    </div>
  );
}
