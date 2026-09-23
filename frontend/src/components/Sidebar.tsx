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
// 批次 B2：图标从 Unicode 码点改为 `Icon` 组件（唯一出口，见 visual-design-spec §4.2）
import Icon, { type IconName } from './Icon';
// 侧边栏（含移动端抽屉）的类名归模块所有（overhaul-plan 5.6 序 9）：
// `layout.css` 的整节 + `responsive.css` 里命中同一批类名的窄屏规则一起搬了进来
// —— 类名哈希后留在补丁层里的选择器会永远选不中，见 Sidebar.module.css 文件头
import styles from './Sidebar.module.css';

/** 一个导航项。`icon` 是**语义名**（`IconName` 联合类型 —— 拼错在编译期就报错） */
interface NavItem {
  path: string;
  label: string;
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
 */
const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: '',
    items: [{ path: '/', label: '仪表盘', icon: 'dashboard' }],
  },
  {
    title: '学习',
    items: [
      { path: '/today', label: '今日学习', icon: 'today' },
      { path: '/review/cards', label: '卡片复习', icon: 'review-cards' },
      { path: '/daily', label: '今日资料', icon: 'daily' },
      { path: '/projects', label: '项目', icon: 'projects' },
      { path: '/assessment', label: '学习评估', icon: 'assessment' },
      { path: '/goals', label: '学习目标', icon: 'goals' },
    ],
  },
  {
    title: '笔记',
    items: [
      { path: '/notes', label: '笔记列表', icon: 'notes' },
      { path: '/trash', label: '回收站', icon: 'trash' },
    ],
  },
  {
    title: '知识',
    items: [
      { path: '/cards', label: '知识卡片', icon: 'cards' },
      { path: '/graph', label: '知识图谱', icon: 'graph' },
      { path: '/qa', label: '问答', icon: 'qa' },
      { path: '/questions', label: '问题集', icon: 'questions' },
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

      <nav className={sidebarClass} role="navigation" aria-label="主导航">
        {/* 头部：Logo + 折叠按钮（桌面）/ 关闭按钮（移动抽屉） */}
        <div className={styles.sidebarHeader}>
          <button className={styles.sidebarLogo} onClick={() => handleNav('/')}>
            {collapsed ? 'E' : 'EngramNote'}
          </button>
          <button
            className={styles.sidebarCollapseBtn}
            onClick={onToggleCollapse}
            aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
          >
            {collapsed ? '\u00BB' : '\u00AB'}
          </button>
          {/* 仅移动端抽屉打开时出现：手机上抽屉占 86vw，手指够不到遮罩边缘时
              需要一个明确的关闭入口（显示/隐藏由 mobileOpen 决定，见本文件顶部说明） */}
          {mobileOpen && (
            <button
              className={styles.sidebarMobileClose}
              onClick={onMobileClose}
              aria-label="关闭菜单"
            >
              {'\u2715'}
            </button>
          )}
        </div>

        {/* 导航分组 */}
        <div className={styles.sidebarBody}>
          {NAV_SECTIONS.map((section) => (
            <div key={section.title || 'home'} className={styles.sidebarSection}>
              {section.title && <div className={styles.sidebarSectionTitle}>{section.title}</div>}
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
                    <span className={styles.sidebarItemLabel}>{item.label}</span>
                  </button>
                  {/* 上传快捷入口在笔记分组 */}
                  {item.path === '/notes' && !collapsed && (
                    <button
                      className={styles.sidebarItemAction}
                      onClick={() => handleNav('/upload')}
                      aria-label="上传资料"
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

        {/* 底部：退出 */}
        <div className={styles.sidebarFooter}>
          <button className={styles.sidebarItem} onClick={logout}>
            <span className={styles.sidebarItemIcon}>
              <Icon name="logout" size={20} />
            </span>
            <span className={styles.sidebarItemLabel}>退出</span>
          </button>
        </div>
      </nav>
    </>
  );
}
