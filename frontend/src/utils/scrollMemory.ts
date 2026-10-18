/**
 * @file 阅读位置记忆：把「上次读到哪」持久化到 localStorage
 * @description 「使用时的想法.txt」第 1 条的前端一半
 *
 * ## 要解决的具体场景（用户原话）
 *
 * > 笔记页面刷新很频繁，有的时候切至别的窗口干活之后，再回来一刷新
 * > 就不知道看到哪里了，因为刷新之后页面恢复了初始状态，还要往下滑。
 *
 * 关键在**刷新**两个字：切窗口再回来浏览器本来就保留滚动位置，
 * 但 F5 会把 DOM 和 `window.scrollY` 一起重置。
 *
 * ## 为什么不能用 `DailyMaterials.tsx` 那一套（模块作用域变量）
 *
 * `pages/DailyMaterials.tsx:106-122` 已经把「离开时记、后退回来恢复」做过了，
 * 但它在文件头明确写了「整页刷新（F5）后它自然清零」—— 那是有意的取舍
 * （它解决的是「点进详情再退回来」）。本文件要解决的恰好是它放弃的那一半，
 * 所以必须落到 `localStorage`。
 *
 * ## 为什么是 localStorage 而不是 sessionStorage
 *
 * 用户的选择（2026-09-26）：**跨刷新 + 跨会话**。sessionStorage 只在当前
 * 标签页生命周期内有效，关掉标签页就没了 —— 而「切到别的窗口干活一整天、
 * 回来重开」正是要覆盖的场景。
 *
 * ## 为什么只存数字，不存任何笔记内容
 *
 * 存的是 `{ y, mode, at }`：滚动像素、视图模式、时间戳。
 * **没有标题、没有正文片段、没有锚点文本** —— 这一条让本功能不触碰
 * `backend/tests/test_no_personal_data.py` 的口径（那个守卫扫的是真实用户
 * 内容进仓库/进日志），也让 localStorage 里不留可读的学习内容。
 *
 * ## 降级：任何异常都不许冒泡
 *
 * `localStorage` 的每一次访问都可能抛 —— Safari 隐私模式、配额写满
 * （`QuotaExceededError`）、企业策略禁用存储。这些都**不是**本功能的
 * 失败场景，而是「这次不记忆」而已：用户顶多回到刷新前的观感（页面在顶部），
 * 那是**功能没生效**，不是**功能坏了**。所以下面每个出口都包了 `try/catch`，
 * 并且**不弹提示、不写 console** —— 一个记不住滚动位置的功能没有资格打扰用户。
 */

/** localStorage 键前缀。带版本号：将来改存储形状时，旧键自然失效而不是被误读 */
const KEY_PREFIX = 'engramnote:scroll:v1:';

/**
 * 记忆的有效期：30 天。
 *
 * 为什么需要它：没有 TTL 的话，一条三个月前的记录会在某天突然把用户
 * 丢到一个早已忘记的位置上 —— 那比「回到顶部」更让人困惑。
 * 30 天足够覆盖「这本书我还在读」，又短到不会跨过一整轮学习周期。
 */
const TTL_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * 最多保留多少条笔记的记忆。
 *
 * 为什么要上限：localStorage 通常只有 5MB，而键是**按笔记**分的 ——
 * 一个重度用户读过几千篇笔记时，键会无界增长。每条记录不到 100 字节，
 * 200 条约 20KB，对配额毫无压力，同时覆盖「最近在读的笔记」这个真实集合。
 */
const MAX_ENTRIES = 200;

/** 一条记忆的内容 */
export interface ScrollMemory {
  /** 离开时的窗口滚动位置（px） */
  y: number;
  /** 当时显示的视图模式（`original` / `clean`）。恢复时要求一致，见 `useScrollMemory` */
  mode: string;
  /** 写入时间戳（ms）。用于 TTL 判定与超限淘汰 */
  at: number;
}

/** 本模块的存储入口。抽成函数是为了让测试能替换它，也为了让 `try/catch` 只有一处 */
function storage(): Storage | null {
  try {
    // 隐私模式下 `localStorage` 这个 getter 本身就可能抛
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 拼接某个笔记的键 */
function keyFor(noteId: string): string {
  return `${KEY_PREFIX}${noteId}`;
}

/**
 * 判断一个解析出来的值是不是合法记忆。
 *
 * 为什么逐字段查而不是 `as ScrollMemory`：localStorage 是**用户可编辑**的，
 * 也是**跨版本残留**的。一个 `y: "abc"` 会让 `window.scrollTo({top: NaN})`
 * 静默不滚（或滚到 0），而失败形态会指向 `scrollTo` 那一行 ——
 * 那是完全正常的代码。宁可在这里判死。
 */
function isMemory(value: unknown): value is ScrollMemory {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.y === 'number' &&
    Number.isFinite(candidate.y) &&
    candidate.y >= 0 &&
    typeof candidate.mode === 'string' &&
    typeof candidate.at === 'number' &&
    Number.isFinite(candidate.at)
  );
}

/**
 * 读出某条笔记的记忆。
 *
 * @returns 记忆；不存在 / 已过期 / 形状不对 / 存储不可用时一律 `null`
 */
export function readScrollMemory(noteId: string, now: number = Date.now()): ScrollMemory | null {
  const store = storage();
  if (!store) return null;

  try {
    const raw = store.getItem(keyFor(noteId));
    if (!raw) return null;

    const parsed: unknown = JSON.parse(raw);
    if (!isMemory(parsed)) {
      // 形状不对就顺手清掉，免得每次读都要再判一次
      store.removeItem(keyFor(noteId));
      return null;
    }
    if (now - parsed.at > TTL_MS) {
      store.removeItem(keyFor(noteId));
      return null;
    }
    return parsed;
  } catch {
    // ⚠️ 走到这里的有两种：`JSON.parse` 抛（值不是 JSON），或上面任一处存储访问抛。
    // 前者必须**顺手清掉** —— 脏值不清理的话，每次挂载都要再解析一次同一个坏值，
    // 而且它永远占着一个键位（容量上限那条路径会把别人挤掉）。
    // 清理本身也可能抛（存储不可用），所以它自己也要包一层。
    try {
      store.removeItem(keyFor(noteId));
    } catch {
      // 存储不可用：这次读不到就是全部后果
    }
    return null;
  }
}

/**
 * 写入某条笔记的记忆，并做一次超限淘汰。
 *
 * @param noteId 笔记 ID
 * @param y 滚动位置（px）。负数与 `NaN` 会被拒绝 —— 它们只可能来自测量错误
 * @param mode 当时的视图模式
 * @param now 当前时间戳（测试用）
 */
export function writeScrollMemory(
  noteId: string,
  y: number,
  mode: string,
  now: number = Date.now(),
): void {
  // 拒绝非法输入：写进去只会让下次恢复变成一个静默的 no-op
  if (!noteId || !Number.isFinite(y) || y < 0) return;

  const store = storage();
  if (!store) return;

  try {
    const record: ScrollMemory = { y, mode, at: now };
    const key = keyFor(noteId);
    store.setItem(key, JSON.stringify(record));
    evictOverflow(store, key);
  } catch {
    // 配额写满 / 被策略拒绝 —— 这次不记，不打扰用户
  }
}

/** 删除某条笔记的记忆（恢复成功后清掉待恢复值、或用户主动清空时用） */
export function clearScrollMemory(noteId: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(keyFor(noteId));
  } catch {
    // 同上：删不掉也不影响任何正确性
  }
}

/**
 * 超出 `MAX_ENTRIES` 时淘汰最旧的几条。
 *
 * 为什么在**写入之后**才淘汰（而不是写入前先数）：淘汰要遍历所有键，
 * 而遍历是这里唯一有 O(n) 成本的动作。放在写之后，只有真正需要时才付这个成本。
 *
 * 为什么淘汰依据是 `at` 而不是键名顺序：键名是随机 UUID，没有任何时间语义。
 * 顺带把过期的条目一起收掉 —— 否则一个不常来的用户永远触发不了淘汰，
 * 过期条目会一直躺在那里（`readScrollMemory` 只在**读到**时才清理它自己那一条）。
 *
 * @param protectedKey 刚刚写入的那一条。它**必须**活下来 ——
 *   虽然它的 `at` 是最新的、按排序天然排在末尾，但把这条不变量写进签名
 *   比依赖「排序恰好如此」可靠：将来谁改了排序方向，这里会当场删错东西。
 */
function evictOverflow(store: Storage, protectedKey: string): void {
  const keys: string[] = [];
  for (let i = 0; i < store.length; i += 1) {
    const key = store.key(i);
    if (key && key.startsWith(KEY_PREFIX)) keys.push(key);
  }
  if (keys.length <= MAX_ENTRIES) return;

  const entries: { key: string; at: number }[] = [];
  for (const key of keys) {
    try {
      const parsed: unknown = JSON.parse(store.getItem(key) ?? '');
      // 形状不对的一并当成过期处理（它们永远不会被读出来）
      entries.push({ key, at: isMemory(parsed) ? parsed.at : 0 });
    } catch {
      entries.push({ key, at: 0 });
    }
  }

  // 最旧的排前面（`at: 0` 的形状不对条目排最前，优先被收掉）
  entries.sort((a, b) => a.at - b.at);
  const overflow = entries.length - MAX_ENTRIES;
  for (let i = 0; i < overflow; i += 1) {
    const entry = entries[i];
    if (!entry || entry.key === protectedKey) continue;
    try {
      store.removeItem(entry.key);
    } catch {
      // 单条删不掉不影响整体
    }
  }
}

/** 本模块导出的常量，供测试与调用方引用（避免测试里重写魔数） */
export const SCROLL_MEMORY_LIMITS = { TTL_MS, MAX_ENTRIES, KEY_PREFIX } as const;
