/**
 * @file 图标图形注册表（`Icon.tsx` 的内部实现，页面**不要**直接 import 这里）
 *
 * 语义名 → 图形。图形文件本身只提供 `<path>` / `<rect>` / `<circle>`，
 * 几何参数（viewBox / 线宽 1.5 / currentColor / fill:none / round）全在 `Icon.tsx` 里统一施加 ——
 * 所以"线宽写歪了"这种事在这一层不可能发生，也不需要几十个文件各写一遍。
 *
 * ## 为什么 `satisfies` 而不是类型注解
 *
 * 写成 `const ICONS: Record<string, ComponentType> = {...}` 会把键宽化成 `string`，
 * 于是 `type IconName = keyof typeof ICONS` 变成 `string`，未知语义名就能漏到运行期
 * （渲染成空白，且不报错）。`satisfies` 只校验"每个值都是组件"，键保持**字面量**，
 * `IconName` 因此是真正的联合类型 —— 拼错在**编译期**失败（见 `Icon.test.tsx` 的护栏用例）。
 *
 * 新增图标时的顺序：① 在 `icons/` 加图形文件 ② 在这里登记 ③ 在 `Icon.test.tsx`
 * 的 `ICON_NAMES` 里补名字（少登记会被测试逮住）。
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

// ── 批次 B3 新增（一）：任务表点名的 17 个 ──
import Upload from './upload';
import Delete from './delete';
import Edit from './edit';
import Search from './search';
import Add from './add';
import Close from './close';
import Filter from './filter';
import Due from './due';
import Processing from './processing';
import Ai from './ai';
import Success from './success';
import Warning from './warning';
// `error.tsx` 的组件名就叫 `Error`（与文件名逐字对应），在**本文件**里改名导入：
// 这个文件不抛异常，所以让 `Error` 在模块作用域里被遮蔽没有收益，只有困惑。
import ErrorIcon from './error';
import Quote from './quote';
import File from './file';
import Folder from './folder';
import Seal from './seal';

// ── 批次 B3 新增（二）：17 个之外**必须补**的 14 个 ──
//
// 它们不在任务表的 17 行里，但 B3 的"必须处理的"清单点了名却没有对应图形：
// 折叠箭头 ▶ ×4、更多操作 ⋯、重点 ⭐、鼠标 🖱、摊开的书 📖 ×2、图谱的
// `+` / − / ⤢ 三个控件、认证页邮箱/密码/用户名三枚内联 SVG、创建模式提示 ●、
// `Toast` 四种提示共用的那一个图形槽、建议关联卡片里的 `↔`。
// 少画任何一个，那一处就只能继续留着 Unicode 或内联 SVG —— 也就是这一批没做完。
import Chevron from './chevron';
import More from './more';
import Star from './star';
import Mouse from './mouse';
import Book from './book';
import ZoomIn from './zoom-in';
import ZoomOut from './zoom-out';
import FitScreen from './fit-screen';
import Mail from './mail';
import Lock from './lock';
import User from './user';
import Dot from './dot';
import Info from './info';
import Relation from './relation';

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
  // B3（一）
  upload: Upload,
  delete: Delete,
  edit: Edit,
  search: Search,
  add: Add,
  close: Close,
  filter: Filter,
  due: Due,
  processing: Processing,
  ai: Ai,
  success: Success,
  warning: Warning,
  error: ErrorIcon,
  quote: Quote,
  file: File,
  folder: Folder,
  seal: Seal,
  // B3（二）
  chevron: Chevron,
  more: More,
  star: Star,
  mouse: Mouse,
  book: Book,
  'zoom-in': ZoomIn,
  'zoom-out': ZoomOut,
  'fit-screen': FitScreen,
  mail: Mail,
  lock: Lock,
  user: User,
  dot: Dot,
  info: Info,
  relation: Relation,
} satisfies Record<string, ComponentType>;

/** 全部合法语义名（联合类型：`Icon` 的 `name` 用它约束） */
export type IconName = keyof typeof ICONS;
