/**
 * @file 侧边栏组件
 * @description Notion 式左侧边栏，分组导航，支持折叠/展开
 * 视觉重构：替代原有顶部 Navbar
 *
 * 移动端（overhaul-plan 5.10）：≤768px 时侧边栏变抽屉，由 App.tsx 的汉堡按钮打开。
 * 本组件在这里补两件手机上必须有的东西：
 * 1. 抽屉内的「关闭」按钮 —— 遮罩和"点条目自动关闭"都在，但手指够不到遮罩边缘时
 *    （大屏手机上抽屉占 86vw）需要一个明确的关闭入口；
 * 2. 打开时锁住页面滚动 —— 否则在遮罩上滑动会把背后的页面滚走，
 *    关掉抽屉后用户会发现自己被带到别处了。
 */
import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
// i18n（阶段 0 起）：`t()` 取文案，`locale` / `setLocale` 驱动语言切换器。
// `MessageKey` 用于 `NavItem.labelKey` —— 导航文案存**键名**不存文案
import { useTranslation, type MessageKey } from '../i18n';
// 批次 B2：图标从 Unicode 码点改为 `Icon` 组件（唯一出口，见 visual-design-spec §4.2）
import Icon, { type IconName } from './Icon';
// 侧边栏（含移动端抽屉）的类名归模块所有（overhaul-plan 5.6 序 9）：
// `layout.css` 的整节 + `responsive.css` 里命中同一批类名的窄屏规则一起搬了进来
// —— 类名哈希后留在补丁层里的选择器会永远选不中，见 Sidebar.module.css 文件头
import styles from './Sidebar.module.css';

/** 一个导航项。`icon` 是**语义名**（`IconName` 联合类型 —— 拼错在编译期就报错） */
interface NavItem {
  path: string;
  /**
   * 文案**键名**，不是文案本身（i18n 阶段 1）
   *
   * ⚠️ 这里刻意**不存字符串文案**：存了的话，切语言时这份模块级常量
   * 不会跟着变（它在模块加载时就固化了），于是导航永远是加载时的语言。
   * 存键名、渲染时 `t(labelKey)` 才对。
   */
  labelKey: MessageKey;
  icon: IconName;
}

/**
 * 导航分组定义
 *
 * **批次 B2**：`icon` 原来是 Unicode 码点字面量（`\u2302` ⌂、`\u2618` ☘、
 * `\u25B7` ▷、`\u25A3` ▣、`\u25C9` ◉、`\u25C8` ◈、`\u25CE` ◎ …）。
 * 那串字符的问题不是"不好看"，而是**跨平台不可控**：
 * `\u2753` ❓ 与 `\u2611` ☑ 在某些系统上渲染成**彩色 emoji**，与其余单色符号不同源；
 * `▷ ▣ ◉ ◈ ◎` 五个几何图形在 16px 下彼此几乎分不清，语义完全靠旁边的文字兜；
 * `\u2618` ☘ 是三叶草，与"今日学习"毫无关系。
 * 现在换成自绘图标（24 网格 / 线宽 1.5 / currentColor），尺寸与基线由 `Icon` 统一保证。
 *
 * **i18n 阶段 1**：`title` / `label` 从**文案**改成**键名**（`MessageKey`）——
 * 渲染时才 `t()`。理由见 `NavItem.labelKey` 的注释。
 */
const NAV_SECTIONS: { titleKey: MessageKey | null; items: NavItem[] }[] = [
  {
    // 第一组没有分组标题（仪表盘独立成组）
    titleKey: null,
    items: [{ path: '/', labelKey: 'nav.dashboard', icon: 'dashboard' }],
  },
  {
    titleKey: 'nav.sectionLearning',
    items: [
      { path: '/today', labelKey: 'nav.today', icon: 'today' },
      { path: '/review/cards', labelKey: 'nav.reviewCards', icon: 'review-cards' },
      { path: '/daily', labelKey: 'nav.daily', icon: 'daily' },
      { path: '/projects', labelKey: 'nav.projects', icon: 'projects' },
      { path: '/assessment', labelKey: 'nav.assessment', icon: 'assessment' },
      { path: '/goals', labelKey: 'nav.goals', icon: 'goals' },
    ],
  },
  {
    titleKey: 'nav.sectionNotes',
    items: [
      { path: '/notes', labelKey: 'nav.notes', icon: 'notes' },
      { path: '/trash', labelKey: 'nav.trash', icon: 'trash' },
    ],
  },
  {
    titleKey: 'nav.sectionKnowledge',
    items: [
      { path: '/cards', labelKey: 'nav.cards', icon: 'cards' },
      { path: '/graph', labelKey: 'nav.graph', icon: 'graph' },
      { path: '/qa', labelKey: 'nav.qa', icon: 'qa' },
      { path: '/questions', labelKey: 'nav.questions', icon: 'questions' },
    ],
  },
];

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export default function Sidebar({
  collapsed,
  onToggleCollapse,
  mobileOpen,
  onMobileClose,
}: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { logout } = useAuth();
  const { t, locale, setLocale } = useTranslation();

  // 抽屉打开期间锁住 body 滚动（样式见 Sidebar.module.css 的 `body.sidebarOpenLock`）。
  // 放在 effect 里而不是渲染期，是为了让"打开/关闭/卸载"三种路径都必然解绑 ——
  // 直接在渲染里 add 会在组件卸载时把锁留在 body 上，整页再也滚不动。
  // 类名从模块取（哈希后是 `_sidebarOpenLock_hash`）：样式表侧的 `body.sidebarOpenLock`
  // 用的是同一个模块类名，两边不可能分叉。
  useEffect(() => {
    if (!mobileOpen) return;
    document.body.classList.add(styles.sidebarOpenLock);
    return () => document.body.classList.remove(styles.sidebarOpenLock);
  }, [mobileOpen]);

  function handleNav(path: string) {
    navigate(path);
    onMobileClose();
  }

  function isActive(path: string) {
    if (path === '/') return location.pathname === '/';
    return location.pathname.startsWith(path);
  }

  const sidebarClass =
    `${styles.sidebar}` +
    `${collapsed ? ` ${styles.sidebarCollapsed}` : ''}` +
    `${mobileOpen ? ` ${styles.sidebarMobileOpen}` : ''}`;

  return (
    <>
      {/* 移动端遮罩 */}
      {mobileOpen && <div className={styles.sidebarOverlay} onClick={onMobileClose} />}

      <nav className={sidebarClass} role="navigation" aria-label={t('nav.mainNav')}>
        {/* 头部：Logo + 折叠按钮（桌面）/ 关闭按钮（移动抽屉） */}
        <div className={styles.sidebarHeader}>
          <button className={styles.sidebarLogo} onClick={() => handleNav('/')}>
            {collapsed ? 'E' : 'EngramNote'}
          </button>
          <button
            className={styles.sidebarCollapseBtn}
            onClick={onToggleCollapse}
            aria-label={collapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
          >
            {collapsed ? '\u00BB' : '\u00AB'}
          </button>
          {/* 仅移动端抽屉打开时出现：手机上抽屉占 86vw，手指够不到遮罩边缘时
              需要一个明确的关闭入口（显示/隐藏由 mobileOpen 决定，见本文件顶部说明） */}
          {mobileOpen && (
            <button
              className={styles.sidebarMobileClose}
              onClick={onMobileClose}
              aria-label={t('nav.closeMenu')}
            >
              {'\u2715'}
            </button>
          )}
        </div>

        {/* 导航分组 */}
        <div className={styles.sidebarBody}>
          {NAV_SECTIONS.map((section) => (
            /* ⚠️ `key` 用 `titleKey` 而不是 `t(titleKey)`：键名在两种语言下都稳定，
               而译文可能重复（那时 React 会因 key 冲突丢掉一组）。 */
            <div key={section.titleKey ?? 'home'} className={styles.sidebarSection}>
              {section.titleKey && (
                <div className={styles.sidebarSectionTitle}>{t(section.titleKey)}</div>
              )}
              {section.items.map((item) => (
                /* 每行包一层 div：行内快捷操作（笔记分组的「上传资料」）
                   必须是导航按钮的**兄弟**节点 —— 把一个 <button> 嵌进
                   另一个 <button> 是非法 HTML（React 会报 validateDOMNesting），
                   键盘 Tab 也只会落在外层那颗按钮上。
                   行本身是定位上下文，快捷按钮的位置由模块里的
                   .sidebarItemAction 绝对定位钉在右端，视觉与嵌套时一致；
                   两者不再是父子，点击也就不需要 stopPropagation 了。
                   （批次 B3：那颗按钮里的 `+` 换成了 `upload` 图标。） */
                <div key={item.path} className={styles.sidebarItemRow}>
                  <button
                    className={`${styles.sidebarItem}${isActive(item.path) ? ` ${styles.sidebarItemActive}` : ''}`}
                    onClick={() => handleNav(item.path)}
                  >
                    <span className={styles.sidebarItemIcon}>
                      <Icon name={item.icon} size={20} />
                    </span>
                    <span className={styles.sidebarItemLabel}>{t(item.labelKey)}</span>
                  </button>
                  {/* 上传快捷入口在笔记分组 */}
                  {item.path === '/notes' && !collapsed && (
                    <button
                      className={styles.sidebarItemAction}
                      onClick={() => handleNav('/upload')}
                      aria-label={t('nav.uploadAction')}
                    >
                      {/* 批次 B3：ASCII `+` 换 `<Icon name="upload" />` ——
                          这个按钮的动作是「上传资料」（它就是 aria-label），
                          `+` 只是"加东西"的权宜字形；语义名按动作取，
                          所以不并到 `add`（那是"新建一条记录"）。 */}
                      <Icon name="upload" size={16} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* 底部：语言切换 + 退出 */}
        <div className={styles.sidebarFooter}>
          {/* 语言切换（i18n 阶段 0）。
              ## 为什么放在这里
              侧边栏底部是全站唯一"属于当前用户、与页面内容无关"的位置，
              语言偏好正是这种设置。没有独立的设置页（全仓没有 Setting/Profile 页），
              为它新开一页不划算。
              ## 为什么用 `.segment-control` / `.segment-btn`
              这两个是**既有全局类**（`styles/learning.css:61`），
              视图切换与学习评估页在用 —— 复用它们等于"语言切换"与
              "视图切换"是同一类控件（都是二选一/多选一的模式切换），
              而且**没有新增任何样式**。
              ## 为什么两个语言名都不翻译
              见 `messages.zh-CN.ts` 的 `languageZh` / `languageEn` 说明：
              语言名是**自指**的 —— 一个只懂英文的用户在中文界面里找
              "English" 时，把它译成"英语"反而更难找。
              ## 可访问名
              外层 `role="group"` + `aria-label`（"语言"/"Language"），
              读屏会读成"语言 分组，中文，按钮，已按下"。
              两个按钮用 `aria-pressed` 表达选中态，而不是只靠颜色。 */}
          <div className={styles.sidebarLang} role="group" aria-label={t('common.language')}>
            <span className="segment-control">
              <button
                type="button"
                className={`segment-btn ${locale === 'zh-CN' ? 'segment-btn-active' : ''}`}
                onClick={() => setLocale('zh-CN')}
                aria-pressed={locale === 'zh-CN'}
              >
                {t('common.languageZh')}
              </button>
              <button
                type="button"
                className={`segment-btn ${locale === 'en' ? 'segment-btn-active' : ''}`}
                onClick={() => setLocale('en')}
                aria-pressed={locale === 'en'}
              >
                {t('common.languageEn')}
              </button>
            </span>
          </div>

          <button className={styles.sidebarItem} onClick={logout}>
            <span className={styles.sidebarItemIcon}>
              <Icon name="logout" size={20} />
            </span>
            <span className={styles.sidebarItemLabel}>{t('nav.logout')}</span>
          </button>
        </div>
      </nav>
    </>
  );
}
