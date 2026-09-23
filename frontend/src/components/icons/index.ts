/**
 * @file 图标图形注册表（`Icon.tsx` 的内部实现，页面**不要**直接 import 这里）
 *
 * 语义名 → 图形。图形文件本身只提供 `<path>` / `<rect>` / `<circle>`，
 * 几何参数（viewBox / 线宽 1.5 / currentColor / fill:none / round）全在 `Icon.tsx` 里统一施加 ——
 * 所以"线宽写歪了"这种事在这一层不可能发生，也不需要 13 个文件各写一遍。
 *
 * ## 为什么 `satisfies` 而不是类型注解
 *
 * 写成 `const ICONS: Record<string, ComponentType> = {...}` 会把键宽化成 `string`，
 * 于是 `type IconName = keyof typeof ICONS` 变成 `string`，未知语义名就能漏到运行期
 * （渲染成空白，且不报错）。`satisfies` 只校验"每个值都是组件"，键保持**字面量**，
 * `IconName` 因此是真正的联合类型 —— 拼错在**编译期**失败（见 `Icon.test.tsx` 的护栏用例）。
 *
 * 新增图标时的顺序：① 在 `icons/` 加图形文件 ② 在这里登记 ③ 在 `Icon.test.tsx`
 * 的 `NAV_ICON_NAMES` 里补名字（少登记会被测试逮住）。
 */
import type { ComponentType } from 'react';

// 导入顺序与 visual-design-spec §4.3 的表格一致，便于逐行对照审阅
import Dashboard from './dashboard';
import Today from './today';
import ReviewCards from './review-cards';
import Daily from './daily';
import Projects from './projects';
import Assessment from './assessment';
import Goals from './goals';
import Notes from './notes';
import Trash from './trash';
import Cards from './cards';
import Graph from './graph';
import Qa from './qa';
import Questions from './questions';
// 批次 B2 新增：不在 §4.3 的导航表格里，是"退出"与"移动端汉堡"两个动作图标
import Logout from './logout';
import Menu from './menu';

/** 全部图形：键是语义名，值是只画图形的组件 */
export const ICONS = {
  dashboard: Dashboard,
  today: Today,
  'review-cards': ReviewCards,
  daily: Daily,
  projects: Projects,
  assessment: Assessment,
  goals: Goals,
  notes: Notes,
  trash: Trash,
  cards: Cards,
  graph: Graph,
  qa: Qa,
  questions: Questions,
  logout: Logout,
  menu: Menu,
} satisfies Record<string, ComponentType>;

/** 全部合法语义名（联合类型：`Icon` 的 `name` 用它约束） */
export type IconName = keyof typeof ICONS;
