/**
 * @file 设计样板间（`/styleguide`）
 * @description 把整套设计令牌与组件状态摆在一页上，供"改完设计后一眼验收"。
 *
 * ## 为什么需要这一页（visual-refactor-plan 批次 0.2）
 *
 * 这套设计过去的问题不是"配色难看"，而是**从来没有一次决策**：
 * 令牌散在 40 个模块里、11 个令牌定义了零引用没人发现、
 * 272 处硬编码色值绕过令牌层、同一语义有两套色阶。
 * 没有一页"能看见全部材料"的地方，验收就只能靠翻 18 个页面 ——
 * 而"翻不完"正是这些冗余能长期存活的原因。
 *
 * ## 它不给用户看
 *
 * 不进 Sidebar、不进任何导航，只能手输 `/styleguide`；
 * 且只在开发构建里注册（`App.tsx` 的 `import.meta.env.DEV` 守卫），
 * 生产产物里不含它。
 *
 * ## 取值方式：读 `:root` 上的实际值
 *
 * 一律 `getComputedStyle(document.documentElement).getPropertyValue(...)`，
 * **不硬编码展示值** —— 所以改一个令牌，这一页立刻跟着变，
 * 这正是它作为验收工具的意义。
 *
 * ## 顺带当检查表用
 *
 * 清单里登记了"本计划要新增但尚未落地"的令牌，它们会显示 `未定义`。
 * 这是**有意的**：清单与实际令牌层的漂移会当场暴露，
 * 而不是等到某个页面看起来不对才被发现。
 */
import { useState } from 'react';

import styles from './StyleGuide.module.css';

interface TokenSpec {
  token: string;
  label: string;
  usage: string;
  /** 本计划新增、当前可能尚未定义（批次 A1 落地） */
  planned?: boolean;
}

const COLORS: TokenSpec[] = [
  { token: '--color-primary', label: '主色 · 墨', usage: '实底按钮 / 当前选中 / 链接' },
  { token: '--color-accent', label: '强调 · 泥金', usage: '只用于成就、AI 产出、当前项描边' },
  { token: '--color-text', label: '焦墨', usage: '标题 / 正文强调 / 关键数字' },
  { token: '--color-text-secondary', label: '重墨', usage: '正文次要 / 列表元信息' },
  { token: '--color-text-tertiary', label: '淡墨', usage: '说明性小字 / 分组标题' },
  { token: '--color-border', label: '边框', usage: '卡片 / 输入框' },
  { token: '--color-border-light', label: '分隔', usage: '分隔线 / 区块边界' },
  { token: '--color-bg', label: '宣纸底', usage: '页面底' },
  { token: '--color-surface', label: '纸白', usage: '卡片底' },
  { token: '--color-success', label: '成功', usage: '完成 / 已掌握 / 通过' },
  { token: '--color-warning', label: '警告', usage: '待处理 / 到期提醒' },
  { token: '--color-error', label: '错误', usage: '失败 / 危险操作' },
];

const TYPE_SCALE: TokenSpec[] = [
  { token: '--text-2xs', label: '11.2px', usage: '徽章 / 极小标注', planned: true },
  { token: '--text-xs', label: '12px', usage: '元信息 / 次要文字', planned: true },
  {
    token: '--text-sm',
    label: '12.8px',
    usage: 'UI 主体 / 按钮 / 表单（现状 104 处）',
    planned: true,
  },
  { token: '--text-sm-alt', label: '13.6px', usage: '⚠️ 存量专用，勿新增', planned: true },
  { token: '--text-base', label: '14px', usage: '列表正文', planned: true },
  { token: '--text-base-alt', label: '14.4px', usage: '⚠️ 存量专用，勿新增', planned: true },
  { token: '--text-md', label: '16px', usage: '阅读正文 / Markdown', planned: true },
  { token: '--text-lg', label: '17.6px', usage: '区块标题', planned: true },
  { token: '--text-xl', label: '24px', usage: '页面标题（统一值）', planned: true },
  { token: '--text-2xl', label: '32px', usage: '统计大数字', planned: true },
];

const SPACING: TokenSpec[] = [
  { token: '--space-2xs', label: '2px', usage: '细微分隔', planned: true },
  { token: '--space-xs', label: '4px', usage: '紧邻元素' },
  { token: '--space-sm', label: '8px', usage: '按钮内边距 / 行内间隔' },
  { token: '--space-md', label: '16px', usage: '卡片内边距 / 网格间距' },
  { token: '--space-lg', label: '24px', usage: '区块之间' },
  { token: '--space-xl', label: '32px', usage: '大区块之间' },
  { token: '--space-2xl', label: '48px', usage: '页面级留白' },
];

const RADII: TokenSpec[] = [
  { token: '--radius-sm', label: 'sm', usage: '输入框 / 按钮 / 徽章' },
  { token: '--radius-md', label: 'md', usage: '卡片' },
  { token: '--radius-lg', label: 'lg', usage: '面板 / 对话框' },
  { token: '--radius-full', label: 'full', usage: '胶囊 / 圆标' },
];

const SHADOWS: TokenSpec[] = [
  { token: '--shadow-sm', label: 'sm', usage: '卡片静置' },
  { token: '--shadow-md', label: 'md', usage: '卡片 hover / 下拉' },
  { token: '--shadow-lg', label: 'lg', usage: '对话框 / 浮层' },
  { token: '--shadow-focus', label: 'focus', usage: '统一的聚焦光环', planned: true },
];

const MOTION: TokenSpec[] = [
  { token: '--duration-fast', label: '150ms', usage: 'hover / 颜色变化', planned: true },
  { token: '--duration-base', label: '250ms', usage: '展开 / 淡入', planned: true },
  { token: '--duration-slow', label: '400ms', usage: '页面进入 / 抽屉', planned: true },
  { token: '--ease-out-expo', label: 'ease-out-expo', usage: '全站主力曲线（现状 45 处）' },
];

/** 要读取的全部令牌（模块级常量：引用稳定，effect 不会每次渲染都跑） */
const ALL_TOKENS: string[] = [
  ...COLORS,
  ...TYPE_SCALE,
  ...SPACING,
  ...RADII,
  ...SHADOWS,
  ...MOTION,
].map((spec) => spec.token);

/** 读 `:root` 上的实际值；读不到就是空串（渲染成"未定义"） */
function readTokenValues(): Record<string, string> {
  const computed = getComputedStyle(document.documentElement);
  const next: Record<string, string> = {};
  ALL_TOKENS.forEach((token) => {
    next[token] = computed.getPropertyValue(token).trim();
  });
  return next;
}

export default function StyleGuide() {
  // 用 `useState` 的**惰性初始化**读值，而不是 `useEffect` + `setState`：
  // 这里读的是 `:root` 上的静态样式，而 `main.tsx` 把全部样式表排在应用组件
  // **之前**导入，首次渲染时样式已经就位 —— 没有"先渲染空值、再补一次"的必要。
  // 那也正是 `react-hooks/set-state-in-effect` 拒绝的写法（会造成级联渲染）。
  const [values] = useState<Record<string, string>>(readTokenValues);

  /** 空值渲染成醒目的"未定义"，让清单漂移当场可见 */
  function valueOf(token: string): string {
    const value = values[token];
    if (value === undefined) return '…';
    return value === '' ? '未定义' : value;
  }

  function isMissing(token: string): boolean {
    return values[token] === '';
  }

  return (
    <div className="page-enter">
      <h1 className="heading-serif" style={{ fontSize: '1.5rem', marginBottom: 4 }}>
        设计样板间
      </h1>
      <p className={styles.lead}>
        这一页不进导航、不进生产产物。它的作用是<strong>一眼验收</strong>
        ：改一个令牌，这里立刻跟着变。 标记「未定义」的是本计划要新增、尚未落地的令牌 ——
        它们的出现与消失本身就是检查。
      </p>

      {/* ── 色彩 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>色彩 · 墨分五色</h2>
        <p className={styles.note}>
          层次靠同一墨色的不同浓度表达，而不是靠渐变过渡（设计规范 §1）。
        </p>
        <div className={styles.swatchGrid}>
          {COLORS.map((spec) => (
            <div key={spec.token} className={styles.swatch}>
              <div
                className={styles.swatchChip}
                style={{ background: `var(${spec.token})` }}
                aria-hidden="true"
              />
              <div className={styles.swatchMeta}>
                <div className={styles.tokenName}>{spec.label}</div>
                <code className={styles.tokenValue}>{spec.token}</code>
                <div className={styles.tokenValue}>{valueOf(spec.token)}</div>
                <div className={styles.usage}>{spec.usage}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── 字号 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>字号</h2>
        <p className={styles.note}>
          值 <strong>逐字等于现状</strong>：本轮只给字号建名字，不做任何位移（设计规范 §5.1）。
        </p>
        <div className={styles.rows}>
          {TYPE_SCALE.map((spec) => (
            <div key={spec.token} className={styles.typeRow}>
              <span className={styles.rowMeta}>
                <code className={styles.tokenValue}>{spec.token}</code>
                <span className={styles.usage}>{spec.usage}</span>
              </span>
              <span className={styles.typeSample} style={{ fontSize: `var(${spec.token})` }}>
                永久记忆的印迹 Engram
              </span>
              <span className={isMissing(spec.token) ? styles.missing : styles.tokenValue}>
                {valueOf(spec.token)}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* ── 间距 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>间距</h2>
        <div className={styles.rows}>
          {SPACING.map((spec) => (
            <div key={spec.token} className={styles.spaceRow}>
              <code className={styles.tokenValue}>{spec.token}</code>
              <span
                className={styles.spaceBar}
                style={{ width: `var(${spec.token})` }}
                aria-hidden="true"
              />
              <span className={isMissing(spec.token) ? styles.missing : styles.tokenValue}>
                {valueOf(spec.token)}
              </span>
              <span className={styles.usage}>{spec.usage}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── 圆角 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>圆角</h2>
        <div className={styles.inlineRow}>
          {RADII.map((spec) => (
            <div key={spec.token} className={styles.specimen}>
              <div
                className={styles.radiusBox}
                style={{ borderRadius: `var(${spec.token})` }}
                aria-hidden="true"
              />
              <code className={styles.tokenValue}>{spec.token}</code>
              <span className={styles.usage}>
                {valueOf(spec.token)} · {spec.usage}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* ── 阴影 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>阴影</h2>
        <div className={styles.inlineRow}>
          {SHADOWS.map((spec) => (
            <div key={spec.token} className={styles.specimen}>
              <div
                className={styles.shadowBox}
                style={{ boxShadow: `var(${spec.token}, 0 0 0 2px #c0392b)` }}
                aria-hidden="true"
              />
              <code className={styles.tokenValue}>{spec.token}</code>
              <span className={isMissing(spec.token) ? styles.missing : styles.usage}>
                {valueOf(spec.token)}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* ── 动效 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>动效</h2>
        <p className={styles.note}>
          时长令牌是本计划新增（现状 17 处动画用了 9 个不同时长，且没有任何时长令牌）。
        </p>
        <div className={styles.rows}>
          {MOTION.map((spec) => (
            <div key={spec.token} className={styles.spaceRow}>
              <code className={styles.tokenValue}>{spec.token}</code>
              <span className={isMissing(spec.token) ? styles.missing : styles.tokenValue}>
                {valueOf(spec.token)}
              </span>
              <span className={styles.usage}>{spec.usage}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── 占位：后续批次补齐 ── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>待补（后续批次）</h2>
        <ul className={styles.todoList}>
          <li>
            <strong>图标表</strong> —— 30 个自绘图标按语义分组（批次 B1/B3、F1）。
            排成表之后，"缺哪个语义、哪个线宽不对"一眼可见。
          </li>
          <li>
            <strong>组件状态矩阵</strong> —— 按钮 4 变体 × 4 状态（默认 / hover / focus-visible /
            disabled）、输入框 3 态、徽章、卡片（批次 D 之后）。
          </li>
          <li>
            <strong>对话框演示</strong> —— 焦点陷阱、Esc、遮罩统一后的样子（批次 D1/D2）。
          </li>
          <li>
            <strong>页面标题</strong> —— `&lt;PageHeader&gt;` 统一后的五个层级（批次 C1）。
          </li>
        </ul>
      </section>
    </div>
  );
}
