/**
 * 级联求解的小工具（overhaul-plan 5.6 收尾轮新增）
 *
 * ## 为什么需要它
 *
 * 5.6 的三道既有检查各有一个**结构性的**盲区，两个都是靠一次性探针才发现的
 * （见 `docs/css-migration-plan.md` §5 雷区 12、§7.0 第 3/4 条）：
 *
 *  1. **跨媒体查询的覆盖战**：`css-migration-diff.mjs` 按
 *     `(上下文 + 选择器 + 属性)` 建键，于是"`@media` 里一条 + 顶层一条、
 *     同选择器同属性"落在**两个键**上，谁赢看不见；
 *  2. **简写 vs 长写**：三条检查全都按**属性名**配对，
 *     `border`（简写，展开出 `border-left-*`）与 `border-left`（长写）
 *     是两个名字，配不上；选择器往往也不同。
 *
 * 两者其实是同一件事：**在产物上做一次"权重 + 源序"的真实求解**。
 * 所以这里把求解需要的东西抽成一份（与解析器同样只有一份实现，
 * 见 `lib/css-parse.mjs` 的文件头）：
 *
 *  - `specificity` / `cmpSpec`：本项目用到的选择器形态的权重；
 *  - `SHORTHAND_TOUCHES` / `touchedLonghands`：简写会**写到**哪些长写属性；
 *  - `collectCascadeDecls`：把产物拆成"一条声明一行"的扁平表（带文件序、文件内序、上下文）；
 *  - `pickWinner`：按权重 → 源序算得主；**跨懒加载 chunk 时返回 `unknown`**
 *    （那种情况静态判不了，必须显式报出来，见 `cascadeRank` 的注释）。
 *
 * ⚠️ 这里**不**重复实现 CSS 解析：规则切分一律走 `lib/css-parse.mjs`。
 */
import { parseRules, splitSelectors } from './css-parse.mjs';

/** 剥掉 CSS Modules 的哈希后缀：`_qaAiCard_1lr0k_43` → `_qaAiCard`（同 verify-built-css） */
export const stripHash = (s) => s.replace(/_([0-9a-z]{5})_\d+(?![0-9a-z])/g, '');

/** 选择器 / 值的空白归一（只用于比较，不用于展示原值） */
export const normText = (s) => s.replace(/\s+/g, ' ').trim();

/**
 * 极简权重计算：只处理本项目用到的形态
 * （元素 / `.类` / `#id` / `:伪类` / `::伪元素` / `[属性]` / `:not(...)` 不展开）。
 * 返回 [id, class, type] 三元组，逐个比大小。
 *
 * 与 `verify-built-css.mjs` 里那份逐字相同 —— 原来的那份留在原处不动，
 * 这里抽出来是给新检查用的；两处都只服务于"同一个元素上的两条规则"，
 * 不追求覆盖 `:is()` / `:where()` 之外的现代选择器。
 */
export function specificity(sel) {
  const s = sel.replace(/:where\([^)]*\)/g, ''); // :where() 权重为 0
  const ids = (s.match(/#[\w-]+/g) || []).length;
  const classes =
    (s.match(/\.[\w-]+/g) || []).length +
    (s.match(/\[[^\]]*\]/g) || []).length +
    (s.match(/:(?!:)[\w-]+/g) || []).length;
  const pseudoEls = (s.match(/::[\w-]+/g) || []).length;
  const types = (s.match(/(^|[\s>+~(,])([a-zA-Z][\w-]*)/g) || []).length + pseudoEls;
  return [ids, classes, types];
}

export const cmpSpec = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

/**
 * 简写属性 → 它**会写的长写属性**清单。
 *
 * ## 为什么只需要"写到哪些长写"，不需要展开出具体值
 *
 * 竞争判据是"两条声明会不会落在同一个长写属性上"，而不是"谁的值更大"：
 * 只要简写 `border` 与长写 `border-left` 命中同一个元素，两者就会争
 * `border-left-width / -style / -color` 三条长写，胜负**只由权重 + 源序决定**。
 * 真要把 `border: 1px solid var(--x)` 拆成三条长写的值，得实现一遍 CSS 简写
 * 语法（还要解析 `var()`），而那是**另一件事**（值级求解），
 * 本检查只需要"存在竞争 + 谁赢"。
 *
 * 覆盖面刻意只到"真实会写出来的"这几族：border / padding / margin /
 * background / font / inset（任务点名的六族）+ border-radius / gap / flex /
 * overflow / transition / text-decoration / list-style / outline。
 * **没有**做 CSS 全量简写表 —— 一份又长又没人维护的表只会变成噪音源；
 * 缺哪些族写在 `css-convention.md` §7 的盲区一节里。
 */
export const SHORTHAND_TOUCHES = {
  border: [
    'border-top-width',
    'border-right-width',
    'border-bottom-width',
    'border-left-width',
    'border-top-style',
    'border-right-style',
    'border-bottom-style',
    'border-left-style',
    'border-top-color',
    'border-right-color',
    'border-bottom-color',
    'border-left-color',
  ],
  'border-width': [
    'border-top-width',
    'border-right-width',
    'border-bottom-width',
    'border-left-width',
  ],
  'border-style': [
    'border-top-style',
    'border-right-style',
    'border-bottom-style',
    'border-left-style',
  ],
  'border-color': [
    'border-top-color',
    'border-right-color',
    'border-bottom-color',
    'border-left-color',
  ],
  'border-top': ['border-top-width', 'border-top-style', 'border-top-color'],
  'border-right': ['border-right-width', 'border-right-style', 'border-right-color'],
  'border-bottom': ['border-bottom-width', 'border-bottom-style', 'border-bottom-color'],
  'border-left': ['border-left-width', 'border-left-style', 'border-left-color'],
  padding: ['padding-top', 'padding-right', 'padding-bottom', 'padding-left'],
  margin: ['margin-top', 'margin-right', 'margin-bottom', 'margin-left'],
  inset: ['top', 'right', 'bottom', 'left'],
  background: [
    'background-color',
    'background-image',
    'background-repeat',
    'background-position',
    'background-size',
    'background-attachment',
    'background-clip',
    'background-origin',
  ],
  font: [
    'font-style',
    'font-variant',
    'font-weight',
    'font-stretch',
    'font-size',
    'line-height',
    'font-family',
  ],
  'border-radius': [
    'border-top-left-radius',
    'border-top-right-radius',
    'border-bottom-right-radius',
    'border-bottom-left-radius',
  ],
  gap: ['row-gap', 'column-gap'],
  flex: ['flex-grow', 'flex-shrink', 'flex-basis'],
  overflow: ['overflow-x', 'overflow-y'],
  transition: [
    'transition-property',
    'transition-duration',
    'transition-timing-function',
    'transition-delay',
  ],
  'text-decoration': [
    'text-decoration-line',
    'text-decoration-style',
    'text-decoration-color',
    'text-decoration-thickness',
  ],
  'list-style': ['list-style-type', 'list-style-position', 'list-style-image'],
  outline: ['outline-width', 'outline-style', 'outline-color'],
};

/** 所有"长写"属性 → 它属于哪几族简写（`border-left-width` → `['border', 'border-width', 'border-left']`） */
const LONGHAND_FAMILIES = new Map();
for (const [short, longs] of Object.entries(SHORTHAND_TOUCHES)) {
  for (const l of longs) {
    if (!LONGHAND_FAMILIES.has(l)) LONGHAND_FAMILIES.set(l, []);
    LONGHAND_FAMILIES.get(l).push(short);
  }
}

/** 这个属性是不是"简写"（表里有）；是则返回它会写的长写清单，否则 null */
export function touchedLonghands(prop) {
  return SHORTHAND_TOUCHES[prop.trim().toLowerCase()] || null;
}

/** 这个属性是不是"长写"（会被某族简写覆盖）；是则返回覆盖它的简写清单，否则 null */
export function coveringShorthands(prop) {
  return LONGHAND_FAMILIES.get(prop.trim().toLowerCase()) || null;
}

/**
 * 两条声明的竞争关系。
 *
 * 判据是**展开后的长写属性集合有交集**，而不是"属性名谁包含谁"：
 *   - `border`（12 条长写） vs `border-left`（3 条长写）→ 交集 3 条 ⇒ 竞争
 *     （**这就是雷区 12 的实例**：`border: 1px solid` 与 `border-left: 3px solid`。
 *     只按"长写名单里有没有 `border-left`"判会漏掉它 —— 因为 `border-left`
 *     自己也是简写，不在 `border` 的长写名单里。第一版就是这么漏的）；
 *   - `border` vs `border-left-width` → 交集 1 条 ⇒ 竞争；
 *   - `padding` vs `padding-left` → 竞争；
 *   - `background` vs `padding` → 交集空 ⇒ 无关；
 *   - **同名属性**（`padding` vs `padding`）不算 —— 那是普通覆盖，
 *     既有冲突统计按属性名已经看得见。
 *
 * `shortDecl` / `longDecl` 只是**报告用的名字**：展开集合更大的那条叫 short
 * （"更简"），两条都是简写时取更大的那条；一样大就取先出现的。
 *
 * @returns {{longhands: string[], shortDecl: object, longDecl: object} | null}
 */
export function shorthandClash(a, b) {
  if (a.prop === b.prop) return null;
  const setA = new Set(touchedLonghands(a.prop) || [a.prop]);
  const setB = new Set(touchedLonghands(b.prop) || [b.prop]);
  const longhands = [...setA].filter((p) => setB.has(p));
  if (longhands.length === 0) return null;
  const [shortDecl, longDecl] = setA.size >= setB.size ? [a, b] : [b, a];
  return { longhands, longhand: longhands.join('/'), shortDecl, longDecl };
}

/**
 * 产物级联次序的模型。
 *
 * ## 为什么不能只按"文件名字典序"
 *
 * Vite 把 CSS 分成两类：
 *   - **静态**引入的样式表（`main.tsx` 里的 `./styles/*.css` + 静态组件的模块）
 *     全部打进 `index-<hash>.css`，在 `<head>` 里；
 *   - **懒加载**页面/组件的模块 CSS 各自一个 chunk，
 *     由 `__vitePreload` 在运行时**注入到 `index.css` 之后**
 *     （计划 §5 雷区 8 的补充：所以模块规则靠源序压过全局 `.btn` 是对的）。
 *
 * 于是：
 *   - `index.css` ↔ 任意 chunk：**chunk 在后**，得主是 chunk（这条是实测过的，
 *     第三批的 jsdom 探针 + 计划 §5 雷区 12 的结论都依赖它）；
 *   - 两个不同 chunk 之间：**静态判不了** —— 谁先被注入取决于路由先加载谁，
 *     这里返回 `null`（调用方必须把它当作"判不了"报出来，不能猜）。
 */
export function fileRank(fileName) {
  return /^index[-.]/.test(fileName) ? 0 : 1;
}

/**
 * 得主判定。
 * @returns {'a'|'b'|'unknown'} a/b = 哪条声明赢；unknown = 权重与源序都不足以判定
 */
export function pickWinner(a, b) {
  const sp = cmpSpec(specificity(a.sel), specificity(b.sel));
  if (sp !== 0) return sp > 0 ? 'a' : 'b';
  const ra = fileRank(a.file);
  const rb = fileRank(b.file);
  if (ra !== rb) return ra > rb ? 'a' : 'b'; // index.css 在前 ⇒ chunk 赢
  if (a.file !== b.file) return 'unknown'; // 两个懒加载 chunk：静态判不了
  return a.order > b.order ? 'a' : 'b'; // 同文件：源序后者胜
}

/**
 * 把产物拆成"一条声明一行"的扁平表（**只有调用方给的顺序**才有意义）。
 * @param {{name: string, css: string}[]} sheets 已按级联顺序排好的产物样式表
 * @returns {{file:string, order:number, context:string, sel:string, prop:string, val:string}[]}
 */
export function collectCascadeDecls(sheets) {
  const out = [];
  for (const s of sheets) {
    let order = 0;
    for (const r of parseRules(s.css)) {
      // at-rule（`@font-face` / `@keyframes`）不是选择器
      if (r.selector.trim().startsWith('@')) continue;
      for (const sel of splitSelectors(r.selector)) {
        if (sel.trim().startsWith('@')) continue;
        order += 1;
        for (const [p, v] of r.decls) {
          out.push({
            file: s.name,
            order,
            context: r.context,
            sel: normText(stripHash(sel)),
            prop: p.trim().toLowerCase(),
            val: normText(v),
          });
        }
      }
    }
  }
  return out;
}

/**
 * 一个选择器是不是"单类选择器"（`.foo` / `._foo_hash`，剥哈希后恰好一个类名、
 * 没有后代、没有伪类伪元素、没有元素名）。
 *
 * 跨选择器的简写竞争（`.card` × `.qaAiCard`）只在"两条单类规则命中同一个元素"
 * 时才是真竞争 —— 而"命中同一个元素"这件事由 TSX 的 `className` 提供证据
 * （见 `coAppliedClassGroups`）。带 `:hover` / 后代的选择器要不要一起算，
 * 是另一个判断（会引入"悬停时才竞争"的语义），本检查**刻意只做基态**，
 * 这条限制写在 `css-convention.md` §7 的盲区里。
 */
export function isSingleClassSelector(sel) {
  return /^\._?[A-Za-z][\w-]*$/.test(normText(sel));
}

/** 从 TSX 源码里抽"同一个元素上并列了哪几个类名"（跨选择器竞争的证据来源） */
export function coAppliedClassGroups(tsxSources) {
  const groups = [];
  for (const { file, text } of tsxSources) {
    for (const m of text.matchAll(/className\s*=/g)) {
      const start = m.index + m[0].length;
      const expr = readJsxAttributeValue(text, start);
      if (!expr) continue;
      const refs = extractClassRefs(expr.value);
      if (refs.length >= 2) {
        groups.push({
          file,
          line: text.slice(0, start).split('\n').length,
          snippet: normText(expr.value).slice(0, 120),
          refs,
        });
      }
    }
  }
  return groups;
}

/** 从 `className=` 之后读出属性值（`"..."` / `'...'` / `{ ... }` 括号配对） */
function readJsxAttributeValue(text, i) {
  while (i < text.length && /\s/.test(text[i])) i++;
  const q = text[i];
  if (q === '"' || q === "'") {
    const end = text.indexOf(q, i + 1);
    if (end < 0) return null;
    return { value: text.slice(i, end + 1) };
  }
  if (q !== '{') return null;
  let depth = 0;
  for (let j = i; j < text.length; j++) {
    if (text[j] === '{') depth++;
    else if (text[j] === '}') {
      depth--;
      if (depth === 0) return { value: text.slice(i, j + 1) };
    }
  }
  return null;
}

/**
 * 从一个 JSX 属性值表达式里抽出类名引用。
 *
 * 认三种写法（本项目实际用到的全部形态，第三节的"组合"约定）：
 *   1. 字面量：`className="btn btn-primary"`；
 *   2. 模板块：`` className={`card ${styles.x}`} ``（`styles.` 引用 + 字面段）；
 *   3. 查表：`` className={`${styles.a} ${LOOKUP[k]}`} `` —— `LOOKUP` 的值
 *      在 TSX 里是 `styles.*`，**这一层拿不到**，所以只抽出 `styles.a`
 *      （少认一个类名会让"竞争"漏报，不会误报）。
 *
 * ⚠️ **部分 token 的处理**：模板里 `` `badge-${type}` `` 的字面段是
 * `badge-`（半截），直接当类名会得到一个不存在的类。所以：字面段的**最后一个**
 * token 若后面紧跟 `${` 且**段尾没有空白**，就标成"可疑"，
 * 由调用方拿产物类名表核对 —— 存在同名类（如 `filter-pill${…}` 的 `filter-pill`）
 * 就留，不存在（如 `badge-`）就丢。宁可漏报也不误报。
 */
export function extractClassRefs(expr) {
  const refs = []; // { kind: 'global'|'module', name: string, partial: boolean }

  // ① 引号字符串字面量：`className="btn btn-primary"`、`' card'`、
  //    以及三元分支里的 `' filter-pill-active'`（在 `${}` 里面也要认出来）
  for (const m of expr.matchAll(/'([^'\\]*)'|"([^"\\]*)"/g)) {
    const text = m[1] !== undefined ? m[1] : m[2];
    for (const t of tokensOf(text)) refs.push({ kind: 'global', name: t, partial: false });
  }

  // ② 模板字符串：`${}` **之外**的原文段按"有没有紧贴 `${` / `}`"标可疑；
  //    `${}` 里面的引号字面量已由 ① 覆盖，`styles.x` 引用单独抽。
  for (const m of expr.matchAll(/`([\s\S]*?)`/g)) {
    const body = m[1];
    const parts = body.split(/\$\{[^}]*\}/);
    const dynamicCount = (body.match(/\$\{[^}]*\}/g) || []).length;
    parts.forEach((part, idx) => {
      const toks = tokensOf(part);
      toks.forEach((t, ti) => {
        const isFirst = ti === 0;
        const isLast = ti === toks.length - 1;
        // 左边紧贴 `${…}` 的结束、或右边紧贴 `${` 开始（且段尾无空白）⇒ 半截 token
        const gluedLeft = isFirst && idx > 0 && !/^\s/.test(part);
        const gluedRight = isLast && idx < dynamicCount && !/\s$/.test(part);
        refs.push({ kind: 'global', name: t, partial: gluedLeft || gluedRight });
      });
    });
  }

  // ③ `styles.x` / `styles['x']` 之外的一切模块类名引用
  for (const m of expr.matchAll(/styles\.([A-Za-z_$][\w$]*)/g)) {
    refs.push({ kind: 'module', name: m[1], partial: false });
  }

  // 去重：同名以"确定"的那一次为准（partial 只是"待核对"）
  const byKey = new Map();
  for (const r of refs) {
    const k = `${r.kind}:${r.name}`;
    const prev = byKey.get(k);
    if (!prev || (prev.partial && !r.partial)) byKey.set(k, r);
  }
  return [...byKey.values()];
}

const CLASS_TOKEN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
function tokensOf(text) {
  return text
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter((t) => t && CLASS_TOKEN.test(t));
}

/**
 * **跨媒体查询覆盖战**（收尾轮新增的检查之一）。
 *
 * 判据：**同选择器 + 同属性**，一条在 `@media` 里、一条在顶层，
 * 两条值不同，而**媒体查询那条在源序上更靠前** ⇒ 它在自己的断点上
 * 永远被顶层那条压掉。媒体查询**不增加权重**，这是纯源序的胜负。
 *
 * 实例（迁移前就存在）：`markdown.css` 768px 档的
 * `.markdown-body .katex { font-size: 1em }` 与 `markdown-extras.css` 顶层的 `1.1em`
 * 权重同为 (0,2,0)，后者在产物里更靠后 ⇒ 窄屏字号从未生效（计划 §4.7）。
 *
 * @returns {{candidates:number, findings:object[]}} candidates 是"考虑了这么多对"，
 *   为 0 说明这项检查**没起到作用**（调用方必须报错，不能当成"没问题"）。
 */
export function findMediaWars(decls) {
  const byKey = new Map();
  for (const d of decls) {
    const k = `${d.sel} :: ${d.prop}`;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(d);
  }
  let candidates = 0;
  const findings = [];
  for (const [key, list] of byKey) {
    const tops = list.filter((d) => d.context === '');
    const medias = list.filter((d) => d.context !== '');
    if (!tops.length || !medias.length) continue;
    for (const m of medias) {
      for (const t of tops) {
        if (m.val === t.val) continue; // 值一样就没有"谁赢"的问题
        candidates += 1;
        const w = pickWinner(m, t); // 'a' = 媒体查询那条
        if (w === 'a') continue; // 媒体查询那条靠源序取胜 —— 这正是它该有的样子
        findings.push({
          key,
          sel: m.sel,
          prop: m.prop,
          media: { context: m.context, val: m.val, file: m.file, order: m.order },
          top: { val: t.val, file: t.file, order: t.order },
          winner: w === 'b' ? 'top' : 'unknown',
        });
      }
    }
  }
  return { candidates, findings };
}

/**
 * **简写 vs 长写竞争 —— 同选择器那一半**（收尾轮新增的检查之二）。
 *
 * 判据：同一个（选择器 + 上下文）下出现两条声明，一条是简写（如 `border`）、
 * 另一条正好是它会写的长写（如 `border-left`）⇒ 两条争同一批长写属性，
 * 胜负只由源序决定（权重相同时）。
 *
 * 同一选择器的竞争**读源码就能看出来**（顺序就在眼前），所以这一半的价值不是
 * "发现"，而是**逐条登记**：谁是得主、被压掉的那条是不是有意为之。
 * 真正看不见的是跨选择器那一半，见 `findCrossClassShorthandClashes`。
 *
 * ⚠️ 同名属性（`padding` vs `padding`）**不算**这里要报的形态 —— 那是普通覆盖。
 *
 * @returns {{candidates:number, findings:object[]}}
 */
export function findShorthandClashes(decls) {
  const byKey = new Map();
  for (const d of decls) {
    const k = `${d.sel} :: ${d.context}`;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(d);
  }
  let candidates = 0;
  const findings = [];
  for (const [key, list] of byKey) {
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const clash = shorthandClash(list[i], list[j]);
        if (!clash) continue;
        candidates += 1;
        const { shortDecl, longDecl, longhand } = clash;
        const w = pickWinner(shortDecl, longDecl);
        findings.push({
          key,
          sel: shortDecl.sel,
          context: shortDecl.context,
          longhand,
          shorthand: {
            prop: shortDecl.prop,
            val: shortDecl.val,
            file: shortDecl.file,
            order: shortDecl.order,
          },
          long: {
            prop: longDecl.prop,
            val: longDecl.val,
            file: longDecl.file,
            order: longDecl.order,
          },
          winner: w === 'a' ? 'shorthand' : w === 'b' ? 'longhand' : 'unknown',
        });
      }
    }
  }
  return { candidates, findings };
}

/**
 * **简写 vs 长写竞争 —— 跨选择器那一半**（雷区 12 的常设版）。
 *
 * 实例：`` className={`card ${styles.qaAiCard}`} `` —— 全局 `.card` 写
 * `border: 1px solid …`（简写），模块 `.qaAiCard` 写
 * `border-left: 3px solid …`（长写）。两者权重同为 (0,1,0)，选择器不同，
 * 谁赢**只看产物里的先后**（模块 chunk 在 `index.css` 之后注入 ⇒ 长写赢）。
 * 三条既有检查全都看不见它（计划 §5 雷区 12 有逐条说明）。
 *
 * 判据链：① TSX 里这两个类名**并列在同一个 `className` 上**（`coGroups` 提供证据）；
 * ② 产物里各有一条**单类规则**；③ 两条规则是简写/长写关系。
 * 缺任何一条都不成立 —— 尤其①，没有它"两个类是否会命中同一个元素"就是猜。
 *
 * @param {object[]} decls collectCascadeDecls 的产物
 * @param {{refs:{kind:string,name:string}[], file:string, line:number, snippet:string}[]} coGroups
 * @param {(ref:object)=>string|null} lookup 类名引用 → 产物里的**单类** key（`card` / `_qaAiCard`）；
 *   返回 null = 产物里没有这个单类规则（半截 token 就靠这一步被丢掉）
 */
export function findCrossClassShorthandClashes(decls, coGroups, lookup) {
  const byClass = new Map();
  for (const d of decls) {
    if (d.context !== '' || !isSingleClassSelector(d.sel)) continue;
    const key = d.sel.replace(/^\./, '');
    if (!byClass.has(key)) byClass.set(key, []);
    byClass.get(key).push(d);
  }
  let candidates = 0;
  const findings = [];
  for (const g of coGroups) {
    const resolved = g.refs
      .map((ref) => {
        const key = lookup(ref);
        if (!key || !byClass.has(key)) return null;
        return { ref, key, decls: byClass.get(key) };
      })
      .filter(Boolean);
    for (let i = 0; i < resolved.length; i += 1) {
      for (let j = i + 1; j < resolved.length; j += 1) {
        const A = resolved[i];
        const B = resolved[j];
        for (const a of A.decls) {
          for (const b of B.decls) {
            const clash = shorthandClash(a, b);
            if (!clash) continue;
            candidates += 1;
            const { shortDecl, longDecl, longhand } = clash;
            const w = pickWinner(shortDecl, longDecl);
            findings.push({
              key: `${A.key} × ${B.key} :: ${longhand}`,
              where: `${g.file}:${g.line}`,
              snippet: g.snippet,
              classes: [A.key, B.key],
              longhand,
              shorthand: {
                prop: shortDecl.prop,
                val: shortDecl.val,
                sel: shortDecl.sel,
                file: shortDecl.file,
                order: shortDecl.order,
              },
              long: {
                prop: longDecl.prop,
                val: longDecl.val,
                sel: longDecl.sel,
                file: longDecl.file,
                order: longDecl.order,
              },
              winner: w === 'a' ? 'shorthand' : w === 'b' ? 'longhand' : 'unknown',
            });
          }
        }
      }
    }
  }
  return { candidates, findings };
}
