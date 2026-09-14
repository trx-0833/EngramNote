/**
 * 迁移前后规则清单差集（overhaul-plan 5.6 的"什么都没丢"证据）
 *
 * ## 它回答的问题
 *
 * "把规则从全局样式表搬进 CSS Modules 之后，有没有哪一条**悄悄变了**？"
 * 读代码回答不了：类名被哈希、选择器被重写、声明会被压缩器重排。
 * 所以这里把"迁移前（HEAD 里的源码）"与"迁移后（dist 产物）"两侧的规则
 * 归一化成同一形状再逐条比：
 *
 *   - 侧别 A：`git show HEAD:...` 里的原规则；
 *   - 侧别 B：dist 产物里哈希后的规则；
 *   - 归一化：剥哈希后缀、统一值写法、类名映射成中性占位符 `.C0`/`.C1`…；
 *   - 对齐：A 有 B 无 = **丢失**（逐条交代）；B 有 A 无 = **新增**（同样交代）；
 *     两边都有但值不同 = **值有变化**（必须能说清为什么）。
 *
 * ## 从"一份硬编码的试点切片"改成"按批次声明"（5.6 第一批时改的）
 *
 * 试点轮里 RENAME / WANTED_SELECTOR / 读哪两个文件全是写死的常量，
 * 到了第二批就完全用不了。现在每个批次在 `BATCHES` 里声明：
 * 迁移前的全局样式表、这批搬走的**老类名**、以及每个类名的新家。
 * 新类名不是手写的，而是由老类名**机械推导**（kebab → camelCase）——
 * 手写映射表就是一处会写错、且写错了没人发现的地方。
 * 推导结果还会回模块文件里核对一遍（`checkRenames` 自检），
 * 所以"推导规则"和"模块里真实写的类名"不可能悄悄分叉。
 *
 * ## 为什么"值有变化"不等于事故
 *
 * 本轮有两处**有意**的值替换，都在这里显式列出来：
 *   1. `box-shadow: 0 0 0 3px rgba(15,52,96,.08)` → `… var(--color-primary-light)`
 *      （令牌化，值完全相同，归一化后应判为"逐字保留"）；
 *   2. `animation: scaleIn …` → `animation: feedbackScaleIn …`
 *      + 模块内新增 `@keyframes feedbackScaleIn`（动画体逐字复制）。
 *      这是**必需**的：CSS Modules 会把动画名一起哈希，裸名引用会在产物里
 *      悬空（详见 QuizAnswerCard.module.css 文件头）。这里对动画做一次
 *      名称替换后再比，把"改名"与"动画体是否一致"分开看。
 *
 * ## 选择器强化（`:global(.btn).authSubmit`）也算"声明过的差异"
 *
 * 第一批发现：**模块的 CSS 在产物里排在全局样式表之前**（Vite 按模块图顺序
 * 产出，而静态引入的组件先于 `main.tsx` 里的样式表被求值）。
 * 于是 `.auth-submit` 与全局 `.btn` 这类"同权重、同在产物里"的竞争会反转胜负。
 * 修法是把全局类写进选择器提高权重，产物里表现为 `.btn._authSubmit_hash`。
 * 差集比对时把这段**声明过的**前缀剥掉（`hardened`），否则会被误报成
 * "一条规则丢失 + 一条规则新增"。
 *
 * 用法：node scripts/css-migration-diff.mjs
 *   前置：npm run build（侧别 B 取自 dist 产物）
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { parseRules, classesOf, findRecentRev, readFromGit, splitSelectors } from './lib/css-parse.mjs'

const root = process.cwd()

/**
 * 批次声明。加一批就在这里追加一项，不要改上面的逻辑。
 *
 * - `beforeSheets`：这批规则迁移前住在哪些全局样式表里（从 git 读）；
 * - `rev`：从哪个修订读"迁移前"。默认 `HEAD`，**只对已经提交过的批次**
 *   才需要往前挪一格 —— 试点（quiz 切片）已随 `6acb21c` 提交，
 *   所以它的"迁移前"是 `HEAD~1`；第一批尚未提交，用默认的 `HEAD`。
 *   读错修订的表现是"迁移前 0 条"，脚本会直接报错退出，不会静默放过；
 * - `groups`：老类名 → 新家的模块文件（新类名由 kebab→camel 推导后回文件核对）；
 * - `hardened`：为压过全局类而**显式加进选择器**的全局类前缀（老类名 → 前缀）。
 *   第一批曾用过一次（`:global(.btn).authSubmit`），后来改成修正 `main.tsx`
 *   的导入顺序、把选择器还原成单类，所以现在是空的。机制留着：哪天再出现
 *   "必须靠提权才能赢"的正当场景，在这里登记即可 —— 不登记的话差集会把它
 *   误报成"丢失 1 条 + 新增 1 条"；
 * - `keyframes`：搬进模块并改名的 `@keyframes`（动画体必须与全局原版一致）。
 * - `resolvedConflicts`（第四批新增）：**被裁决的"输家声明"**。
 *   序 5 的 `assessment.css` 与 `refinements.css` 对 `.quiz-question-card` /
 *   `-number` / `-text` 写了 14 条同名属性的不同值（`.score-summary-number` 另有 5 条
 *   跨选择器的覆盖）—— 两边权重相同，**谁赢只看导入顺序**。
 *   迁移的契约不允许"照抄一套"，于是先用真实 Chromium 读 computed style 测出胜者，
 *   再把**输的那套删掉**。删掉的声明既不是"丢失"也不是"值有变化"，
 *   而是**第三种、必须逐条声明**的差异，所以在这里登记：脚本把它从"迁移前"
 *   一侧摘掉、单独成节列出（谁赢、凭什么），并**自检**每条声明都真的命中过 ——
 *   写错一条（类名拼错、值改了）会报错退出，不会变成一条永远绿灯的空声明。
 *
 * ⚠️ `rev` 一般**不用写**：留空时脚本按内容自动定位"迁移前"（从 HEAD 往回找
 * 第一个还含有本批老类名的修订）。写死 `HEAD~N` 会被并行的无关提交打乱 ——
 * 第二批期间另一个 agent 提交了一个后端改动，所有相对计数就集体错位了。
 * 这个字段留着只是为了在自动定位不适用时手工兜底。
 */
const BATCHES = [
  {
    id: '试点：quiz 答题切片',
    beforeSheets: ['src/styles/learning.css', 'src/styles/responsive.css'],
    groups: [
      {
        module: 'src/components/quiz/QuizAnswerCard.module.css',
        classes: ['quiz-option', 'quiz-option-selected', 'feedback-correct', 'feedback-incorrect'],
      },
      {
        module: 'src/components/quiz/SelfRatingButtons.module.css',
        classes: ['self-rating-btn'],
      },
    ],
    hardened: {},
    keyframes: [
      {
        fromSheet: 'src/styles/base.css',
        orig: 'scaleIn',
        module: 'src/components/quiz/QuizAnswerCard.module.css',
        renamed: 'feedbackScaleIn',
      },
      {
        fromSheet: 'src/styles/base.css',
        orig: 'shake',
        module: 'src/components/quiz/QuizAnswerCard.module.css',
        renamed: 'feedbackShake',
      },
    ],
  },
  {
    id: '第一批：auth → cleaning → diff → dashboard',
    beforeSheets: [
      'src/styles/auth.css',
      'src/styles/cleaning.css',
      'src/styles/diff.css',
      'src/styles/dashboard.css',
      'src/styles/responsive.css',
    ],
    groups: [
      {
        module: 'src/pages/Auth.module.css',
        classes: [
          'auth-bg',
          'auth-card',
          'auth-title',
          'auth-input-group',
          'auth-input-icon',
          'auth-submit',
          'auth-footer',
        ],
      },
      {
        module: 'src/components/CleaningPanel.module.css',
        classes: [
          'cleaning-panel',
          'cleaning-stats',
          // `cleaning-progress` / `cleaning-progress-bar` 原本在这里（第一批逐字搬进模块）。
          // 收尾轮（死代码清理）把它们连同模块内的 `@keyframes cleaningPulse` 一起删了 ——
          // 两个类名全项目 0 处 TSX 引用，删它是可证明的空操作。
          // 证据 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`；
          // 留在 `classes` 里会让 `checkRenames` 自检失败（模块里再也找不到 `.cleaningProgress`）。
          'duplicate-blocks',
          'duplicate-block',
          'duplicate-block-header',
          'duplicate-block-info',
          'duplicate-block-index',
          'duplicate-block-similarity',
          'duplicate-block-actions',
          'duplicate-block-compare',
          'duplicate-block-text-label',
          'duplicate-block-text-content',
          'duplicate-block-text-empty',
        ],
      },
      {
        module: 'src/components/DiffView.module.css',
        classes: [
          'diff-container',
          'diff-summary',
          'diff-header',
          'diff-col-label',
          'diff-body',
          'diff-block',
          'diff-line',
          'diff-line-prefix',
          'diff-line-number',
          'diff-line-content',
          'diff-line-removed',
          'diff-line-added',
          'diff-line-unchanged',
        ],
      },
      {
        module: 'src/pages/Dashboard.module.css',
        classes: ['dashboard-two-col', 'dashboard-review-card', 'trend-bar', 'trend-bar-warning'],
      },
    ],
    // 曾经是 `{ 'auth-submit': '.btn' }`（`:global(.btn).authSubmit`）。
    // 已改为修正 `main.tsx` 的导入顺序 + 还原单类选择器，
    // 所以这里刻意留空：留空意味着"这一批没有任何选择器文本变化"，
    // 任何新的提权都必须显式登记，否则差集会报"丢失 + 新增"。
    hardened: {},
    keyframes: [
      {
        fromSheet: 'src/styles/base.css',
        orig: 'scaleIn',
        module: 'src/pages/Auth.module.css',
        renamed: 'authScaleIn',
      },
      {
        fromSheet: 'src/styles/base.css',
        orig: 'cleaning-pulse',
        module: 'src/components/CleaningPanel.module.css',
        renamed: 'cleaningPulse',
        // 收尾轮（死代码清理）：全局原版与模块内那份**都删了** ——
        // 模块内那份的唯一引用者是 `.cleaningProgressBar`，而那条规则零引用。
        // `retired: true` 让这条比对的判据变成"全局原版确实存在过 + 模块里现在确实没有了"，
        // 而不是"两份动画体一致"。谁把进度条加回来，这里会立刻报红。
        retired: true,
      },
    ],
    /**
     * 收尾轮（死代码清理）：第一批那 60 条里，有 **2 条是零引用的死规则**
     * （`.cleaning-progress` / `.cleaning-progress-bar`，清洗进度条后来改由
     * `TaskProgress` 用全局 `.progress-bar*` 渲染）。第一批的契约是"证明什么都没丢"，
     * 所以当时**逐字搬进模块、不删**；收尾轮核过证据后删除，登记在这里，
     * 于是批次汇总会显示 58 逐字保留 + 2 已声明删除（合计仍是 60，不是"少了 2 条"）。
     * 两个类名同时要从上面的 `classes` 里去掉：`checkRenames` 会回模块核对
     * "由老类名推导出的新类名真的写在模块里"，而它们已经不在模块里了。
     * 证据：`docs/migration-evidence/5.6-13-dead-css-cleanup.md`；
     * 三个方向（迁移前有 → 源码没了 → 产物没了）的自检在
     * `scripts/verify-built-css.mjs` 的 `CLEANUP_RETIREMENTS`。
     */
    resolvedConflicts: [
      {
        sheet: 'src/styles/cleaning.css',
        selector: '.cleaning-progress',
        prop: '*',
        value: '*',
        note: '死代码清理：零引用（全项目没有任何 tsx 挂这个类名）⇒ 整条删除',
        winner: '不适用 —— 0 处引用，删它是可证明的空操作',
        evidence: 'grep `src/**` 的 TSX/TS 命中 0；运行时拼类名（classList/模板串/styles[…]）0 处',
      },
      {
        sheet: 'src/styles/cleaning.css',
        selector: '.cleaning-progress-bar',
        prop: '*',
        value: '*',
        note: '死代码清理：零引用 ⇒ 整条删除（它还是模块内 `@keyframes cleaningPulse` 的唯一引用者，那条动画同批删除）',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
    ],
  },
  {
    id: '第二批：markdown-extras（ask-ai + selection-menu）',
    // 本批尚未提交 → 迁移前就读 HEAD（= 第一批提交后的状态）
    beforeSheets: ['src/styles/markdown-extras.css'],
    groups: [
      {
        module: 'src/components/NoteAskPanel.module.css',
        classes: [
          'ask-ai-panel',
          'ask-ai-header',
          'ask-ai-title',
          'ask-ai-close',
          'ask-ai-body',
          'ask-ai-input',
          'ask-ai-selected-hint',
          'ask-ai-actions',
          'ask-ai-question',
          'ask-ai-thinking',
          'ask-ai-answer',
          'ask-ai-error',
          'ask-ai-provider',
        ],
      },
      {
        module: 'src/pages/notedetail/SelectionMenu.module.css',
        classes: ['selection-menu'],
      },
    ],
    // 本批没有需要提权的规则：`.ask-ai-input` / `.selection-menu` 都是单类选择器，
    // 相对全局层只有元素选择器（`input, textarea` / `button`）能碰上，
    // 权重 (0,1,0) > (0,0,1)，与先后无关。
    hardened: {},
    // 本批不搬 `@keyframes`：同表的 `citation-flash` 属于留在全局的
    // `.markdown-body mark.citation-highlight`，引用与定义仍在同一层。
    keyframes: [],
  },
  {
    id: '第三批：learning 按归属拆分 + dashboard 余下（StatCard）',
    // 本批尚未提交 → 迁移前就读 HEAD（= 第二批提交后的状态）。
    // 三个来源样式表：learning.css（`.qa-*` / `.upload-zone*` / `.search-input-*`）、
    // responsive.css（`.list-toolbar` 两条 480px 规则 + `.stat-number` 的 480px 规则）、
    // dashboard.css（`.stat-card*` / `.stat-number` / `.stat-label`）。
    beforeSheets: [
      'src/styles/learning.css',
      'src/styles/responsive.css',
      'src/styles/dashboard.css',
    ],
    groups: [
      {
        module: 'src/pages/QA.module.css',
        classes: ['qa-user-bubble', 'qa-ai-card'],
      },
      {
        module: 'src/pages/Upload.module.css',
        classes: ['upload-zone', 'upload-zone-active'],
      },
      {
        // `.list-toolbar` 这个类名**只**在 responsive.css 里出现过（全项目唯一一处），
        // 所以必须在这里声明：不声明的话那两条 480px 规则会被算成"丢失"
        // （旧类名集合里没有它，侧别 A 就一条也捞不到）。
        module: 'src/pages/NotesList.module.css',
        classes: ['list-toolbar', 'search-input-wrapper', 'search-input-icon'],
      },
      {
        // 统计卡片：先抽出 `components/StatCard.tsx`，样式再随组件进模块。
        // `stat-card-*` 的 4 个配色变体只出现在 `::before` 上，推导规则
        // （kebab → camel）与模块里的 `.statCardBlue::before` 一致。
        module: 'src/components/StatCard.module.css',
        classes: [
          'stat-card',
          'stat-card-blue',
          'stat-card-green',
          'stat-card-gold',
          'stat-card-purple',
          'stat-number',
          'stat-label',
        ],
      },
    ],
    // 本批没有需要提权的规则：模块类都只写在自家元素上，两个与全局类并列的地方
    // （`.qaAiCard` 挨着 `.card`、`.state-*` 之类）都没有**同名属性**竞争 ——
    // `.qaAiCard` 与 `.card` 争的是 `border-left` 长写 vs `border` 简写，
    // 差集脚本按属性名配对，本来也不会把它误报成"丢失 + 新增"。
    hardened: {},
    keyframes: [
      {
        fromSheet: 'src/styles/base.css',
        orig: 'slideUp',
        module: 'src/pages/QA.module.css',
        renamed: 'qaSlideUp',
      },
      {
        fromSheet: 'src/styles/base.css',
        orig: 'glowPulse',
        module: 'src/pages/Upload.module.css',
        renamed: 'uploadGlowPulse',
      },
    ],
  },
  {
    id: '第四批：assessment.css 按归属拆分 + assessment × refinements 冲突裁决',
    // 本批尚未提交 → 迁移前按内容自动定位（= 第三批提交后的状态）。
    // 三个来源：assessment.css（本页私有的 20 条）、refinements.css（补丁层的 14 条）、
    // responsive.css（480px 的 `.knowledge-points-grid` 一条）。
    beforeSheets: [
      'src/styles/assessment.css',
      'src/styles/refinements.css',
      'src/styles/responsive.css',
    ],
    groups: [
      {
        // 留全局的（不在这个清单里）：`.assessment-header` / `-title` / `-subtitle`
        // （学习评估页 + `pages/projects/ProjectsHeader.tsx`）与 `.note-select-card*`
        // （+ `pages/projects/ProjectNotesList.tsx`，且 `Projects.test.tsx` 按类名查询）
        // —— 判据是规范 §4 第 2 条（两个不相邻功能），见 assessment.css 文件头。
        module: 'src/pages/LearningAssessment.module.css',
        classes: [
          'score-bar',
          'score-bar-header',
          'score-bar-label',
          'score-bar-value',
          'score-bar-track',
          'score-bar-fill',
          'score-bar-fill-high',
          'score-bar-fill-mid',
          'score-bar-fill-low',
          'quiz-question-card',
          'quiz-question-card-active',
          'quiz-question-number',
          'quiz-question-text',
          'score-summary-card',
          'score-summary-number',
          'score-summary-label',
          'score-value',
          'knowledge-points-grid',
          'knowledge-points-section',
        ],
      },
    ],
    // 本批没有需要提权的规则：模块类都单独挂在元素上，没有"全局类 + 模块类
    // 并列写在同一个元素"的情况（`CASCADE_PAIRS` 因此也不需要新增条目，
    // 与第三批同理）。唯一的"半覆盖"是 `.scoreSummaryCard .scoreSummaryNumber`
    // —— 那条**故意保留两条规则**，因为合并会把它从 (0,2,0) 降到 (0,1,0)。
    hardened: {},
    // 本批不搬 `@keyframes`：这一批规则里一条 `animation` 都没有
    // （`LearningAssessment.tsx` 的行内 `animation: 'slideUp …'` 用的是全局动画，
    // 不过 CSS Modules，`base.css` 的定义不受影响）。
    keyframes: [],
    /**
     * 被裁决的"输家声明"（19 条）。判据不是源码顺序，而是**真实 Chromium 的
     * computed style**：一次性探针走真实渲染路径（登录 → /assessment →
     * 开放性问题 → 生成问题 → 提交答案）读 `.quiz-question-card` /
     * `-number` / `-text` / `.score-summary-number` 的全部 computed 属性，
     * 并额外用真实指针 hover、真实焦点触发 `:focus-within`。
     * 实测结论：14 条冲突**全部**由后加载的 `refinements.css` 取胜
     * （`quiz-question-card` 的 `border-radius` = 16px 而不是 10px、
     * `-number` 的 32px / 50% / 金色、`-text` 的 1.125rem / flex-start …）。
     * 探针用完已删，配方与完整实测值见 `docs/css-migration-plan.md` §5 雷区 3
     * 与 `docs/migration-evidence/5.6-09-conflict-resolution.md`。
     */
    resolvedConflicts: [
      // ── `.quiz-question-card`（5 条）──
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-card',
        prop: 'border-radius',
        value: 'var(--radius-md)',
        winner: 'refinements.css 的 `.quiz-question-card { border-radius: var(--radius-lg) }`',
        evidence: 'computed border-radius = 16px（--radius-lg），不是 10px',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-card',
        prop: 'padding',
        value: 'var(--space-lg)',
        winner: 'refinements.css 的 `padding: 24px`',
        evidence:
          'computed padding = 24px；两侧**数值相同**（--space-lg = 24px），' +
          '所以这条只是写法之争，删输家不改变渲染',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-card',
        prop: 'margin-bottom',
        value: 'var(--space-md)',
        winner: 'refinements.css 的 `margin-bottom: 16px`',
        evidence:
          'computed margin-bottom = 16px；两侧**数值相同**（--space-md = 16px），同上',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-card',
        prop: 'box-shadow',
        value: 'var(--shadow-sm)',
        winner: 'refinements.css 的 `box-shadow: 0 2px 8px rgba(15, 52, 96, 0.06)`',
        evidence: 'computed box-shadow = rgba(15,52,96,.06) 0 2px 8px（不是 --shadow-sm 的双层阴影）',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-card',
        prop: 'transition',
        value: 'border-color 0.2s ease',
        winner: 'refinements.css 的 `transition: border-color .25s …, box-shadow .25s …`',
        evidence: 'computed transition-property = border-color, box-shadow、duration = .25s, .25s',
      },
      // ── `.quiz-question-number`（6 条）──
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-number',
        prop: 'width',
        value: '28px',
        winner: 'refinements.css 的 `width: 32px`',
        evidence: 'computed width = 32px',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-number',
        prop: 'height',
        value: '28px',
        winner: 'refinements.css 的 `height: 32px`',
        evidence: 'computed height = 32px',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-number',
        prop: 'border-radius',
        value: '9999px',
        winner: 'refinements.css 的 `border-radius: 50%`',
        evidence: 'computed border-radius = 50%（32px 见方下与 9999px 视觉相同，但胜者是 50%）',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-number',
        prop: 'background',
        value: 'var(--gradient-primary)',
        winner: 'refinements.css 的 `background: var(--color-accent-light)`',
        evidence: 'computed background-image = none、background-color = rgba(201,169,89,.12)（金色淡底）',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-number',
        prop: 'color',
        value: 'white',
        winner: 'refinements.css 的 `color: var(--color-accent)`',
        evidence: 'computed color = rgb(201,169,89)，不是白字',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-number',
        prop: 'font-size',
        value: '0.8rem',
        winner: 'refinements.css 的 `font-size: 0.9rem`',
        evidence: 'computed font-size = 14.4px（0.9rem），不是 12.8px',
      },
      // ── `.quiz-question-text`（3 条）──
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-text',
        prop: 'align-items',
        value: 'center',
        winner: 'refinements.css 的 `align-items: flex-start`',
        evidence: 'computed align-items = flex-start',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-text',
        prop: 'gap',
        value: 'var(--space-xs)',
        winner: 'refinements.css 的 `gap: var(--space-sm)`',
        evidence: 'computed column-gap = 8px（--space-sm），不是 4px',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.quiz-question-text',
        prop: 'font-size',
        value: '0.95rem',
        winner: 'refinements.css 的 `font-size: 1.125rem`',
        evidence: 'computed font-size = 18px（1.125rem），不是 15.2px',
      },
      // ── `.score-summary-number`：不是同名属性冲突，而是补丁层用 (0,2,0) 压掉单类 ──
      {
        sheet: 'src/styles/assessment.css',
        selector: '.score-summary-number',
        prop: 'font-size',
        value: '2.5rem',
        winner: 'refinements.css 的 `.score-summary-card .score-summary-number { font-size: 2rem }`（权重 (0,2,0)）',
        evidence: 'computed font-size = 32px（2rem），不是 40px',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.score-summary-number',
        prop: 'background',
        value: 'var(--gradient-primary)',
        winner: 'refinements.css 的 `background: none`',
        evidence: 'computed background-image = none（渐变字被补丁层关掉，改成金色实字）',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.score-summary-number',
        prop: '-webkit-background-clip',
        value: 'text',
        winner: 'refinements.css 的 `-webkit-background-clip: unset`',
        evidence: 'computed background-clip = border-box',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.score-summary-number',
        prop: 'background-clip',
        value: 'text',
        winner: 'refinements.css 的 `background-clip: unset`',
        evidence: 'computed background-clip = border-box',
      },
      {
        sheet: 'src/styles/assessment.css',
        selector: '.score-summary-number',
        prop: '-webkit-text-fill-color',
        value: 'transparent',
        winner: 'refinements.css 的 `-webkit-text-fill-color: var(--color-accent)`',
        evidence: 'computed color = rgb(201,169,89)（金色实字，不是透明填充）',
      },
    ],
  },
  {
    id: '第五批（序 8）：components.css 的页面级布局挂点（note-detail / note-list / edit-split）',
    // 本批尚未提交 → 迁移前按内容自动定位（= 第四批提交后的状态）。
    // 两个来源：components.css（5 条页面级布局挂点）、responsive.css（同一批类名的
    // 窄屏规则 8 条：768px 档 7 条 + 480px 档 1 条）—— 两半必须同批处理，
    // 留一半在补丁层里 = 规则还在、永不生效（计划 §5 雷区 2）。
    beforeSheets: ['src/styles/components.css', 'src/styles/responsive.css'],
    groups: [
      {
        module: 'src/pages/notedetail/NoteDetailHeader.module.css',
        classes: ['note-detail-header', 'note-detail-actions'],
      },
      {
        module: 'src/pages/NotesList.module.css',
        classes: ['note-list-item', 'note-list-actions'],
      },
      {
        module: 'src/pages/notedetail/EditSplitView.module.css',
        classes: ['edit-split'],
      },
    ],
    // 本批没有需要提权的规则：`.noteDetailActions :global(.btn)` 里的 `:global`
    // 不是提权，而是**保住原来的后代选择器**（`.note-detail-actions .btn`，权重 (0,2,0)）——
    // 不写 `:global` 的话 `.btn` 会被一起哈希，产物里指向一个不存在的类名。
    // 权重与原来逐字相同，所以不是 `hardened`（那是"权重只升不降"的声明）。
    hardened: {},
    // 本批不搬 `@keyframes`：这批规则里一条 `animation` 都没有。
    keyframes: [],
  },
  {
    id: '第六批（序 9）：layout.css 按归属拆分（Sidebar / App 骨架）+ responsive.css 同批规则',
    // 本批尚未提交 → 迁移前按内容自动定位（= 第五批提交后的状态）。
    // 两个来源：layout.css（侧边栏整节 + `.app-layout*` + `.sidebar-mobile-toggle`，
    // 共 27 条）与 responsive.css（命中同一批类名的窄屏规则 12 条：
    // 768px 档 11 条 + 480px 档 1 条）。两半必须同批 —— 留一半在补丁层里
    // 就是"规则还在、永不生效"（计划 §5 雷区 2）。
    beforeSheets: ['src/styles/layout.css', 'src/styles/responsive.css'],
    groups: [
      {
        // 留全局的：`.navbar*`（5 条，grep 0 处引用 —— 没有归属组件可搬，
        // 死规则留到"死 CSS 清理"轮，见 layout.css 文件头）
        module: 'src/components/Sidebar.module.css',
        classes: [
          'sidebar',
          'sidebar-collapsed',
          'sidebar-mobile-open',
          'sidebar-header',
          'sidebar-logo',
          'sidebar-collapse-btn',
          'sidebar-mobile-close',
          'sidebar-body',
          'sidebar-open-lock',
          'sidebar-section',
          'sidebar-section-title',
          'sidebar-item-row',
          'sidebar-item',
          'sidebar-item-active',
          'sidebar-item-icon',
          'sidebar-item-label',
          'sidebar-item-action',
          'sidebar-divider',
          'sidebar-footer',
          'sidebar-overlay',
        ],
      },
      {
        module: 'src/App.module.css',
        classes: ['app-layout', 'app-layout-collapsed', 'sidebar-mobile-toggle'],
      },
    ],
    // 本批没有需要提权的规则。`.app-layout .container` 里的 `:global(.container)`
    // 不是提权而是**保住全局类名**（`.container` 留在 components.css）：
    // 产物是 `._appLayout_hash .container`，权重仍 (0,2,0)，与迁移前逐字相同。
    hardened: {},
    // `.sidebar-overlay` 写的是裸名 `animation: fadeIn`（定义在 base.css）——
    // 搬进模块后动画名会被一起哈希而定义留在原处 ⇒ 遮罩淡入静默消失。
    // 动画体逐字复制成 `sidebarOverlayFadeIn`（雷区 1 的第七次实例）。
    keyframes: [
      {
        fromSheet: 'src/styles/base.css',
        orig: 'fadeIn',
        module: 'src/components/Sidebar.module.css',
        renamed: 'sidebarOverlayFadeIn',
      },
    ],
    /**
     * 本批唯一"工具原本看不见"的搬家：`responsive.css` 顶部的
     * `:root { --page-pad-y-top: 64px; --page-pad-y-bottom: var(--space-md) }`
     * （480px 档另有 60px / var(--space-sm)）。
     *
     * `:root` 的选择器里**一个类名都没有**，所以它从来不在差集的视野里
     * （`classesOf` 是空的）—— 不登记的话，这条被搬走的声明永远不会被任何检查看见。
     * 两个变量随 `.app-layout` 收进 `src/App.module.css` 的 `.appLayout`：
     * 它们的两处读者（`.app-layout .container` 与图谱模块的 `.graph-page`）
     * 都在 `.app-layout` 的子树里，继承关系与计算值完全相同；
     * `:root` 属于令牌层（base.css），补丁层不该定义全局令牌（规范 §2）。
     * 自定义属性名不参与 CSS Modules 哈希，变量名与值一个字未改。
     */
    relocations: [
      {
        from: { sheet: 'src/styles/responsive.css', context: '', selector: ':root', prop: '--page-pad-y-top', value: '64px' },
        to: { file: 'src/App.module.css', context: '', selector: '.appLayout' },
        why: '`:root` 里没有类名 → 差集看不见；随 `.appLayout` 收进模块（值不变）',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '', selector: ':root', prop: '--page-pad-y-bottom', value: 'var(--space-md)' },
        to: { file: 'src/App.module.css', context: '', selector: '.appLayout' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 480px)',
          selector: ':root',
          prop: '--page-pad-y-top',
          value: '60px',
        },
        to: { file: 'src/App.module.css', context: '@media (max-width: 480px)', selector: '.appLayout' },
        why: '窄屏覆盖值随同一条规则搬（480px 档）',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 480px)',
          selector: ':root',
          prop: '--page-pad-y-bottom',
          value: 'var(--space-sm)',
        },
        to: { file: 'src/App.module.css', context: '@media (max-width: 480px)', selector: '.appLayout' },
        why: '同上',
      },
    ],
  },
  {
    id: '第七批（序 10）：graph.css 整份进图谱模块 + responsive.css 同批规则',
    // 本批尚未提交 → 迁移前按内容自动定位（= 第六批提交后的状态）。
    beforeSheets: ['src/styles/graph.css', 'src/styles/responsive.css'],
    groups: [
      {
        // 整个图谱功能一份模块：`.graph-panel` / `.graph-panel-title` /
        // `.graph-legend` / `.graph-legend-item` 被两个以上组件共用，
        // 拆开只会复制或逼出跨目录 import（理由写在 Graph.module.css 文件头）。
        module: 'src/components/graph/Graph.module.css',
        classes: [
          'graph-page',
          'graph-page-main',
          'graph-sidebar',
          'graph-canvas',
          'graph-toolbar',
          'graph-toolbar-left',
          'graph-toolbar-right',
          'graph-legend',
          'graph-legend-item',
          'graph-legend-item-active',
          'graph-legend-dot',
          'graph-btn',
          'graph-btn-active',
          'graph-badge',
          'graph-create-hint',
          'graph-panel',
          'graph-panel-title',
          'graph-suggestion-card',
          'graph-relation-line',
          'graph-controls',
          'graph-control-btn',
          'graph-minimap',
          'graph-suggestion-score-bar',
          'graph-suggestion-score-bar-fill',
          'graph-stats-grid',
          'graph-stat-item',
          'graph-stat-value',
          'graph-stat-label',
          'graph-stats-bar-row',
          'graph-stats-bar-label',
          'graph-stats-bar-track',
          'graph-stats-bar-fill',
          'graph-stats-bar-count',
          'graph-search-box',
          'graph-search-input',
          'graph-search-spinner',
          'graph-search-results',
          'graph-search-result-item',
          'graph-search-result-dot',
          'graph-search-result-title',
          'graph-search-result-type',
          'graph-filter-select',
          'graph-neighbor-item',
          'graph-neighbor-dot',
          'graph-neighbor-title',
          'graph-neighbor-rel',
          'graph-neighbor-count',
        ],
      },
    ],
    // 本批没有需要提权的规则：所有选择器都是单类或"类 + 元素/伪类"，
    // 没有"全局类 + 模块类并列写在同一个元素"的情况（与第三、四批同理）。
    hardened: {},
    // `.graph-search-spinner` 的裸名 `animation: graph-spin` —— 动画体逐字复制成
    // 模块里的 `graphSpin`；全局原版留在 graph.css（**不删**），
    // 理由写在 graph.css 与 Graph.module.css 的文件头。
    keyframes: [
      {
        fromSheet: 'src/styles/graph.css',
        orig: 'graph-spin',
        module: 'src/components/graph/Graph.module.css',
        renamed: 'graphSpin',
      },
    ],
    /**
     * `responsive.css` 的两条**四选择器组**：`.filter-pill` / `.segment-btn`
     * 留全局、`.graph-btn` / `.graph-control-btn` 进模块 ⇒ 组必须拆开。
     * 不登记的话差集会报"1 条丢失 + 1 条新增"，而两边其实一个值都没改。
     */
    relocations: [
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill, .segment-btn, .graph-btn, .graph-control-btn',
          prop: 'min-height',
          value: '40px',
        },
        to: {
          file: 'src/components/graph/Graph.module.css',
          context: '@media (max-width: 768px)',
          selector: '.graphBtn, .graphControlBtn',
        },
        why: '图谱两个类进模块（`.filter-pill, .segment-btn` 那半留在 responsive.css 等序 13）',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill, .segment-btn, .graph-btn',
          prop: 'padding-left',
          value: 'var(--space-md)',
        },
        to: { file: 'src/components/graph/Graph.module.css', context: '@media (max-width: 768px)', selector: '.graphBtn' },
        why: '同上（选择器组拆开，值不变）',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill, .segment-btn, .graph-btn',
          prop: 'padding-right',
          value: 'var(--space-md)',
        },
        to: { file: 'src/components/graph/Graph.module.css', context: '@media (max-width: 768px)', selector: '.graphBtn' },
        why: '同上（选择器组拆开，值不变）',
      },
    ],
  },
  {
    id: '第八批（序 12）：refinements.css 按归属拆分（补丁层清空）',
    // 本批尚未提交 → 迁移前按内容自动定位（= 第七批提交后的状态）。
    // 两个来源：refinements.css（38 条）与 responsive.css（该文件里的
    // `[style*="rgba(0,0,0,0.5)"] { padding }` 一条 —— 与 refinements 那 12 条同类，一起裁决）。
    beforeSheets: ['src/styles/refinements.css', 'src/styles/responsive.css'],
    groups: [
      {
        // `.markdown-editor` = 分屏左边的 textarea；`.edit-toolbar` 是零引用的预留样式
        // （逐字搬、不删，死代码清理单独一轮）
        module: 'src/pages/notedetail/EditSplitView.module.css',
        classes: ['markdown-editor', 'edit-toolbar'],
      },
    ],
    // 本批没有需要提权的规则；`.editToolbar :global(.btn)` 里的 `:global` 是
    // **保住全局类名**（`.btn` 留在 components.css），权重 (0,2,0) 与原来相同。
    hardened: {},
    keyframes: [],
    /**
     * 没有进模块的那些规则：类名留全局（`.card-hover` / `.btn:disabled` /
     * `.filter-pill` / `.segment-btn` / `.card`），所以按归属搬回**拥有该类名的
     * 全局样式表**；选择器文本与值一个字未改。逐条双向自检。
     */
    relocations: [
      // ── `.btn` 的禁用态 → components.css（放在全部 `:hover` 规则之后，顺序即行为）──
      { prop: 'cursor', value: 'not-allowed' },
      { prop: 'opacity', value: '0.55' },
      { prop: 'transform', value: 'none' },
      { prop: 'box-shadow', value: 'none' },
      { prop: 'pointer-events', value: 'auto' },
    ].map((d) => ({
      from: {
        sheet: 'src/styles/refinements.css',
        context: '',
        selector: '.btn:disabled, .btn[disabled]',
        prop: d.prop,
        value: d.value,
      },
      to: { file: 'src/styles/components.css', context: '', selector: '.btn:disabled, .btn[disabled]' },
      why: '全局公共件 `.btn` 的禁用态回家；与 `.btn-*-hover` 同权重 ⇒ 必须排在它们之后（本文件里就在后面）',
    })).concat([
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.btn-primary:disabled, .btn-danger:disabled',
          prop: 'opacity',
          value: '0.6',
        },
        to: {
          file: 'src/styles/components.css',
          context: '',
          selector: '.btn-primary:disabled, .btn-danger:disabled',
        },
        why: '同上',
      },
      // ── `.card-hover`：序 5 裁决出来的**胜者**搬回全局卡片定义处 ──
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.card-hover',
          prop: 'transition',
          value:
            'transform 0.3s var(--ease-out-expo), box-shadow 0.3s var(--ease-out-expo), border-color 0.3s var(--ease-out-expo)',
        },
        to: { file: 'src/styles/components.css', context: '', selector: '.card-hover' },
        why: '序 5 的裁决结论：胜者在补丁层 ⇒ 拆补丁层时把值搬回 `.card` 旁边（现在只有一个定义处）',
      },
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.card-hover:hover',
          prop: 'transform',
          value: 'translateY(-2px)',
        },
        to: { file: 'src/styles/components.css', context: '', selector: '.card-hover:hover' },
        why: '序 5 实测胜者（hover 时 computed transform = matrix(1,0,0,1,0,-2)）',
      },
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.card-hover:hover',
          prop: 'box-shadow',
          value: 'var(--shadow-lg)',
        },
        to: { file: 'src/styles/components.css', context: '', selector: '.card-hover:hover' },
        why: '序 5 实测胜者（hover 时 computed box-shadow = --shadow-lg 的值）',
      },
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.card-hover:hover',
          prop: 'border-color',
          value: 'var(--color-border)',
        },
        to: { file: 'src/styles/components.css', context: '', selector: '.card-hover:hover' },
        why: '这一条两边本来就同值（序 5 只删了输的两条），一起收口到同一个定义处',
      },
      // ── `.card` 的窄屏内边距 → components.css 的 768 块 ──
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '@media (max-width: 768px)',
          selector: '.card',
          prop: 'padding',
          value: 'var(--space-md)',
        },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.card' },
        why: '压的是同文件的 `.card { padding: var(--space-lg) }`，媒体块在文件末尾 ⇒ 胜负关系不变',
      },
      // ── `.filter-pill` / `.segment-btn` 的指示线 → learning.css（定义它们的地方）──
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill', prop: 'position', value: 'relative' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill' },
        why: '两个类名的定义在 learning.css（4 个页面 / 2 个功能在用 ⇒ 留全局）',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'content', value: "''" },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '金色指示线随类名回家',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'position', value: 'absolute' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'bottom', value: '-2px' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'left', value: '50%' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'width', value: '0' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'height', value: '2px' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'background', value: 'var(--gradient-gold)' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'border-radius', value: '1px' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill::after', prop: 'transform', value: 'translateX(-50%)' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.filter-pill::after',
          prop: 'transition',
          value: 'width 0.25s var(--ease-out-expo)',
        },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.filter-pill-active::after', prop: 'width', value: '60%' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.filter-pill-active::after' },
        why: '激活态；必须排在 `.filter-pill::after` 之后（同权重）',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn', prop: 'position', value: 'relative' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn' },
        why: '同上（`.segment-btn` 的定义也在 learning.css）',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'content', value: "''" },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'position', value: 'absolute' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'bottom', value: '1px' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'left', value: '20%' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'width', value: '0' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'height', value: '2px' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'background', value: 'var(--color-accent)' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn::after', prop: 'border-radius', value: '1px' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '',
          selector: '.segment-btn::after',
          prop: 'transition',
          value: 'width 0.25s var(--ease-out-expo)',
        },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn::after' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/refinements.css', context: '', selector: '.segment-btn-active::after', prop: 'width', value: '60%' },
        to: { file: 'src/styles/learning.css', context: '', selector: '.segment-btn-active::after' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/refinements.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill',
          prop: 'padding',
          value: 'var(--space-xs) var(--space-sm)',
        },
        to: { file: 'src/styles/learning.css', context: '@media (max-width: 768px)', selector: '.filter-pill' },
        why:
          '跨属性竞争（计划 §5 雷区 12）：它是简写，压掉 `responsive.css` 那条 ' +
          '`padding-left/right: var(--space-md)`（序 13 搬进 learning.css 时保持"长写在前、简写在后"）',
      },
      // ── 5 个零引用的**预留语义化类**：序 12 逐字搬进 components.css，
      //    收尾轮（死代码清理）**整条删除** —— 见本批 `resolvedConflicts` 末尾那 6 条 ──
    ]),
    /**
     * `[style*="rgba(0,0,0,0.5)"] …` 那 12 条 —— **整条规则删除**，
     * 依据是真 Chromium 实测：该属性选择器命中 **0** 个元素
     * （浏览器把内联 `rgba(0,0,0,0.5)` 序列化成带空格的 `rgba(0, 0, 0, 0.5)`，
     * 不带空格的子串永远匹配不上）⇒ 迁移前就从未生效。
     * `prop: '*'` 表示"这条规则的全部声明"。
     */
    resolvedConflicts: [
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] > .card',
        prop: '*',
        value: '*',
        winner: '不适用 —— 没有任何元素命中这个选择器（不是"谁赢"，是"从来没生效"）',
        evidence: '探针实测：无空格形态命中 0 / 带空格形态命中 1 / 页面里真的存在 1 个遮罩',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] > .card > h3',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上（同一个属性选择器前缀）',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card label',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card label:hover',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card label:has(input:checked)',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card label input[type="checkbox"]',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card label span',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card > div[style*="justify-content: flex-end"]',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上（属性选择器链里两段都用了不带空格的写法）',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card .btn-primary',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card .btn-primary:hover',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card .btn-primary:active',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card .btn-secondary',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      {
        sheet: 'src/styles/refinements.css',
        selector: '[style*="rgba(0,0,0,0.5)"] .card .btn-secondary:hover',
        prop: '*',
        value: '*',
        winner: '不适用 —— 同上',
        evidence: '同上',
      },
      /**
       * ── 收尾轮（死代码清理）：5 个零引用的预留语义化类，整条规则删除 ──
       *
       * 它们原本由**本批登记为 relocation**（序 12 从 `refinements.css` 逐字搬进
       * `components.css`，`prop: '*'`）。收尾轮核过证据（`src/**` 的 TSX/TS 里
       * 0 处引用、运行时拼类名 0 处、`LinkManagerModal.tsx` 用的是内联 style
       * 一个类名都没挂）之后**整条删掉**，于是登记从 `relocations` 挪到这里：
       * 删掉的声明同样既不是"丢失"也不是"值有变化"。
       * 逐条证据见 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`。
       */
      ...[
        '.link-modal',
        '.material-list-item',
        '.material-list-item:hover',
        '.material-list-item-selected',
        '.type-badge',
        '.type-badge-material',
      ].map((selector) => ({
        sheet: 'src/styles/refinements.css',
        selector,
        prop: '*',
        value: '*',
        note: '死代码清理：零引用（预留语义化类）⇒ 整条删除，不是"搬家"',
        winner: '不适用 —— 全项目 0 处引用（连挂类名的 tsx 都没有），删它是可证明的空操作',
        evidence: 'grep `src/**` 的 TSX/TS 命中 0；运行时拼类名（classList/模板串/styles[…]）0 处',
      })),
    ],
  },
  {
    id: '第九批（序 13）：responsive.css 清空（补丁层的最后一节）',
    /**
     * 这一批**没有任何类名进模块**：剩下的规则要么是跨功能共用的全局类名
     * （`.btn` / `.card` / `.page-header-row` / `.filter-pill` / `.segment-btn` /
     * `.heading-serif` / 裸元素重置），要么是 `marked` 生成的正文类名
     * （`.markdown-body …`）—— 按规范 §4 全部留全局，只是"回到拥有它的样式表"。
     *
     * 所以它走 `groups: []` 这条特殊通道：证据不是"逐条保留"，
     * 而是"① 本批登记的每一条声明双向自检通过 + ② 工作区里的 `responsive.css`
     * 真的只剩注释（解析出 0 条规则）"。
     *
     * 这张表在 HEAD 里还有 30 余条规则属于更早的批次（试点 / 第一 / 第三 /
     * 序 5 / 序 8 / 序 9 / 序 10 各自登记过），它们不在本批的清单里 ——
     * 跨批次的合并审计见 `docs/migration-evidence/5.6-10-empty-patch-layers.md`。
     */
    beforeSheets: ['src/styles/responsive.css'],
    groups: [],
    hardened: {},
    keyframes: [],
    relocations: [
      // ── 768px：通用触控目标 / 溢出保护 → components.css ──
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.btn', prop: 'min-height', value: '40px' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.btn' },
        why: '`.btn` 是全局公共件（45 处引用）⇒ 留在 components.css 的媒体块里',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill, .segment-btn, .graph-btn, .graph-control-btn',
          prop: 'min-height',
          value: '40px',
        },
        to: { file: 'src/styles/learning.css', context: '@media (max-width: 768px)', selector: '.filter-pill, .segment-btn' },
        why:
          '⚠️ 与序 10 登记的是**同一条** `from` 声明：四个选择器组里 ' +
          '`.graph-btn` / `.graph-control-btn` 那半随组件进了图谱模块（序 10 登记），' +
          '`.filter-pill` / `.segment-btn` 这半留全局 → 进 learning.css（本批登记）。' +
          '组的拆分已发生，所以 `from.selector` 写的是迁移前的原始组文本',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill, .segment-btn, .graph-btn',
          prop: 'padding-left',
          value: 'var(--space-md)',
        },
        to: { file: 'src/styles/learning.css', context: '@media (max-width: 768px)', selector: '.filter-pill, .segment-btn' },
        why: '同上（注意它被 refinements 那条 `padding` 简写压掉，见序 12 的登记）',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.filter-pill, .segment-btn, .graph-btn',
          prop: 'padding-right',
          value: 'var(--space-md)',
        },
        to: { file: 'src/styles/learning.css', context: '@media (max-width: 768px)', selector: '.filter-pill, .segment-btn' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: 'select, input:not([type="checkbox"]):not([type="radio"]), textarea',
          prop: 'min-height',
          value: '40px',
        },
        to: {
          file: 'src/styles/components.css',
          context: '@media (max-width: 768px)',
          selector: 'select, input:not([type="checkbox"]):not([type="radio"]), textarea',
        },
        why: '裸元素重置（规范 §4 第 1 条）：没有"拥有者组件"，只能留在全局层',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.btn-ghost', prop: 'padding', value: 'var(--space-xs) var(--space-sm)' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.btn-ghost' },
        why: '`.btn-ghost` 定义在 components.css',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.btn-ghost', prop: 'font-size', value: '0.8rem' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.btn-ghost' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.card', prop: 'overflow-wrap', value: 'anywhere' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.card' },
        why: '`.card` 是全局公共件',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.page-header-row', prop: 'flex-wrap', value: 'wrap' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.page-header-row' },
        why: '5 个页面在用的页头挂点（规范 §4 第 2 条）；类名原来**只定义在 responsive.css**，现在回到 components.css 的页面级挂点区',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.page-header-row', prop: 'row-gap', value: 'var(--space-sm)' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 768px)', selector: '.page-header-row' },
        why: '同上',
      },
      // ── 768px：`.markdown-body` 一整组 → markdown.css（拥有它的样式表）──
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body', prop: 'font-size', value: '1rem' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body' },
        why: '类名留全局（三个不相邻功能在用 + `marked` 生成的 DOM）⇒ 回到 markdown.css',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body', prop: 'line-height', value: '1.85' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body h1', prop: 'font-size', value: '1.35rem' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body h1' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body h2', prop: 'font-size', value: '1.2rem' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body h2' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body h3', prop: 'font-size', value: '1.05rem' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body h3' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body p, .markdown-body li, .markdown-body blockquote, .markdown-body :not(pre) > code',
          prop: 'overflow-wrap',
          value: 'anywhere',
        },
        to: {
          file: 'src/styles/markdown.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body p, .markdown-body li, .markdown-body blockquote, .markdown-body :not(pre) > code',
        },
        why: '长英文/URL/行内代码必须能断行（选择器组原样保留）',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body table', prop: 'display', value: 'block' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body table' },
        why: '宽表格自身成为横向滚动容器',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body table', prop: 'max-width', value: '100%' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body table' },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body table', prop: 'overflow-x', value: 'auto' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body table' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body table',
          prop: '-webkit-overflow-scrolling',
          value: 'touch',
        },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body table' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body th, .markdown-body td',
          prop: 'padding',
          value: 'var(--space-xs) var(--space-sm)',
        },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body th, .markdown-body td' },
        why: '同上（选择器组原样保留）',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body th, .markdown-body td',
          prop: 'white-space',
          value: 'normal',
        },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body th, .markdown-body td' },
        why: '同上',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body .katex-block, .markdown-body .katex-display',
          prop: 'max-width',
          value: '100%',
        },
        to: {
          file: 'src/styles/markdown.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body .katex-block, .markdown-body .katex-display',
        },
        why: 'KaTeX 是第三方 DOM：类名只能全局命中（规范 §4 第 3 条）',
      },
      {
        from: {
          sheet: 'src/styles/responsive.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body .katex-block, .markdown-body .katex-display',
          prop: 'overflow-x',
          value: 'auto',
        },
        to: {
          file: 'src/styles/markdown.css',
          context: '@media (max-width: 768px)',
          selector: '.markdown-body .katex-block, .markdown-body .katex-display',
        },
        why: '同上',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body .katex', prop: 'font-size', value: '1em' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body .katex' },
        why:
          '这条**从未生效**（被 markdown-extras.css 顶层同权重的 1.1em 压掉，计划 §4.7）；' +
          '搬进 markdown.css 后它仍在 markdown-extras.css 之前 ⇒ 胜负关系逐字不变',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 768px)', selector: '.markdown-body pre', prop: 'font-size', value: '0.85rem' },
        to: { file: 'src/styles/markdown.css', context: '@media (max-width: 768px)', selector: '.markdown-body pre' },
        why: '代码块只收字号',
      },
      // ── 480px ──
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 480px)', selector: '.btn', prop: 'min-height', value: '44px' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 480px)', selector: '.btn' },
        why: '48px 档的触控目标下限（Apple HIG / WCAG 2.5.5）',
      },
      {
        from: { sheet: 'src/styles/responsive.css', context: '@media (max-width: 480px)', selector: '.heading-serif', prop: 'overflow-wrap', value: 'anywhere' },
        to: { file: 'src/styles/components.css', context: '@media (max-width: 480px)', selector: '.heading-serif' },
        why: '`.heading-serif` 定义在 components.css',
      },
    ],
    /**
     * `[style*="rgba(0,0,0,0.5)"] { padding: var(--space-md) }`（768px 档）——
     * 与 refinements.css 那 12 条同一个属性选择器前缀，**命中 0 个元素**，
     * 迁移前就从未生效。真 Chromium 实测见序 12 的登记与证据文件。
     */
    resolvedConflicts: [
      {
        sheet: 'src/styles/responsive.css',
        context: '@media (max-width: 768px)',
        selector: '[style*="rgba(0,0,0,0.5)"]',
        prop: '*',
        value: '*',
        winner: '不适用 —— 没有任何元素命中这个选择器（"从来没生效"，不是"谁赢"）',
        evidence: '探针实测：无空格形态命中 0 / 带空格形态命中 1 / 页面里真的存在 1 个遮罩',
      },
    ],
  },
]

// ── 归一化 ──
/**
 * 值归一化：吃掉压缩器/构建器的等价改写，只留语义。
 *
 * 每一条都要有据可查 —— 下面的替换全部是"同一个颜色的两种写法"或
 * "同一个伪元素的两种写法"，不是把差异抹平：
 *
 * 1. `rgba(15, 52, 96, .08)` ↔ `var(--color-primary-light)`
 *    —— 试点轮的令牌化替换，base.css 里两者数值完全相同，
 *    展开回字面值再比，避免把"用了令牌"误报成"值变了"。
 * 2. `rgba(255,255,255,.92)` ↔ `#ffffffeb`
 *    —— 压缩器把带 alpha 的颜色压成 8 位十六进制（alpha 四舍五入到 8 位：
 *    `.92 × 255 = 234.6 → 235 = 0xeb`）。两侧都归一到 `#rrggbbaa`。
 * 3. `#fffc` ↔ `#ffffffcc` —— 压缩器把可缩写的形式写到最短；同样展开。
 * 4. `color: white` ↔ `color: #fff` —— 命名色被压成十六进制。
 * 5. `content: ''` ↔ `content: ""` —— 引号风格。
 * 6. `border-radius: 16px 16px 4px 16px` ↔ `border-radius:16px 16px 4px`
 *    —— 第 4 个值省略时取第 2 个值，渲染完全相同（第三批实测：
 *    `.qaUserBubble` 的胶囊圆角）。见 `canonRadius`。
 * 7. `inset: 0` ↔ `top:0;right:0;bottom:0;left:0` —— 构建器把 `inset`
 *    简写**降级**成四条长写（第三批实测：`.uploadZoneActive::after`）。
 *    这是声明级的改写，所以由 `canonDecls` 里的 `expandInset` 两侧展开。
 *
 * 这七条都是**产物**层面的等价改写，与"搬家有没有改变语义"无关；
 * 不归一化的话它们会伪装成"值有变化"，把真正的变化淹掉
 * （第三批实测：`.listToolbar .searchInputWrapper` 被误写成单类时，
 * 差集正是靠这里报出的"1 条丢失 + 1 条新增"抓到的）。
 */
const TOKEN_EQUIV = {
  'var(--color-primary-light)': 'rgba(15,52,96,.08)',
}

/** 8 位十六进制化：`#abc` / `#abcc` / `#aabbcc` / `#aabbccdd` 一律补成 `#aabbccdd` */
function expandHex(s) {
  return s.replace(/#([0-9a-f]{3,8})(?![\w-])/gi, (m, h) => {
    let x = h.toLowerCase()
    // 3 位 / 4 位是缩写形式（4 位那档带 alpha，如 `#fffc` = `#ffffffcc`）
    if (x.length === 3 || x.length === 4) x = x.split('').map((c) => c + c).join('')
    if (x.length === 6) x += 'ff'
    return x.length === 8 ? `#${x}` : m
  })
}

/** `rgba(r,g,b,a)` → `#rrggbbaa`（alpha 四舍五入到 8 位，与压缩器一致） */
function rgbaToHex8(s) {
  return s.replace(
    /rgba\((\d{1,3}),(\d{1,3}),(\d{1,3}),(1|0|\.\d+|0\.\d+)\)/g,
    (m, r, g, b, a) => {
      const av = a === '1' ? 255 : a === '0' ? 0 : Math.round(Number.parseFloat(a) * 255)
      const hex = (n) => Number(n).toString(16).padStart(2, '0')
      return `#${hex(r)}${hex(g)}${hex(b)}${hex(av)}`
    },
  )
}

function canonValue(v) {
  let s = v
    .replace(/\s+/g, ' ')
    .replace(/(?<![\d.])0\.(\d)/g, '.$1') // 0.3s → .3s
    .replace(/translateX\(([^)]*)\)/g, 'translate($1)')
    .replace(/translateY\(([^)]*)\)/g, 'translate(0, $1)')
    .replace(/\s*,\s*/g, ',')
    .replace(/"/g, "'") // content: "" → content: ''
    .replace(/\bwhite\b/g, '#ffffff') // 命名色 → 十六进制
    .replace(/\s+/g, '')
    .toLowerCase()
  for (const [token, literal] of Object.entries(TOKEN_EQUIV)) {
    s = s.split(token).join(literal)
  }
  return expandHex(rgbaToHex8(s))
}

/**
 * `border-radius` 的四值 → 三值（第三批新增的等价改写，逐条说明出处）。
 *
 * CSS 规定"第 4 个值省略时取第 2 个值"，所以压缩器会把
 * `border-radius: 16px 16px 4px 16px`（第 4 值与第 2 值相同）收成
 * `border-radius:16px 16px 4px` —— 渲染结果完全相同。
 * 实测出处：`pages/QA.module.css` 的 `.qaUserBubble`（原 `learning.css`
 * 的 `.qa-user-bubble` 胶囊气泡），产物 `dist/assets/QA-*.css`。
 * 不归一化的话它会被报成 1 条"丢失"，把真变化淹掉。
 *
 * 只处理这一个形态：带 `/`（椭圆半径）或 `var()` / `calc()` 时原样返回，
 * 不做任何猜测。
 */
function canonRadius(v) {
  if (/[/()]/.test(v)) return v
  const parts = v.trim().split(/\s+/)
  if (parts.length === 4 && parts[3] === parts[1]) return parts.slice(0, 3).join(' ')
  return v
}

/**
 * `inset` 简写 → 四条长写（第三批新增，声明级改写，所以在 `canonDecls` 里做）。
 *
 * `inset` 是 `top/right/bottom/left` 的简写，构建器按目标浏览器
 * （Vite 默认 target ≈ safari14 / chrome87）把它**降级**成长写。
 * 实测出处：`pages/Upload.module.css` 的 `.uploadZoneActive::after`
 * （原 `learning.css` 的 `.upload-zone-active::after`），源码写 `inset: 0`，
 * 产物 `dist/assets/Upload-*.css` 里是
 * `top:0;right:0;bottom:0;left:0` —— 语义完全相同，但按属性名配对时
 * 会整整少一条声明（差集报"丢失：产物缺 inset"）。
 *
 * 看不懂的写法（`var()` / `calc()` / 斜杠）返回 null，调用方原样保留 ——
 * 宁可留着一条可疑差异，也不猜。
 */
function expandInset(v) {
  const parts = v.trim().split(/\s+/)
  if (parts.length < 1 || parts.length > 4) return null
  if (parts.some((p) => /[(),/]/.test(p))) return null
  // 简写展开规则：1 值→四边；2 值→上下 / 左右；3 值→上 / 左右 / 下；4 值→上右下左
  const [t, r = t, b = t, l = r] = parts
  return [
    ['top', t],
    ['right', r],
    ['bottom', b],
    ['left', l],
  ]
}

/**
 * 声明列表 → "prop:val" 排序后的数组。
 *
 * `animation` / `animation-name` 的值**整条归一成 `NAME`**，不参与文本比对：
 * 动画名在产物里被哈希（`scaleIn` → `_feedbackScaleIn_1a0d4_1`），
 * 逐字比文本必然不等。而动画真正要验的两件事 ——
 * "引用的动画在产物里有没有定义"（绑定）与"动画体是否与全局原版一致"——
 * 由文件末尾两个独立检查负责，比硬凑成一次字符串比对可靠得多。
 *
 * ⚠️ 判断必须看**属性名**，不能对值写 `/animation:/` ——
 * 传进来的已经只是值（`feedbackScaleIn 0.3s …`），不含属性名，永远匹配不上。
 */
function canonDecls(decls) {
  const out = []
  for (const [p, v] of decls) {
    const prop = p.trim().toLowerCase()
    if (prop === 'animation' || prop === 'animation-name') {
      out.push(`${prop}:NAME`)
      continue
    }
    // `inset` 简写被构建器降级成四条长写 → 两侧都展开成同样的四个键再比
    const inset = prop === 'inset' ? expandInset(v) : null
    if (inset) {
      for (const [longhand, value] of inset) out.push(`${longhand}:${canonValue(value)}`)
      continue
    }
    out.push(`${prop}:${canonValue(prop === 'border-radius' ? canonRadius(v) : v)}`)
  }
  return out.sort()
}

/**
 * 剥掉 CSS Modules 的哈希后缀：`_quizOption_1a0d4_52` → `_quizOption`。
 * 哈希形如 `_<5位>_<行号>`，但**只剥这一种**，不要用宽泛的
 * `_[0-9a-z]+_\d+` —— 那会把 `_feedbackscalein_1a0d4_1` 里经过
 * 别名替换后的名字也啃掉一截，剩下一个孤零零的下划线，
 * 让两侧看起来"值不同"（试点轮踩过，两条 animation 报了假警报）。
 */
const stripHash = (s) => s.replace(/_([0-9a-z]{5})_\d+(?![0-9a-z])/g, '')

/** `auth-input-group` → `authInputGroup`（本项目模块类名的唯一推导规则） */
const kebabToCamel = (name) => name.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())

/**
 * 定位"迁移前"的修订：从 HEAD 往回找第一个**还含有本批老类名**的修订。
 *
 * 不再用 `HEAD~N` 数格子：并行 agent 提交无关改动会让所有相对计数集体错位
 * （第二批真的遇到了）。按内容定位与提交顺序无关，见
 * `lib/css-parse.mjs` 的 `findRecentRev`。
 */
function resolveBeforeRev(batch, oldNames) {
  return findRecentRev((rev) =>
    batch.beforeSheets.some((rel) => {
      const abs = path.join(root, rel)
      const css = readFromGit(abs, rev)
      return parseRules(css).some((r) => classesOf(r.selector).some((c) => oldNames.has(c)))
    }),
  )
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * 自检：推导出来的新类名必须真的写在它声称的模块文件里。
 * 没有这一步，"机械推导"就可能与模块里实际写的类名分叉
 * （比如有人手写了 `authForm` 而不是 `authCard`），
 * 而分叉的表现是"规则全丢"这种吓人的假警报。
 */
function checkRenames(batch) {
  const problems = []
  for (const g of batch.groups) {
    const abs = path.join(root, g.module)
    if (!fs.existsSync(abs)) {
      problems.push(`${g.module} 不存在`)
      continue
    }
    const css = fs.readFileSync(abs, 'utf8')
    for (const old of g.classes) {
      const camel = kebabToCamel(old)
      if (!new RegExp(`\\.${camel}(?![\\w-])`).test(css)) {
        problems.push(`${g.module} 里找不到 .${camel}（由 .${old} 推导而来）`)
      }
    }
  }
  return problems
}

/**
 * 已登记的"搬家"（第五批新增的第五类差异）：**同一条声明从 A 挪到 B，值不变**。
 *
 * ## 为什么需要它
 *
 * 本工具按 `groups`（老类名 → 新模块）把规则分成"在切片里"和"不在切片里"，
 * 而 5.6 的后半程（序 9/12/13）要做一件工具原本看不见的事：
 * **把声明从一个全局样式表挪到另一个全局样式表**，或者**把一条多选择器组拆开**
 * （重的部分随组件进模块、留全局的那部分进它自己的样式表）。
 * 两种情况下"迁移前有、迁移后那一处没有"都会长得像"丢失"：
 *
 * - `.graph-btn, .graph-control-btn` 与 `.filter-pill, .segment-btn` 原来在
 *   `responsive.css` 的**同一条规则**里（`min-height: 40px`）。图谱写进模块、
 *   `filter-pill` 留全局 ⇒ 选择器组必须拆开，两次搬家都没有"值变化"；
 * - `responsive.css` 的 `:root { --page-pad-y-top: 64px }` 根本没有类名
 *   （`classesOf` 是空的），本来就不在差集的视野里 —— 不登记的话，
 *   这条搬走的声明**永远不会被任何检查看见**（正是"漏登记 = 静默不覆盖"）。
 *
 * ## 自检（两个方向都响亮失败）
 *
 * ① `from` 在"迁移前"的源文本里必须**找得到**（sheet + context + selector + prop + value
 *    逐字对上，值走 `canonValue`）—— 写错类名会报"一次都没命中"；
 * ② `to` 在目标文件里必须**找得到**（该选择器所在的规则里，有同名同值的声明）——
 *    搬丢了会报"目标里没有这条"。
 * 两条都通过才打印，所以这份清单不可能变成一串永远绿灯的空声明。
 *
 * 登记的声明会从"迁移前"一侧摘掉（否则会被报成丢失），并从摘要里单独成节。
 */
function relocationMatches(entry, sheet, context, selector, prop, value) {
  const f = entry.from
  return (
    f.sheet === sheet &&
    (f.context || '') === context &&
    f.selector === selector.trim() &&
    (f.prop.trim() === '*' ||
      (f.prop.trim().toLowerCase() === prop.trim().toLowerCase() &&
        canonValue(f.value) === canonValue(value)))
  )
}

/**
 * 整条规则的搬家：`from.prop: '*'` 表示"这条规则的全部声明一起走"。
 *
 * 机制保留、**目前没有用户**：它原来服务于零引用的预留类
 * （`.link-modal` / `.material-list-item*` / `.type-badge*`）—— 序 12 登记它们
 * "整条搬进 components.css"，收尾轮核实零引用后改登记为 `resolvedConflicts`
 * 的删除条目（同一个数组，只是从"搬家"变成"删除"），于是最后一批通配搬家也消失了。
 * 留着是因为它同样是 `resolvedConflicts` 里"整条规则删除"的判据
 * （13 条属性选择器 + 6 条预留类都用 `prop: '*'`）—— 两份登记共用一个写法。
 */
function isWildcardRelocation(entry) {
  return entry.from.prop.trim() === '*'
}

/** 从某条规则的声明里摘掉"已登记的搬家"，并记账（命中次数） */
function applyRelocations(batch, rel, context, selector, decls, hitCounts) {
  const entries = batch.relocations || []
  if (entries.length === 0) return decls
  const kept = []
  for (const [p, v] of decls) {
    const idx = entries.findIndex((e) => relocationMatches(e, rel, context, selector, p, v))
    if (idx >= 0) {
      hitCounts[idx] += 1
      continue
    }
    kept.push([p, v])
  }
  return kept
}

/** 目标文件里有没有这条声明（`to.selector` 可以是某个选择器组里的一员） */
function relocationLands(entry, droppedDecls = 0) {
  const abs = path.join(root, entry.to.file)
  if (!fs.existsSync(abs)) return false
  const wildcard = isWildcardRelocation(entry)
  const want = wildcard ? '' : entry.from.prop.trim().toLowerCase()
  const wantVal = wildcard ? '' : canonValue(entry.from.value)
  for (const r of parseRules(fs.readFileSync(abs, 'utf8'))) {
    if (entry.to.context !== undefined && entry.to.context !== r.context) continue
    // `to.selector` 可以写成**整个选择器组**（`.a, .b`）或组里的一员：
    // 两种都接受 —— 组在产物/源码里可能跨行，所以先归一空白再比。
    const norm = (s) => s.replace(/\s+/g, ' ').trim()
    const members = splitSelectors(r.selector).map(norm)
    if (!members.includes(norm(entry.to.selector)) && norm(r.selector) !== norm(entry.to.selector)) {
      continue
    }
    if (wildcard) {
      // 整条搬家：目标那条规则至少要装下搬走的这么多声明
      if (r.decls.length >= droppedDecls) return true
      continue
    }
    if (r.decls.some(([p, v]) => p.trim().toLowerCase() === want && canonValue(v) === wantVal)) {
      return true
    }
  }
  return false
}

/**
 * 已裁决的冲突：把"输家声明"从**迁移前**一侧摘掉，并逐条记账。
 *
 * ## 第五批起支持 `prop: '*'`（整条规则）
 *
 * 序 13 清空 `responsive.css` / 序 12 拆 `refinements.css` 时遇到一类
 * **整条规则都该删**的声明：`[style*="rgba(0,0,0,0.5)"] …` 那 13 条。
 * 真 Chromium 实测该属性选择器命中 **0** 个元素（浏览器把内联
 * `rgba(0,0,0,0.5)` 序列化成带空格的 `rgba(0, 0, 0, 0.5)` ⇒ 不带空格的子串
 * 永远匹配不上），所以这些规则**迁移前就从未生效**，删它是可证明的空操作。
 * 逐条声明 40 多条声明太啰嗦、且容易漏一条，于是允许 `prop: '*'` /
 * `value: '*'`：匹配该 (来源样式表 + 上下文 + 选择器) 规则下的**全部声明**。
 * 自检不变 —— 该规则必须真的存在，否则报"一条都没命中"。
 */
function applyResolvedDrops(batch, rel, context, selector, decls, hitCounts) {
  const drops = batch.resolvedConflicts || []
  if (drops.length === 0) return decls
  const kept = []
  for (const [p, v] of decls) {
    const hitIdx = drops.findIndex(
      (d) =>
        d.sheet === rel &&
        d.selector === selector.trim() &&
        (!d.context || d.context === context) &&
        (d.prop.trim() === '*' ||
          (d.prop.trim().toLowerCase() === p.trim().toLowerCase() &&
            canonValue(d.value) === canonValue(v))),
    )
    if (hitIdx >= 0) {
      // 通配条目吃掉整条规则的全部声明；精确条目只吃第一条（原来就是这样）
      if (drops[hitIdx].prop.trim() === '*') {
        hitCounts[hitIdx] += 1
        continue
      }
      if (!hitCounts[hitIdx]) {
        hitCounts[hitIdx] = 1
        continue
      }
    }
    kept.push([p, v])
  }
  return kept
}

/** 批次 → 归一化选择器所需的映射表（老名 → 新名 / 占位符） */
function batchMaps(batch) {
  const remap = [] // [oldName, newName, placeholder]
  const all = batch.groups.flatMap((g) => g.classes)
  // 占位符按名字排序分配，两批之间互不干扰、批内稳定
  const sorted = [...all].sort()
  const placeholderOf = new Map(sorted.map((n, i) => [n, `.C${i}`]))
  for (const g of batch.groups) {
    for (const old of g.classes) {
      remap.push([old, kebabToCamel(old), placeholderOf.get(old)])
    }
  }
  // ⚠️ 替换顺序必须"长名优先"，否则 `.diffLine` 会先命中
  // `.diffLinePrefix` 的前缀（不过 `(?![\w-])` 边界已经挡住了这种情形，
  // 两道保险都留着）。
  remap.sort((a, b) => b[1].length - a[1].length)
  return remap
}

/**
 * 选择器 → 中性占位符。
 *
 * ⚠️ 每个类名必须映射到**不同**的占位符（`.C0` / `.C1` …），
 * 不能都换成同一个 `.X`：那样 `.quiz-option` / `.quiz-option-selected` /
 * `.feedback-correct` 会挤到同一个 key 上，查表时拿到的是别人那条规则，
 * 于是报出"值有变化"的假警报（试点轮踩过：5 条规则被并成 1 个 key）。
 *
 * 另注意产物里是 `._quizOption_hash`：点后面多一个下划线，
 * 所以 `\.` 后面要允许 `_?`，否则一条都对不上。
 */
function neutralSelector(sel, remap) {
  let s = stripHash(sel)
  for (const [oldName, newName, ph] of remap) {
    s = s.replace(new RegExp(`\\._?${escapeRe(newName)}(?![\\w-])`, 'g'), ph)
    s = s.replace(new RegExp(`\\._?${escapeRe(oldName)}(?![\\w-])`, 'g'), ph)
  }
  // 压缩器把 `::before` / `::after` 压成 `:before` / `:after`（同义写法）。
  // 不归一化的话，四条带伪元素的规则会被报成"丢失 + 新增"。
  //
  // 第六批（序 9）又补一条：**选择器组里逗号后的空格被压掉** ——
  // 源码 `.sidebar,\n  .sidebar-collapsed` 解析成 `.sidebar, .sidebar-collapsed`，
  // 产物里是 `._sidebar_hash,._sidebarCollapsed_hash`（逗号后无空格）。
  // 不归一化就会把三条"选择器组"规则（`.sidebar, .sidebar-collapsed` 的 768/480
  // 两档 + `.app-layout, .app-layout-collapsed`）全报成丢失 —— 而它们逐字都在产物里。
  // 逗号两侧的空格在 CSS 里没有语义，归一化不会抹平任何真实差异。
  return s
    .replace(/::/g, ':')
    .replace(/\s*,\s*/g, ',')
    .replace(/\s+/g, ' ')
    .trim()
}

// ── 产物新鲜度（拿旧产物下结论是这类脚本最危险的失败方式）──
const assetsDir = path.join(root, 'dist/assets')
if (!fs.existsSync(assetsDir)) {
  console.error('✗ 没有 dist/assets：先跑 npm run build')
  process.exit(2)
}
const distSheets = fs.readdirSync(assetsDir).filter((x) => x.endsWith('.css'))
const distMtime = Math.max(...distSheets.map((f) => fs.statSync(path.join(assetsDir, f)).mtimeMs))
const newestSrc = Math.max(
  ...fs
    .readdirSync(path.join(root, 'src/styles'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => fs.statSync(path.join(root, 'src/styles', f)).mtimeMs),
)
if (newestSrc > distMtime) {
  console.error('✗ 产物比源码旧：先跑 npm run build，否则结论基于旧产物')
  process.exit(2)
}

let failed = false

for (const batch of BATCHES) {
  console.log(`\n════════ 迁移前后规则清单差集：${batch.id} ════════`)

  const renameProblems = checkRenames(batch)
  if (renameProblems.length) {
    console.error('✗ 类名推导自检失败（先修这个，否则下面全是假警报）：')
    for (const p of renameProblems) console.error(`   - ${p}`)
    process.exit(3)
  }

  const remap = batchMaps(batch)
  const oldNames = new Set(remap.map((r) => r[0]))
  const newNames = [...new Set(remap.map((r) => r[1]))]
  const key = (r) => `${r.context} ${neutralSelector(r.selector, remap)}`

  // ── 侧别 A：迁移前（git 修订里的源码） ──
  const beforeRules = []
  // 已裁决的"输家声明"命中计数：batch.resolvedConflicts 里每条都必须命中一次，
  // 否则说明这条声明是凭空写的（类名/值对不上），要报错而不是静默放过。
  const dropHitCounts = new Array((batch.resolvedConflicts || []).length).fill(0)
  // 已登记的"搬家"命中计数：同样每条都必须命中（否则就是一条永远绿灯的空声明）
  const relocEntries = batch.relocations || []
  const relocHitCounts = new Array(relocEntries.length).fill(0)
  /**
   * 有些批次**没有任何类名进模块**（序 13 只是把 `responsive.css` 的规则
   * 按归属搬去别的全局样式表 / 模块），于是"按类名定位迁移前的修订"这条路走不通
   * （`oldNames` 是空集）。这时按**声明**定位：从 HEAD 往回找第一个
   * "这一批登记的每一条 `from` 声明都还在"的修订。与类名定位同样与提交顺序解耦。
   */
  const groupsEmpty = (batch.groups || []).length === 0
  const resolveBeforeRevByDecls = () =>
    findRecentRev((candidate) => {
      const wanted = [
        ...relocEntries.map((e) => e.from),
        ...(batch.resolvedConflicts || []).map((d) => ({
          sheet: d.sheet,
          context: d.context,
          selector: d.selector,
          prop: d.prop,
          value: d.value,
        })),
      ]
      return wanted.every((f) =>
        parseRules(readFromGit(path.join(root, f.sheet), candidate)).some(
          (r) =>
            r.selector.trim() === f.selector &&
            (!f.context || f.context === r.context) &&
            (f.prop === '*' ||
              r.decls.some(
                ([p, v]) =>
                  p.trim().toLowerCase() === f.prop.trim().toLowerCase() &&
                  canonValue(v) === canonValue(f.value),
              )),
        ),
      )
    })
  const rev = batch.rev || (groupsEmpty ? resolveBeforeRevByDecls() : resolveBeforeRev(batch, oldNames))
  if (!rev) {
    console.error(
      `✗ ${batch.id}：从 HEAD 往回 40 个提交里找不到"迁移前"。` +
        `要么登记写错了，要么这个批次其实没迁移过 —— 两种情况都不该继续编差集。`,
    )
    process.exit(3)
  }
  /** 没有任何声明被"搬家"或"裁决删除"吃掉的规则（用于空批次的自检） */
  const leftoverRules = []
  let leftoverDeclCount = 0
  for (const rel of batch.beforeSheets) {
    const abs = path.join(root, rel)
    // 侧别 A 用 git 读，这样"迁移前"是那个修订的真实内容，
    // 而不是某个人手工留存、可能已经过期的快照文件
    const repoRel = path.relative(path.join(root, '..'), abs).replace(/\\/g, '/')
    const css = execFileSync('git', ['show', `${rev}:${repoRel}`], { encoding: 'utf8' })
    for (const r of parseRules(css)) {
      // 已登记的搬家 / 已裁决的删除：先从这条规则的声明里摘掉（记账），
      // 摘空了就整条不再参与比对。这一步要在"类名过滤"**之前**做，因为有些被搬的
      // 声明所在的选择器根本没有类名（`:root`、`[style*=…]`），它们从来就不在 beforeRules 里。
      const afterReloc = applyRelocations(batch, rel, r.context, r.selector, r.decls, relocHitCounts)
      const afterDrops = applyResolvedDrops(batch, rel, r.context, r.selector, afterReloc, dropHitCounts)
      if (afterDrops.length === r.decls.length && r.decls.length > 0) {
        leftoverRules.push(`${rel} ${r.context || '(顶层)'} \`${r.selector}\``)
        leftoverDeclCount += r.decls.length
      }
      if (afterDrops.length === 0 && r.decls.length > 0) continue
      if (classesOf(r.selector).some((c) => oldNames.has(c))) {
        beforeRules.push({
          side: `${rel}@${rev}`,
          sheet: rel,
          context: r.context,
          selector: r.selector,
          decls: canonDecls(afterDrops),
        })
      }
    }
  }

  // ① 已登记的搬家必须在"迁移前"找得到（找不到 = 类名/值/来源样式表写错了）
  const missedRelocs = relocEntries
    .map((e, i) => ({ e, i }))
    .filter(({ i }) => relocHitCounts[i] === 0)
  if (missedRelocs.length) {
    console.error(
      `✗ ${batch.id}：relocations 里有 ${missedRelocs.length} 条声明在"迁移前"**一条都没命中**` +
        `（sheet / context / selector / prop / value 有一样对不上）。直接报错，` +
        `否则它会变成一条永远绿灯的空登记：`,
    )
    for (const { e } of missedRelocs) {
      console.error(`   - ${e.from.sheet} ${e.from.context || '(顶层)'} \`${e.from.selector}\` { ${e.from.prop}: ${e.from.value} }`)
    }
    process.exit(3)
  }
  // ② 每一条都必须真的落在目标文件里（搬丢了要在这里炸，而不是静默"搬家成功"）
  const lostRelocs = relocEntries.filter((e, i) => !relocationLands(e, relocHitCounts[i]))
  if (lostRelocs.length) {
    console.error(
      `✗ ${batch.id}：relocations 里有 ${lostRelocs.length} 条**在目标文件里找不到**` +
        `（选择器不在那条规则里，或值不同）。搬家没落地就不能从"迁移前"一侧摘掉它：`,
    )
    for (const e of lostRelocs) {
      console.error(`   - → ${e.to.file} \`${e.to.selector}\` 应有 { ${e.from.prop}: ${e.from.value} }`)
    }
    process.exit(3)
  }

  const declaredDrops = batch.resolvedConflicts || []
  const missedDrops = declaredDrops
    .map((d, i) => ({ d, i }))
    .filter(({ i }) => !dropHitCounts[i])
  if (missedDrops.length) {
    console.error(
      `✗ ${batch.id}：resolvedConflicts 里有 ${missedDrops.length} 条声明**一条都没命中**` +
        `（类名/值/来源样式表对不上）。这种"声明了却什么也没删"的条目会让差集看起来更干净，` +
        `所以直接报错：`,
    )
    for (const { d } of missedDrops) {
      console.error(`   - ${d.sheet} ${d.selector} { ${d.prop}: ${d.value} }`)
    }
    process.exit(3)
  }

  /**
   * 没有任何类名进模块的批次（序 13："把 `responsive.css` 清空"）：
   * 这一批不存在"逐条保留"这种证据 —— 它的证据是**完整性**：
   *   ① 迁移前那份样式表里的**每一条声明**都被 `relocations` / `resolvedConflicts`
   *      逐条吃掉（没有一条是"顺手不要了"）；
   *   ② 工作区里的那个样式表**真的空了**（解析出 0 条规则）。
   * 两条都过才算这一批做完；任何一条遗留都会在这里点名。
   */
  if (groupsEmpty) {
    console.log(`迁移前（git ${rev} 源码）：${batch.beforeSheets.join(', ')}`)
    console.log(`本批没有任何类名进模块（规则只是换了归属）⇒ 证据是"完整性"而不是"逐条保留"\n`)
    const notEmpty = []
    for (const rel of batch.beforeSheets) {
      const left = parseRules(fs.readFileSync(path.join(root, rel), 'utf8'))
      if (left.length) notEmpty.push(`${rel} 还剩 ${left.length} 条规则`)
    }
    if (notEmpty.length) {
      console.error('✗ 这一批声称"清空"，但工作区里还有规则：')
      for (const r of notEmpty) console.error(`   - ${r}`)
      process.exit(1)
    }
    console.log(
      `✓ 本批登记的 ${relocEntries.length} 条声明搬家全部双向自检通过、` +
        `${(batch.resolvedConflicts || []).length} 条"确认从未生效"的规则已删除`,
    )
    console.log(`✓ 工作区里的样式表已只剩注释（解析出 0 条规则）：${batch.beforeSheets.join(', ')}`)
    console.log(
      `·  这**一张**表里另有 ${leftoverRules.length} 条规则（${leftoverDeclCount} 条声明）` +
        `不归本批：它们由试点 / 第一 / 第三 / 序 5 / 序 8 / 序 9 / 序 10 各自登记并验证过 ——`,
    )
    console.log(
      `   跨批次的合并审计是一次性脚本（输出收在 docs/migration-evidence/5.6-10-empty-patch-layers.md），` +
        `本脚本只保证"本批登记的双向自检 + 工作区里那张表真的空了"。`,
    )
    console.log('\n──── 已登记的搬家：同一条声明从 A 到 B（值不变，逐条可查）────')
    for (const e of relocEntries) {
      console.log(
        `   - ${e.from.sheet} ${e.from.context || '(顶层)'} \`${e.from.selector}\` { ${e.from.prop}: ${e.from.value} }`,
      )
      console.log(`       → ${e.to.file}${e.to.context ? ` ${e.to.context}` : ''} \`${e.to.selector}\``)
      if (e.why) console.log(`       理由：${e.why}`)
    }
    console.log('\n──── 已裁决的删除（实测选择器命中 0 个元素，迁移前就从未生效）────')
    for (const d of batch.resolvedConflicts || []) {
      console.log(`   - ${d.sheet} ${d.context || '(顶层)'} \`${d.selector}\` { ${d.prop} }`)
      console.log(`       依据：${d.evidence}`)
    }
    continue
  }

  // ── 侧别 B：迁移后（dist 产物） ──
  const afterRules = []
  for (const f of distSheets) {
    const css = fs.readFileSync(path.join(assetsDir, f), 'utf8')
    for (const r of parseRules(css)) {
      const bare = stripHash(r.selector)
      // 先按"声明的选择器强化"把前缀摘掉，再判定这条规则属不属于本批
      let probe = bare
      for (const [old, prefix] of Object.entries(batch.hardened || {})) {
        const camel = kebabToCamel(old)
        probe = probe.replace(
          new RegExp(`^${escapeRe(prefix)}\\._?${escapeRe(camel)}(?![\\w-])`),
          `.${camel}`,
        )
      }
      if (!newNames.some((w) => new RegExp(`\\._?${w}(?![\\w-])`).test(probe))) continue
      afterRules.push({ side: f, context: r.context, selector: probe, decls: canonDecls(r.decls) })
    }
  }

  if (beforeRules.length === 0 || afterRules.length === 0) {
    console.error(
      `✗ 解析结果异常：迁移前 ${beforeRules.length} 条 / 迁移后 ${afterRules.length} 条。` +
        `任何一侧为 0 都说明解析或路径有问题，不能据此下"没丢"的结论。`,
    )
    process.exit(3)
  }

  const afterByKey = new Map()
  for (const r of afterRules) {
    if (!afterByKey.has(key(r))) afterByKey.set(key(r), [])
    afterByKey.get(key(r)).push(r)
  }

  console.log(`迁移前（git ${rev} 源码）：${beforeRules.length} 条`)
  console.log(`迁移后（dist 产物）：${afterRules.length} 条`)
  console.log(`迁移前来源：${[...new Set(beforeRules.map((r) => r.side))].join(', ')}`)
  console.log(`迁移后来源：${[...new Set(afterRules.map((r) => r.side))].join(', ')}\n`)

  let exact = 0
  let changed = 0
  let lost = 0
  /** 产物"多出来的"声明：只可能是构建器注入（如 esbuild 补 `-webkit-user-select`），
      属于加法而非丢失。单独统计、逐条列出，但不当作失败 —— 否则真变化会被噪音淹掉。 */
  const extraDecls = []

  /** "prop:val" 数组 → Map(prop → val) */
  const toMap = (arr) => {
    const m = new Map()
    for (const d of arr) {
      const i = d.indexOf(':')
      m.set(d.slice(0, i), d.slice(i + 1))
    }
    return m
  }

  for (const b of beforeRules) {
    let cands = afterByKey.get(key(b)) || []
    /**
     * 压缩器会把**相邻、且声明逐字相同**的规则合并成一个选择器组。
     * 第七批（序 10）实测：`Graph.module.css` 里
     *
     *   .graphToolbar { flex-wrap: wrap; row-gap: var(--space-xs) }
     *   .graphToolbarLeft, .graphToolbarRight { flex-wrap: wrap; row-gap: var(--space-xs) }
     *
     * 在产物里合成了一条
     * `._graphToolbar_h,._graphToolbarLeft_h,._graphToolbarRight_h{flex-wrap:wrap;row-gap:var(--space-xs)}`
     * —— 于是"同 (上下文 + 选择器)"这个 key 两条都落空，被报成 2 条丢失，
     * 而它们逐字都在产物里（合并的前提就是声明完全相同，语义等价）。
     *
     * 所以落空时退一步找**超集规则**：上下文相同、成员集合 ⊇ 本条、
     * 声明逐字相同。三个条件缺一不可，任何真的丢声明都仍然会走到"丢失"分支。
     * 命中时单独注明"被压缩器合并"，不让它悄悄混进"逐字保留"。
     */
    let mergedInto = null
    if (cands.length === 0 && b.decls.length > 0) {
      const members = (sel) => splitSelectors(sel).map((s) => neutralSelector(s, remap).trim())
      const mine = members(b.selector)
      const hit = afterRules.find(
        (a) =>
          a.context === b.context &&
          a.decls.join(';') === b.decls.join(';') &&
          mine.every((m) => members(a.selector).includes(m)),
      )
      if (hit) {
        cands = [hit]
        mergedInto = hit.selector
      }
    }
    if (cands.length === 0) {
      lost++
      console.log(`✗ 丢失：${key(b)}`)
      console.log(`     原声明：${b.decls.join('; ')}`)
      continue
    }
    /**
     * 这条规则来自哪个样式表、哪些属性已被"裁决"过。
     * 被裁决的属性，其胜者声明会出现在迁移后的同一条规则里 ——
     * 那属于**已经声明过的差异**（下面单独成节），不算"产物凭空多出"，
     * 否则会把 16 条已解释的条目伪装成构建器注入，把真信号淹掉。
     */
    const resolvedProps = new Set(
      (batch.resolvedConflicts || [])
        .filter((d) => d.sheet === b.sheet && d.selector === b.selector.trim())
        .map((d) => d.prop.trim().toLowerCase()),
    )
    const bm = toMap(b.decls)
    let best = null
    for (const c of cands) {
      const cm = toMap(c.decls)
      const missing = [...bm.keys()].filter((k) => !cm.has(k))
      const diff = [...bm.keys()].filter((k) => cm.has(k) && cm.get(k) !== bm.get(k))
      if (missing.length === 0 && diff.length === 0) {
        best = {
          c,
          extra: [...cm.keys()]
            .filter((k) => !bm.has(k) && !resolvedProps.has(k))
            .map((k) => `${k}:${cm.get(k)}`),
        }
        break
      }
      if (!best) best = { c, missing, diff, extra: [] }
    }
    if (!best || best.missing?.length || best.diff?.length) {
      lost++
      console.log(`✗ 丢失：${key(b)}`)
      console.log(`     原声明：${b.decls.join('; ')}`)
      if (best?.missing?.length) console.log(`     产物缺：${best.missing.join(', ')}`)
      continue
    }
    if (best.extra.length) extraDecls.push({ key: key(b), extra: best.extra })
    exact++
    console.log(
      mergedInto
        ? `✓ 逐字保留（压缩器合并了选择器组，产物里是 ${mergedInto}）：${key(b)}`
        : `✓ 逐字保留：${key(b)}`,
    )
    if (best.extra.length) {
      console.log(
        `     ※ 产物多出：${best.extra.join('; ')}` +
          `（构建器注入，或同一选择器下由"已裁决的胜者"补上的声明 —— 见下面那一节）`,
      )
    }
  }

  console.log('\n──── 新增的规则（产物有、迁移前无）────')
  const beforeKeys = new Set(beforeRules.map(key))
  const added = afterRules.filter((r) => !beforeKeys.has(key(r)))
  if (added.length === 0) console.log('（无）')
  for (const a of added) console.log(`+ ${key(a)} { ${a.decls.join('; ')} }`)

  if (extraDecls.length) {
    console.log('\n──── 产物多出的声明（加法，不是丢失；来源是构建器或胜者补上的声明）────')
    console.log(`   共 ${extraDecls.length} 条规则出现多余声明：`)
    for (const e of extraDecls) console.log(`   ${e.key} → ${e.extra.join('; ')}`)
  }

  // 从"迁移前"一侧摘掉、单独成节的**删除**：它们既不是"丢失"也不是"值有变化"。
  // 现在有两类，靠 `note` 区分：
  //   ① 序 5 的冲突裁决：按**实测胜者**删掉的输家声明（真 Chromium 的 computed style）；
  //   ② 收尾轮的死代码清理：零引用的类 / 声明（grep + 运行时拼类名扫描的证据）。
  const dropHits = declaredDrops.filter((_, i) => dropHitCounts[i])
  if (dropHits.length) {
    console.log('\n──── 从"迁移前"一侧删除的声明（逐条可查）────')
    console.log(
      `   共 ${dropHits.length} 条。分两类：① 序 5 那批是**实测胜者**裁决出来的输家声明` +
        `（一次性探针走真实渲染路径，用完已删；配方见计划 §5 雷区 3）；` +
        `② 带"死代码清理"字样的那几条是**零引用**的声明（收尾轮删的），` +
        `依据是 grep + 运行时拼类名扫描，证据 ` +
        '`docs/migration-evidence/5.6-13-dead-css-cleanup.md`：',
    )
    for (const d of dropHits) {
      console.log(`   - ${d.sheet} \`${d.selector}\` { ${d.prop}: ${d.value} }`)
      if (d.note) console.log(`       说明：${d.note}`)
      console.log(`       胜者：${d.winner}`)
      console.log(`       实测：${d.evidence}`)
    }
  }

  // 已登记的"搬家"：单独成节 —— 它们既不是丢失也不是值变化，
  // 而是"同一条声明换了个地方，值一个字没改"（含多选择器组被拆开的情形）。
  if (relocEntries.length) {
    console.log('\n──── 已登记的搬家：同一条声明从 A 到 B（值不变，逐条可查）────')
    console.log(`   共 ${relocEntries.length} 条。两个方向都自检过：`)
    console.log('   ① 在"迁移前"的源文本里真的存在；② 在目标文件里真的落在声明的选择器上。')
    for (const e of relocEntries) {
      console.log(
        `   - ${e.from.sheet} ${e.from.context || '(顶层)'} \`${e.from.selector}\` { ${e.from.prop}: ${e.from.value} }`,
      )
      console.log(`       → ${e.to.file}${e.to.context ? ` ${e.to.context}` : ''} \`${e.to.selector}\``)
      if (e.why) console.log(`       理由：${e.why}`)
    }
  }

  // 选择器强化是"声明过的差异"，单独说清楚：它改了选择器文本，
  // 但按定义**不改变任何属性的取值**（权重只升不降）。
  const hardenedUsed = Object.entries(batch.hardened || {})
  if (hardenedUsed.length) {
    console.log('\n──── 声明过的选择器强化（权重提高，属性值不变）────')
    for (const [old, prefix] of hardenedUsed) {
      const camel = kebabToCamel(old)
      const hits = afterRules.filter((r) =>
        new RegExp(`\\._?${escapeRe(camel)}(?![\\w-])`).test(r.selector),
      ).length
      console.log(
        `   ${old} → 模块里写作 \`:global(${prefix}).${camel}\`，产物里是 \`${prefix}.${camel}\`（匹配 ${hits} 条）`,
      )
    }
  }

  console.log(
    `\n批次汇总：逐字保留 ${exact} / 值有变化 ${changed} / 丢失 ${lost}` +
      (dropHits.length ? ` / 已声明删除 ${dropHits.length}（逐条见上）` : ''),
  )
  if (lost > 0) failed = true
}

// ── 动画绑定检查：引用的动画必须与定义在同一个产物文件里 ──
// 这是试点轮真正抓到 bug 的地方：`animation: scaleIn` 搬进模块后被哈希成
// `_scaleIn_<hash>`，而该 @keyframes 定义在**另一个**产物文件（index.css）里
// —— 名字对不上，动画静默消失。所以判据不是"文本像不像"，
// 而是"引用的名字在自己这个文件里有没有定义"。
console.log('\n──── 动画绑定检查（引用的动画名必须在同一产物文件内有定义）────')
const NOT_A_NAME =
  /^(none|infinite|linear|ease|ease-in|ease-out|ease-in-out|alternate|alternate-reverse|reverse|forwards|backwards|both|normal|running|paused|initial|inherit|unset|revert|steps)$/
let bindingOk = true
for (const f of distSheets) {
  const css = fs.readFileSync(path.join(assetsDir, f), 'utf8')
  const defined = new Set([...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]))
  const used = new Set()
  for (const m of css.matchAll(/animation(?:-name)?\s*:\s*([^;{}]+)/g)) {
    for (const group of m[1].split(',')) {
      for (const tok of group.trim().split(/\s+/)) {
        if (!tok || /^[\d.]/.test(tok) || NOT_A_NAME.test(tok)) continue
        if (!/^[A-Za-z_-][\w-]*$/.test(tok)) continue
        used.add(tok)
      }
    }
  }
  const dangling = [...used].filter((u) => !defined.has(u))
  if (dangling.length) {
    bindingOk = false
    console.log(`✗ ${f}: 引用但未定义 → ${dangling.join(', ')}`)
  } else if (used.size) {
    console.log(`✓ ${f}: 引用 ${used.size} 个动画，全部在本文件内有定义（${[...used].join(', ')}）`)
  }
}

// ── 动画体比对：改名不算丢，动画体必须一字不差 ──
console.log('\n──── 动画体比对（模块内改名后的动画，动画体必须与全局原版一致）────')
/**
 * ⚠️ 原版从**按内容定位的修订**里读，而不是写死 `HEAD`（收尾轮改的）。
 *
 * 收尾轮删掉了 4 个"已经没有样式表用户"的全局 `@keyframes`
 * （`scaleIn` / `cleaning-pulse` / `glowPulse` / `graph-spin`，
 * 见 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`）。
 * 写死 `HEAD` 的话，这一轮**提交之后** `git show HEAD:…/base.css` 里就没有
 * `@keyframes scaleIn` 了 ⇒ `a === null` ⇒ 这条比对会对所有后来的人报红
 * ——而规则本身没有任何问题。`graph.css` 的文件头早就记着这个陷阱
 * （"把原文删掉，提交之后那条比对就找不到原版"），收尾轮用与
 * `findRecentRev` 同一套办法解决：从 HEAD 往回找**第一个还含有这个
 * `@keyframes`** 的修订。与提交顺序、与后来删掉原版的提交都解耦。
 */
const keyframeSourceCache = new Map()
const readKeyframeSource = (rel, name) => {
  const cacheKey = `${rel}::${name}`
  if (keyframeSourceCache.has(cacheKey)) return keyframeSourceCache.get(cacheKey)
  const re = new RegExp(`@keyframes\\s+${escapeRe(name)}\\s*\\{`, 'i')
  const rev = findRecentRev((candidate) => re.test(readFromGit(path.join(root, rel), candidate)))
  if (!rev) {
    console.error(
      `✗ 从 HEAD 往回 40 个提交里找不到还含有 \`@keyframes ${name}\` 的 ${rel} —— ` +
        `登记写错了类名，或那个文件从来没有过这条动画。不能据此下"动画体一致"的结论。`,
    )
    process.exit(3)
  }
  const text = readFromGit(path.join(root, rel), rev)
  keyframeSourceCache.set(cacheKey, { rev, text })
  return keyframeSourceCache.get(cacheKey)
}
const normKeyframes = (css, name) => {
  const re = new RegExp(`@keyframes\\s+${escapeRe(name)}\\s*\\{`, 'i')
  const i = css.search(re)
  if (i < 0) return null
  let depth = 0
  let j = css.indexOf('{', i)
  const start = j
  for (; j < css.length; j++) {
    if (css[j] === '{') depth++
    else if (css[j] === '}') {
      depth--
      if (depth === 0) break
    }
  }
  return css
    .slice(start + 1, j)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s*:\s*/g, ':')
    .replace(/;\s*/g, ';')
    .replace(/\s*,\s*/g, ',')
    .replace(/^;|;$/g, '')
    .trim()
    .toLowerCase()
}
let animOk = true
let keyframeChecks = 0
for (const batch of BATCHES) {
  for (const kf of batch.keyframes) {
    keyframeChecks += 1
    const src = readKeyframeSource(kf.fromSheet, kf.orig)
    const a = normKeyframes(src.text, kf.orig)
    const moduleText = fs.readFileSync(path.join(root, kf.module), 'utf8')
    const b = normKeyframes(moduleText, kf.renamed)
    // `retired: true`（收尾轮新增）：这条动画在"死代码清理"里删掉了 ——
    // 断言**模块里也没有了**，而不是"和原版一致"。
    const same = kf.retired ? a !== null && b === null : a !== null && a === b
    if (!same) animOk = false
    console.log(
      `${same ? '✓' : '✗'} @keyframes ${kf.orig}（${kf.fromSheet}@${src.rev}）→ ` +
        `${kf.renamed}（${kf.module}）${kf.retired ? '【收尾轮已随死代码清理删除】' : ''}`,
    )
    if (!same) {
      console.log(`     全局原版：${a}`)
      console.log(`     模块现状：${b}${kf.retired ? '（期望 null：清理后不该再有）' : ''}`)
    }
  }
}
if (keyframeChecks === 0) {
  console.error('✗ 一条 @keyframes 比对都没有 —— 登记表空了，这项检查等于不存在')
  process.exit(3)
}

console.log(
  `\n════════ 汇总：丢失 ${failed ? '有' : '0'} / ` +
    `动画绑定 ${bindingOk ? '全部自洽' : '有悬空'} / 动画体一致 ${animOk ? '是' : '否'} ════════`,
)

// 动画被**有意**改名并搬进模块，所以"新增 feedbackScaleIn/cleaningPulse…"不算事故；
// 其余新增都值得看一眼，但不阻断（可能是有意的补充规则）。
process.exit(failed || !animOk || !bindingOk ? 1 : 0)
