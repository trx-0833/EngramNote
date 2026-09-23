/**
 * @file 应用根组件
 * @description EngramNote 前端应用的入口组件，负责：
 * 1. 通过 AuthProvider 提供全局认证状态
 * 2. 根据认证状态切换未登录/已登录两套路由
 * 3. 已登录时渲染侧边栏和主内容区域
 *
 * 代码分割（见 docs/overhaul-plan.md §2.8 F-7）：
 * 此前 18 个页面**全部静态 import**，且重量级依赖都在模块顶层进入口 chunk：
 *   - KnowledgeGraph → react-force-graph-2d（连带 d3 生态）
 *   - utils/markdown → highlight.js 完整构建（384 种语言）+ katex + 字体 CSS
 * 后果是"只想看仪表盘"的用户也要先下载并解析整个图谱引擎与高亮引擎。
 * 现改为按路由 React.lazy 懒加载；首屏只保留登录/仪表盘所需的代码。
 */
import { lazy, Suspense, useState } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import Sidebar from './components/Sidebar';
// 批次 B2：移动端汉堡改用 Icon —— 原来是 `\u2630` ☰，而**同一个码点**也被
// 「笔记列表」用着（Sidebar.tsx），于是"菜单"和"笔记列表"在用户眼里是同一个符号。
import Icon from './components/Icon';
import ErrorBoundary from './components/ErrorBoundary';
import LoadingSpinner from './components/LoadingSpinner';
// 应用骨架的类名归模块所有（overhaul-plan 5.6 序 9）：`layout.css` 的
// `.app-layout*` / `.sidebar-mobile-toggle` + `responsive.css` 里同一批窄屏规则
import styles from './App.module.css';

// ── 登录前页面（首屏必需，保持静态导入以最快呈现登录框） ──
import Login from './pages/Login';
import Register from './pages/Register';

// ── 登录后页面：全部懒加载 ──
const Dashboard = lazy(() => import('./pages/Dashboard'));
const NotesList = lazy(() => import('./pages/NotesList'));
const NoteDetail = lazy(() => import('./pages/NoteDetail'));
const Trash = lazy(() => import('./pages/Trash'));
const Upload = lazy(() => import('./pages/Upload'));
const KnowledgeCards = lazy(() => import('./pages/KnowledgeCards'));
const KnowledgeGraph = lazy(() => import('./pages/KnowledgeGraph'));
const CardDetail = lazy(() => import('./pages/CardDetail'));
const QA = lazy(() => import('./pages/QA'));
const Review = lazy(() => import('./pages/Review'));
const QuestionSets = lazy(() => import('./pages/QuestionSets'));
const TodayLearn = lazy(() => import('./pages/TodayLearn'));
const QuickReview = lazy(() => import('./pages/QuickReview'));
const CardReview = lazy(() => import('./pages/CardReview'));
const DailyMaterials = lazy(() => import('./pages/DailyMaterials'));
const Projects = lazy(() => import('./pages/Projects'));
const LearningAssessment = lazy(() => import('./pages/LearningAssessment'));
const LearningGoals = lazy(() => import('./pages/LearningGoals'));
// 404（批次 E8）：原来是一个**内联在本文件里的局部函数**，现在抽成
// `pages/NotFound.tsx`（计划 §6 E8 行点名的那一页）。与其余业务页一样懒加载 ——
// 它只在 `path="*"` 命中时才需要。文案、DOM 语义、`href="/"` 的真实跳转都逐字未变
// （`e2e/a11y.spec.ts` 的 `not-found` 场景按 heading/link 的名字断言它）。
const NotFound = lazy(() => import('./pages/NotFound'));

// 设计样板间（`/styleguide`，visual-refactor-plan 批次 0.2）：
// 只在**开发构建**里注册 —— 生产产物不该带一个纯验收页。
// 注意 `import.meta.env.DEV` 包住的是整个 `lazy()` 调用，而不只是路由：
// Vite 构建时会把条件替换成 `false`，这个动态 import 随之被 tree-shake 掉，
// StyleGuide 根本不产生 chunk。若只包路由，chunk 仍会生成并被打进产物。
const StyleGuide = import.meta.env.DEV ? lazy(() => import('./pages/StyleGuide')) : null;

/** 路由级加载占位 */
function RouteFallback() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '64px 0' }}>
      <LoadingSpinner />
    </div>
  );
}

/**
 * 页宽三档（visual-refactor-plan 批次 C2）—— 收敛前是六档并存。
 *
 * 迁移期 `App.module.css` 有一条 `.appLayout :global(.container) { max-width: none }`，
 * 把全局 `components.css` 的 1200px 上限去掉了；页宽于是完全由 18 个页面的内联
 * `maxWidth` 决定，实测 600 / 640 / 700 / 760 / 800 / 960 六档 + 11 页全宽
 * （1920 屏上同层级内容页有效宽从 700px 到 1616px，差 2.5 倍）。
 *
 * 现在由本表按路由选定，页面里不再写宽度。三档的**值**在
 * `App.module.css` 的 `.mainReading` / `.mainStandard` / `.mainFull` 上，
 * 令牌来自 `base.css` 的 `--width-reading` / `--width-standard` / `--width-full`。
 */
type PageWidth = 'reading' | 'standard' | 'full';

/**
 * 路由 → 档位。写成**显式查表**而不是一串 `if`：新增路由时漏登记会落到
 * 兜底的 standard（1200px，安全档），而不是静默继承上一个 `if` 留下的大宽度。
 *
 * `pattern` 一律以 `/` 结尾，与 `${pathname}/` 比对 ⇒ 同时覆盖 `/review` 与
 * `/review/`；`*` 表示"这一段之后还有东西"（用 `.+` 实现）。
 *
 * ⚠️ **表按序取首个命中**（`Array.prototype.find`），所以更具体的 pattern
 * 必须排在更笼统的前面 —— 下面各档内部就是这么排的。三档之间没有重叠，
 * 所以跨档的先后不影响结果。
 */
const PAGE_WIDTH_ROUTES: { pattern: RegExp; width: PageWidth; why: string }[] = [
  // ── 全宽档（100%）：宽度必须交给窗口的页面 ──
  {
    pattern: /^\/graph\//,
    width: 'full',
    why: '图谱画布：可用高度与宽度都由容器算（`.graphPage` 减的是 `.appLayout` 上那对 --page-pad-y-*），封顶会挤压 canvas',
  },
  {
    pattern: /^\/notes\/.+\//,
    width: 'full',
    why: '笔记详情 `/notes/:noteId`：编辑态是分屏（原文 | 编辑），两栏都要真实可用宽度',
  },
  // ── 阅读档（760px）：一次只做一件事的页面，注意力聚焦 ──
  //    注意 `/review/quick/:noteId` 与 `/review/cards` 都命中上面的 `/^\/review\//`，
  //    这里逐条列出是为了让"哪条路由属于哪档"在源码里可读；它们与笼统那条同档。
  {
    pattern: /^\/review\/quick\/.+\//,
    width: 'reading',
    why: '快速复习 `/review/quick/:noteId`：逐段过原文，行长必须受控',
  },
  {
    pattern: /^\/review\/cards\//,
    width: 'reading',
    why: '卡片直接复习（阶段 3.12）：与答题复习并行的另一条复习路径，宽度保持一致',
  },
  {
    pattern: /^\/review\//,
    width: 'reading',
    why: '答题复习 `/review`：一次一张卡，卡片不该被拉成 1200px 宽的一行字',
  },
  {
    pattern: /^\/upload\//,
    width: 'reading',
    why: '上传表单：单列表单，宽了只是留白（收敛前 640px）',
  },
  {
    pattern: /^\/assessment\//,
    width: 'reading',
    why: '学习评估：一题一屏的问答流（收敛前 960px）',
  },
  {
    pattern: /^\/qa\//,
    width: 'reading',
    why: '智能问答：对话流是单列（收敛前 800px）',
  },
];

/**
 * 给定的 pathname 用哪一档。
 *
 * 兜底是 `standard`（1200px）—— 即 `visual-refactor-plan` 里的"其余全部"
 * （`/` `/notes` `/trash` `/cards` `/cards/*` `/questions` `/today` `/daily`
 * `/projects` `/goals` 与 404）。
 *
 * ⚠️ `/login` 与 `/register` **不在本表也不受影响**：未登录分支根本不渲染 `<main>`
 * （见下方 `if (!isAuthenticated)`），它们的宽度由 `pages/Auth.module.css` 自己管。
 */
function widthClassFor(pathname: string): PageWidth {
  const normalized = `${pathname}/`;
  const hit = PAGE_WIDTH_ROUTES.find((r) => r.pattern.test(normalized));
  return hit ? hit.width : 'standard';
}

/** 档位 → `App.module.css` 的类名（三档各自带 max-width） */
const WIDTH_CLASS: Record<PageWidth, string> = {
  reading: styles.mainReading,
  standard: styles.mainStandard,
  full: styles.mainFull,
};

/**
 * 路由组件
 * 根据认证状态渲染不同路由
 */
function AppRoutes() {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/register" element={<Register />} />
        <Route path="/login" element={<Login />} />
        {/* 未登录访问其它路径时才回到登录页 */}
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <>
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />
      {/* 移动端汉堡菜单（桌面由 CSS 隐藏；抽屉打开时抽屉会盖住它，见 App.module.css） */}
      <button
        className={styles.sidebarMobileToggle}
        onClick={() => setMobileOpen(true)}
        aria-label="打开菜单"
        aria-expanded={mobileOpen}
      >
        <Icon name="menu" size={24} />
      </button>
      <div
        className={`${styles.appLayout}${sidebarCollapsed ? ` ${styles.appLayoutCollapsed}` : ''}`}
      >
        {/* 页宽三档由 `widthClassFor` 按路由决定（批次 C2）；`page-enter` 是全局动画类。
            这里**不再挂全局 `container` 类** —— 那会把 1200px 上限与模块档位放在
            同权重、跨 chunk 的竞争里（见 App.module.css 文件头）。 */}
        <main
          className={`${styles.main} ${WIDTH_CLASS[widthClassFor(location.pathname)]} page-enter`}
        >
          {/* 按路由重置的错误边界：某条数据触发渲染异常后，
              切换到别的页面即可自动恢复，不必刷新（§2.8 F-2） */}
          <ErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                {/* 认证入口的已登录兜底（与未登录分支里那两条同路径路由对应）：
                    令牌在 localStorage 里长期有效，用户完全可能带着已登录状态
                    回到 /login（历史记录、书签，或注册成功后回退一格）。
                    这里若不拦，pathname 会停在 /login 而本表没有这条路由，
                    于是落到下面的 path="*" 渲染出 404 —— 与"登录成功后停在
                    /login"是同一个洞的另一个入口，只修跳转等于把它留着。 */}
                <Route path="/login" element={<Navigate to="/" replace />} />
                <Route path="/register" element={<Navigate to="/" replace />} />
                <Route path="/" element={<Dashboard />} />
                <Route path="/notes" element={<NotesList />} />
                <Route path="/notes/:noteId" element={<NoteDetail />} />
                <Route path="/trash" element={<Trash />} />
                <Route path="/cards" element={<KnowledgeCards />} />
                <Route path="/graph" element={<KnowledgeGraph />} />
                <Route path="/cards/:cardId" element={<CardDetail />} />
                <Route path="/questions" element={<QuestionSets />} />
                <Route path="/qa" element={<QA />} />
                <Route path="/review" element={<Review />} />
                <Route path="/today" element={<TodayLearn />} />
                <Route path="/daily" element={<DailyMaterials />} />
                <Route path="/projects" element={<Projects />} />
                <Route path="/review/quick/:noteId" element={<QuickReview />} />
                {/* 卡片直接复习（阶段 3.12）：与答题复习是并行的两条路径 */}
                <Route path="/review/cards" element={<CardReview />} />
                <Route path="/assessment" element={<LearningAssessment />} />
                <Route path="/goals" element={<LearningGoals />} />
                <Route path="/upload" element={<Upload />} />
                {/* 设计样板间：开发期验收工具，不进导航、不进生产产物 */}
                {StyleGuide && <Route path="/styleguide" element={<StyleGuide />} />}
                {/* 真正的 404，而不是静默重定向 */}
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}

export default App;
