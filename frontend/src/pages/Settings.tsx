/**
 * @file 设置页（PC）
 *
 * ## 数据流
 *
 * `GET /api/settings` 一次拿完整快照；每个控件的 `PATCH` 只提交自己那一段。
 * 服务端返回合并后的完整快照，前端 `setQueryData` 覆盖缓存 —— 不做
 * "改完再 GET 一次"的补拉，避免两次请求之间读到旧值。
 *
 * ## 联动
 *
 * - 每日复习上限：`review_service` 在 due/submit/stats 三处读取（后端已接线）
 * - 邮件开关/发送时间：`reminder_tasks` 每小时按用户小时过滤（后端已接线）
 * - 外观类偏好：只进 localStorage（`utils/devicePrefs.ts`），不落库
 *
 * 没接线的项（留存率、清洗规则、内容语言等）**不渲染**，避免假开关。
 */
import { useEffect, useMemo, useRef, useState, type FocusEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { exportNotesZip } from '../api/client';
import { getSettings, getSettingsUsage, patchSettings, type SettingsPatchBody, type UserSettings } from '../api/settings';
import { useAuth } from '../contexts/AuthContext';
import { useConfirm } from '../components/ConfirmProvider';
import { useToast } from '../components/Toast';
import { useTranslation } from '../i18n';
import { useDevicePrefs, type FontScale } from '../utils/devicePrefs';
import styles from './Settings.module.css';

const SETTINGS_KEY = ['settings'] as const;
const USAGE_KEY = ['settings', 'usage'] as const;

const SECTIONS = [
  { id: 'study', no: '01', labelKey: 'settings.nav.study' },
  { id: 'reminders', no: '02', labelKey: 'settings.nav.reminders' },
  { id: 'appearance', no: '03', labelKey: 'settings.nav.appearance' },
  { id: 'account', no: '04', labelKey: 'settings.nav.account' },
  { id: 'data', no: '05', labelKey: 'settings.nav.data' },
] as const;

type SectionId = (typeof SECTIONS)[number]['id'];

function mergePatch(current: UserSettings, body: SettingsPatchBody): UserSettings {
  const study = body.study?.daily_review_limit;
  const reminders = body.reminders;
  return {
    ...current,
    study: {
      ...current.study,
      ...(typeof study === 'number' ? { daily_review_limit: study } : {}),
    },
    reminders: {
      ...current.reminders,
      ...(typeof reminders?.email_enabled === 'boolean'
        ? { email_enabled: reminders.email_enabled }
        : {}),
      ...(typeof reminders?.email_hour === 'number'
        ? { email_hour: reminders.email_hour }
        : {}),
    },
  };
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function hours(): number[] {
  return Array.from({ length: 24 }, (_, i) => i);
}

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

export default function Settings() {
  const { t, locale, setLocale } = useTranslation();
  const { logoutAllDevices } = useAuth();
  const { prefs, setPref } = useDevicePrefs();
  const confirm = useConfirm();
  const toast = useToast();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({ queryKey: SETTINGS_KEY, queryFn: getSettings });
  const usageQuery = useQuery({ queryKey: USAGE_KEY, queryFn: getSettingsUsage });

  const [active, setActive] = useState<SectionId>('study');
  const [savedSection, setSavedSection] = useState<SectionId | null>(null);
  const [exporting, setExporting] = useState(false);
  const dailyInputRef = useRef<HTMLInputElement>(null);
  const savedTimer = useRef<number | null>(null);
  const initialHashApplied = useRef(false);
  const spyLocked = useRef(false);

  const mutation = useMutation({
    mutationFn: ({ body }: { section: SectionId; body: SettingsPatchBody }) => patchSettings(body),
    onMutate: async ({ body }) => {
      await queryClient.cancelQueries({ queryKey: SETTINGS_KEY });
      const previous = queryClient.getQueryData<UserSettings>(SETTINGS_KEY);
      if (previous) queryClient.setQueryData(SETTINGS_KEY, mergePatch(previous, body));
      return { previous };
    },
    onError: (error, vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(SETTINGS_KEY, context.previous);
        if (vars.section === 'study' && dailyInputRef.current) {
          dailyInputRef.current.value = String(context.previous.study.daily_review_limit);
        }
      }
      toast.error(
        t('settings.saveFailed'),
        error instanceof Error ? error.message : undefined,
      );
    },
    onSuccess: (next, vars) => {
      queryClient.setQueryData(SETTINGS_KEY, next);
      setSavedSection(vars.section);
      if (savedTimer.current !== null) window.clearTimeout(savedTimer.current);
      savedTimer.current = window.setTimeout(() => setSavedSection(null), 1800);
    },
  });

  useEffect(() => {
    return () => {
      if (savedTimer.current !== null) window.clearTimeout(savedTimer.current);
    };
  }, []);

  // 深链接 `/settings#account`：数据到位后再滚，避免卡片还没挂载就找不到节点。
  // 只应用一次 —— 每次保存都会换一个新的 query data 对象，重复应用会把页面
  // 从用户正在看的组拽回 URL 里的旧锚点。
  useEffect(() => {
    if (!settingsQuery.data || initialHashApplied.current) return;
    const id = window.location.hash.replace('#', '');
    if (!SECTIONS.some((section) => section.id === id)) {
      initialHashApplied.current = true;
      return;
    }
    // 标记放在定时器里：StrictMode 的 mount→cleanup→mount 会清掉第一次的
    // timer，若同步置位，第二次就直接 return，锚点永远不生效。
    const timer = window.setTimeout(() => {
      initialHashApplied.current = true;
      scrollTo(id as SectionId, false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [settingsQuery.data]);

  // 左栏随滚动高亮：与原型一致，用滚动位置而不是 IntersectionObserver
  // （后者在"卡片比视口高"时会把上一组也算进来）。
  useEffect(() => {
    const onScroll = () => {
      // 程序化滚动（点左栏 / 深链接）期间不要抢 active：
      // 否则 smooth scroll 的中间位置会把高亮停在上一组。
      // 锁在用户下一次真实滚动/按键时解除（pointerdown 覆盖拖动滚动条）。
      if (spyLocked.current) return;
      const cards = SECTIONS.map((s) => document.getElementById(`settings-${s.id}`)).filter(
        (el): el is HTMLElement => el !== null,
      );
      if (cards.length === 0) return;
      const line = window.scrollY + 180;
      let current: SectionId = SECTIONS[0].id;
      cards.forEach((card) => {
        if (card.getBoundingClientRect().top + window.scrollY <= line) {
          current = card.id.replace('settings-', '') as SectionId;
        }
      });
      if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) {
        current = SECTIONS[SECTIONS.length - 1].id;
      }
      setActive(current);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  // 用户一旦真正滚动/按键，滚动高亮重新接管；程序化滚动本身不算。
  useEffect(() => {
    const unlock = () => {
      spyLocked.current = false;
    };
    window.addEventListener('pointerdown', unlock);
    window.addEventListener('wheel', unlock, { passive: true });
    window.addEventListener('touchstart', unlock, { passive: true });
    window.addEventListener('keydown', unlock);
    return () => {
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('wheel', unlock);
      window.removeEventListener('touchstart', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, []);

  const usagePercent = useMemo(() => {
    const storage = usageQuery.data?.storage;
    if (!storage || storage.quota_bytes <= 0) return 0;
    return Math.min(100, (storage.used_bytes / storage.quota_bytes) * 100);
  }, [usageQuery.data]);

  function save(section: SectionId, body: SettingsPatchBody) {
    mutation.mutate({ section, body });
  }

  function commitDailyLimit(event: FocusEvent<HTMLInputElement>) {
    if (!settingsQuery.data) return;
    const parsed = Number(event.currentTarget.value);
    const value = Number.isFinite(parsed) ? Math.round(parsed) : NaN;
    if (!Number.isFinite(value) || value < 5 || value > 100) {
      event.currentTarget.value = String(settingsQuery.data.study.daily_review_limit);
      toast.error(t('settings.study.invalidLimit'));
      return;
    }
    event.currentTarget.value = String(value);
    if (value === settingsQuery.data.study.daily_review_limit) return;
    save('study', { study: { daily_review_limit: value } });
  }

  async function handleExport() {
    setExporting(true);
    try {
      const { blob, filename } = await exportNotesZip('clean');
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename ?? 'engramnote-export.zip';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
      } finally {
        URL.revokeObjectURL(url);
      }
      toast.success(t('settings.data.exportDone'));
    } catch (error) {
      toast.error(
        t('settings.data.exportFailed'),
        error instanceof Error ? error.message : undefined,
      );
    } finally {
      setExporting(false);
    }
  }

  async function handleLogoutAll() {
    const ok = await confirm({
      title: t('settings.account.logoutAllConfirm'),
      message: t('settings.account.logoutAllHint'),
      confirmText: t('settings.account.logoutAll'),
      danger: true,
    });
    if (!ok) return;
    await logoutAllDevices();
  }

  function scrollTo(id: SectionId, smooth = true) {
    const card = document.getElementById(`settings-${id}`);
    if (!card) return;
    spyLocked.current = true;
    setActive(id);
    card.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    window.history.replaceState(null, '', `#${id}`);
  }

  if (settingsQuery.isLoading) {
    return (
      <div className={styles.page}>
        <div className={styles.loading} role="status">
          {t('settings.loading')}
        </div>
      </div>
    );
  }

  if (settingsQuery.isError || !settingsQuery.data) {
    return (
      <div className={styles.page}>
        <h1 className={styles.title}>{t('settings.title')}</h1>
        <p className={styles.subtitle}>{t('settings.loadFailed')}</p>
        <button className="btn btn-primary" type="button" onClick={() => settingsQuery.refetch()}>
          {t('settings.retry')}
        </button>
      </div>
    );
  }

  const data = settingsQuery.data;
  const storage = usageQuery.data?.storage;

  return (
    <div className={styles.page}>
      <header className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>PREFERENCES</div>
          <h1 className={styles.title}>{t('settings.title')}</h1>
          <p className={styles.subtitle}>{t('settings.subtitle')}</p>
        </div>
      </header>

      <div className={styles.layout}>
        <nav className={styles.rail} aria-label={t('settings.navLabel')}>
          <div className={styles.railLabel}>{t('settings.navLabel')}</div>
          {SECTIONS.map((section) => (
            <button
              key={section.id}
              type="button"
              className={`${styles.railItem} ${active === section.id ? styles.railItemActive : ''}`}
              onClick={() => scrollTo(section.id)}
              aria-current={active === section.id ? 'page' : undefined}
            >
              <i>{section.no}</i>
              <span>{t(section.labelKey)}</span>
            </button>
          ))}
        </nav>

        <div className={styles.panel}>
          <section
            id="settings-study"
            className={`card ${styles.card}`}
            aria-labelledby="settings-study-title"
          >
            <SectionHeader
              id="settings-study-title"
              no="01"
              title={t('settings.study.title')}
              desc={t('settings.study.desc')}
              saved={savedSection === 'study'}
              saving={mutation.isPending && mutation.variables?.section === 'study'}
            />
            <div className={styles.rows}>
              <div className={styles.row}>
                <div>
                  <label className={styles.rowLabel} htmlFor="daily-review-limit">
                    {t('settings.study.daily')}
                  </label>
                  <p className={styles.hint} id="daily-review-limit-hint">
                    {t('settings.study.dailyHint')}
                  </p>
                </div>
                <div className={styles.control}>
                  <div className={styles.number}>
                    <input
                      id="daily-review-limit"
                      ref={dailyInputRef}
                      type="number"
                      min={5}
                      max={100}
                      step={1}
                      defaultValue={data.study.daily_review_limit}
                      onBlur={commitDailyLimit}
                      aria-describedby="daily-review-limit-hint"
                    />
                    <span className={styles.unit}>{t('settings.study.unit')}</span>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section
            id="settings-reminders"
            className={`card ${styles.card}`}
            aria-labelledby="settings-reminders-title"
          >
            <SectionHeader
              id="settings-reminders-title"
              no="02"
              title={t('settings.reminders.title')}
              desc={t('settings.reminders.desc')}
              saved={savedSection === 'reminders'}
              saving={mutation.isPending && mutation.variables?.section === 'reminders'}
            />
            <div className={styles.rows}>
              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.reminders.email')}</span>
                  <p className={styles.hint}>
                    {data.reminders.email_configured
                      ? t('settings.reminders.emailHint')
                      : t('settings.reminders.emailUnavailable')}
                  </p>
                </div>
                <div className={styles.control}>
                  <Toggle
                    label={t('settings.reminders.email')}
                    checked={data.reminders.email_enabled}
                    disabled={!data.reminders.email_configured && !data.reminders.email_enabled}
                    onChange={(checked) =>
                      save('reminders', { reminders: { email_enabled: checked } })
                    }
                  />
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <label className={styles.rowLabel} htmlFor="email-hour">
                    {t('settings.reminders.emailTime')}
                  </label>
                  <p className={styles.hint}>{t('settings.reminders.emailTimeHint')}</p>
                </div>
                <div className={styles.control}>
                  <select
                    id="email-hour"
                    value={data.reminders.email_hour}
                    onChange={(event) =>
                      save('reminders', { reminders: { email_hour: Number(event.target.value) } })
                    }
                  >
                    {hours().map((hour) => (
                      <option key={hour} value={hour}>
                        {hourLabel(hour)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.reminders.desktop')}</span>
                  <p className={styles.hint}>{t('settings.reminders.desktopHint')}</p>
                </div>
                <div className={styles.control}>
                  <Toggle
                    label={t('settings.reminders.desktop')}
                    checked={prefs.desktopNotifications}
                    onChange={(checked) => setPref('desktopNotifications', checked)}
                  />
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.reminders.quiet')}</span>
                  <p className={styles.hint}>{t('settings.reminders.quietHint')}</p>
                </div>
                <div className={styles.control}>
                  <div className={styles.quiet}>
                    <select
                      aria-label={t('settings.reminders.quietStart')}
                      value={prefs.quietStart}
                      onChange={(event) => setPref('quietStart', Number(event.target.value))}
                    >
                      {hours().map((hour) => (
                        <option key={hour} value={hour}>
                          {hourLabel(hour)}
                        </option>
                      ))}
                    </select>
                    <span>{t('settings.reminders.to')}</span>
                    <select
                      aria-label={t('settings.reminders.quietEnd')}
                      value={prefs.quietEnd}
                      onChange={(event) => setPref('quietEnd', Number(event.target.value))}
                    >
                      {hours().map((hour) => (
                        <option key={hour} value={hour}>
                          {hourLabel(hour)}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section
            id="settings-appearance"
            className={`card ${styles.card}`}
            aria-labelledby="settings-appearance-title"
          >
            <SectionHeader
              id="settings-appearance-title"
              no="03"
              title={t('settings.appearance.title')}
              desc={t('settings.appearance.desc')}
              saved={savedSection === 'appearance'}
              saving={false}
            />
            <div className={styles.rows}>
              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.appearance.language')}</span>
                  <p className={styles.hint}>{t('settings.appearance.languageHint')}</p>
                </div>
                <div className={styles.control}>
                  <div className={styles.seg} role="group" aria-label={t('settings.appearance.language')}>
                    <button
                      type="button"
                      className={locale === 'zh-CN' ? styles.segActive : ''}
                      aria-pressed={locale === 'zh-CN'}
                      onClick={() => setLocale('zh-CN')}
                    >
                      中文
                    </button>
                    <button
                      type="button"
                      className={locale === 'en' ? styles.segActive : ''}
                      aria-pressed={locale === 'en'}
                      onClick={() => setLocale('en')}
                    >
                      English
                    </button>
                  </div>
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.appearance.font')}</span>
                  <p className={styles.hint}>{t('settings.appearance.fontHint')}</p>
                </div>
                <div className={styles.control}>
                  <div className={styles.seg} role="group" aria-label={t('settings.appearance.font')}>
                    {(['sm', 'md', 'lg'] as FontScale[]).map((scale) => (
                      <button
                        key={scale}
                        type="button"
                        className={prefs.fontScale === scale ? styles.segActive : ''}
                        aria-pressed={prefs.fontScale === scale}
                        onClick={() => setPref('fontScale', scale)}
                      >
                        {t(`settings.appearance.font_${scale}`)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.appearance.reduceMotion')}</span>
                  <p className={styles.hint}>{t('settings.appearance.reduceMotionHint')}</p>
                </div>
                <div className={styles.control}>
                  <Toggle
                    label={t('settings.appearance.reduceMotion')}
                    checked={prefs.reduceMotion}
                    onChange={(checked) => setPref('reduceMotion', checked)}
                  />
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.appearance.adhd')}</span>
                  <p className={styles.hint}>{t('settings.appearance.adhdHint')}</p>
                </div>
                <div className={styles.control}>
                  <Toggle
                    label={t('settings.appearance.adhd')}
                    checked={prefs.adhdDefault}
                    onChange={(checked) => setPref('adhdDefault', checked)}
                  />
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.appearance.sidebar')}</span>
                  <p className={styles.hint}>{t('settings.appearance.sidebarHint')}</p>
                </div>
                <div className={styles.control}>
                  <Toggle
                    label={t('settings.appearance.sidebar')}
                    checked={prefs.sidebarCollapsed}
                    onChange={(checked) => setPref('sidebarCollapsed', checked)}
                  />
                </div>
              </div>
            </div>
          </section>

          <section
            id="settings-account"
            className={`card ${styles.card}`}
            aria-labelledby="settings-account-title"
          >
            <SectionHeader
              id="settings-account-title"
              no="04"
              title={t('settings.account.title')}
              desc={t('settings.account.desc')}
              saved={false}
              saving={false}
            />
            <div className={styles.rows}>
              <ReadonlyRow label={t('settings.account.email')} value={data.account.email} />
              <ReadonlyRow label={t('settings.account.username')} value={data.account.username} />
              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.account.sessions')}</span>
                  <p className={styles.hint}>{t('settings.account.sessionsHint')}</p>
                </div>
                <div className={styles.control}>
                  <button className="btn btn-danger-outline" type="button" onClick={handleLogoutAll}>
                    {t('settings.account.logoutAll')}
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section
            id="settings-data"
            className={`card ${styles.card}`}
            aria-labelledby="settings-data-title"
          >
            <SectionHeader
              id="settings-data-title"
              no="05"
              title={t('settings.data.title')}
              desc={t('settings.data.desc')}
              saved={false}
              saving={false}
            />
            <div className={styles.rows}>
              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.data.storage')}</span>
                  <p className={styles.hint}>{t('settings.data.storageHint')}</p>
                </div>
                <div className={styles.control}>
                  <div className={styles.progressBlock}>
                    <div
                      className={styles.progress}
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(usagePercent)}
                      aria-label={t('settings.data.storage')}
                    >
                      <span style={{ width: `${usagePercent}%` }} />
                    </div>
                    <span className={styles.progressText}>
                      {storage
                        ? `${formatBytes(storage.used_bytes)} / ${
                            storage.quota_bytes > 0 ? formatBytes(storage.quota_bytes) : '∞'
                          }`
                        : t('settings.data.loading')}
                    </span>
                  </div>
                </div>
              </div>

              <div className={styles.row}>
                <div>
                  <span className={styles.rowLabel}>{t('settings.data.export')}</span>
                  <p className={styles.hint}>{t('settings.data.exportHint')}</p>
                </div>
                <div className={styles.control}>
                  <button
                    className="btn btn-primary"
                    type="button"
                    onClick={handleExport}
                    disabled={exporting}
                  >
                    {exporting ? t('settings.data.exporting') : t('settings.data.exportAction')}
                  </button>
                </div>
              </div>

              <ReadonlyRow label={t('settings.data.version')} value={data.about.app_version} />
            </div>
          </section>
        </div>
      </div>

      <footer className={styles.footer}>
        <span>{t('settings.footer')}</span>
        <span>{t('settings.version', { version: data.about.app_version })}</span>
      </footer>
    </div>
  );
}

function SectionHeader({
  id,
  no,
  title,
  desc,
  saved,
  saving,
}: {
  id: string;
  no: string;
  title: string;
  desc: string;
  saved: boolean;
  saving: boolean;
}) {
  const { t } = useTranslation();
  return (
    <header className={styles.sectionHead}>
      <div>
        <div className={styles.sectionNo}>{no}</div>
        <h2 id={id} className={styles.sectionTitle}>
          {title}
        </h2>
        <p className={styles.sectionDesc}>{desc}</p>
      </div>
      <div
        className={`${styles.status} ${saved ? styles.statusSaved : ''}`}
        role="status"
        aria-live="polite"
      >
        {saving ? t('settings.saving') : saved ? t('settings.saved') : ''}
      </div>
    </header>
  );
}

function Toggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={styles.switch}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className={styles.switchTrack}>
        <span className={styles.switchThumb} />
      </span>
    </label>
  );
}

function ReadonlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.row}>
      <div>
        <span className={styles.rowLabel}>{label}</span>
      </div>
      <div className={styles.control}>
        <span className={styles.readonly} title={value}>
          {value}
        </span>
      </div>
    </div>
  );
}
