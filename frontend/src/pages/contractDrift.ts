/**
 * @file 契约漂移的**唯一判据**与**唯一出口**（overhaul-plan 附录 AZ.7 的落地）
 *
 * ## 为什么需要这个模块
 *
 * 两个页面（`KnowledgeGraph` / `Projects`）为了不白屏，对残缺/漂移的响应一律"宽容解析"：
 * 数组原样用、`{items:[…]}` 拆包照常渲染、真正非数组才退化成空列表。代价写在 AZ.7 里：
 * **真正的后端契约破坏从此不在任何地方报错**。这个模块补上那半边 —— 宽容照旧，但漂移可见。
 *
 * ## 为什么判据只能有一处
 *
 * 同一份数据的每一条入口都必须过同一道判据（BB.5/BB.6 的教训：护栏加在一个入口、
 * 漏了另一个，等于虚假的安心）。所以形状判定只有本文件的三个函数，页面的归一化模块
 * （`knowledgegraph/normalize.ts` / `projects/helpers.ts`）只负责按接口声明"期望什么形状"。
 *
 * ## 提示怎么做到"可见但不打扰"
 *
 * 出口是既有的全局 toast（`components/Toast`）的 `warning`：右上角、4 秒自动消失、
 * 不阻塞、不替换内容。这里**不新建任何 UI**，因为新建就等于再来一套需要维护的提示层。
 *
 * ## 去重策略（防刷屏）
 *
 * 键 = 接口 + 字段 + 形状种类，**整个应用会话只报一次**；模块级 Set 的生命周期
 * 就是一次页面会话（刷新即重置）。理由：
 * 1. 漂移是后端实现问题，第一次报出来就足够定位，重复报同一处不会带来新信息；
 * 2. 知识图谱页每次写操作后都会重拉统计，`normalizeGraphData` 更是在 `useMemo` 里
 *    每次渲染都跑一遍 —— 不去重的话，一次漂移能在几秒里刷出几十条提示，
 *    用户只会学会无视这个提示（那比没有提示更糟）；
 * 3. 不同字段 / 不同种类各报一次，因为它们是各自可修的缺陷。上限是
 *    接口数 × 字段数 × 种类，实际只会报真正漂移的那几条。
 *
 * 另外 toast 自身会把"同种类 + 同文案"的消息合并，所以同一接口的多个字段同时漂移
 * （如 `/graph` 的 `nodes` 与 `edges`）在屏幕上仍是一条。
 */
import { useEffect, useRef } from 'react';
import { useToast } from '../components/Toast';

/** 形状漂移的两种形态：本该是数组却收到包装对象 / 收到的根本不是数组 */
export type ContractDriftKind = 'wrapper' | 'non-array';

export interface ContractDriftEvent {
  /** 出问题的接口（含路径，如 `GET /graph/suggestions`）：提示里直接报给后端就能定位 */
  source: string;
  kind: ContractDriftKind;
  /** 出问题的字段名；顶层数组契约不填（问题就在响应体本身） */
  field?: string;
}

type ContractDriftListener = (event: ContractDriftEvent) => void;

/** 已上报过的去重键（模块生命周期 = 一次页面会话） */
const reported = new Set<string>();
const listeners = new Set<ContractDriftListener>();
/**
 * 订阅者出现之前发生的漂移先存这里
 *
 * 归一化发生在各 hook 的 fetch 里，而订阅发生在 effect 里 —— 两者顺序不保证
 * （`useMemo` 里的 `normalizeGraphData` 甚至可能在首次渲染期间就跑完）。
 * 丢掉这些事件等于"漂移恰好没被看见"，与不做提示没区别。
 */
const pending: ContractDriftEvent[] = [];

/** 判据的唯一出口：非预期形状在这里被记下并广播（同一处漂移只报一次） */
function reportDrift(event: ContractDriftEvent): void {
  const key = `${event.source}|${event.field ?? ''}|${event.kind}`;
  if (reported.has(key)) return;
  reported.add(key);
  if (listeners.size === 0) {
    pending.push(event);
    return;
  }
  listeners.forEach((listener) => listener(event));
}

/**
 * 顶层数组契约：接口声明返回数组（如 `/graph/suggestions`、`/projects`）。
 * 数组原样返回；`{items:[…]}` 是**漂移**但数据可恢复 —— 拆包返回并上报
 * （不能因为形状不对就丢掉数据，那会让用户看到"一个都没有"）；其余归一成空数组并上报。
 */
export function coerceArrayPayload<T>(payload: unknown, source: string): T[] {
  if (Array.isArray(payload)) return payload as T[];
  const wrapped = (payload as { items?: unknown } | null | undefined)?.items;
  if (Array.isArray(wrapped)) {
    reportDrift({ source, kind: 'wrapper' });
    return wrapped as T[];
  }
  reportDrift({ source, kind: 'non-array' });
  return [];
}

/**
 * 分页对象契约：数组**本来就**在 `items` 里（如 `/notes` 返回 `{items,total,page,page_size}`）。
 * 这里 `items` 是约定字段、不是包装对象，只有它不是数组（含字段缺失）才算漂移 ——
 * 把它当包装对象上报会把每一条正常的分页响应都报成漂移。
 */
export function unwrapPageItems<T>(payload: unknown, source: string): T[] {
  const items = (payload as { items?: unknown } | null | undefined)?.items;
  if (Array.isArray(items)) return items as T[];
  reportDrift({ source, kind: 'non-array', field: 'items' });
  return [];
}

/**
 * 对象响应里的数组字段（`nodes` / `edges` / `relation_type_distribution` …）。
 * 字段缺失或类型不对都归一到空数组并上报 —— 消费方（渲染、统计面板）只读归一后的值。
 */
export function coerceArrayField<T>(value: unknown, source: string, field: string): T[] {
  if (Array.isArray(value)) return value as T[];
  reportDrift({ source, kind: 'non-array', field });
  return [];
}

/** 订阅漂移事件；返回取消订阅函数（订阅时把此前积压的事件补投一次） */
export function subscribeContractDrift(listener: ContractDriftListener): () => void {
  listeners.add(listener);
  if (pending.length > 0) {
    pending.splice(0).forEach((event) => listener(event));
  }
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 清空"已上报"与积压事件
 *
 * 去重键是模块级状态，会跨用例存活：前一个用例喂过漂移数据，后一个用例的同一条提示
 * 就会被去重吃掉，测出来是"提示没出现"的假红。测试在 `beforeEach` 里调用它，
 * 每个用例都从"这次会话还没报过任何漂移"开始。
 */
export function resetContractDriftNotices(): void {
  reported.clear();
  pending.length = 0;
}

/** 提示文案：说清"哪个接口、哪里不对、系统怎么处理了"，用户和后端都能据此行动 */
function noticeText(event: ContractDriftEvent): { message: string; detail: string } {
  const where = event.field ? `${event.field} 字段` : '响应体';
  if (event.kind === 'wrapper') {
    return {
      message: `${event.source} 的响应结构与约定不符`,
      detail: '期望纯数组，实际是 items 包装；已拆包正常渲染，后端应改为直接返回数组',
    };
  }
  return {
    message: `${event.source} 的响应结构与约定不符`,
    detail: `${where}不是数组，已按空列表处理（页面不崩，但数据可能不完整）`,
  };
}

/**
 * 把漂移接到全局 toast 上（页面挂载的**唯一**出口）
 *
 * 只在每个页面自己的数据 hook 里调用一次即可：上报是全局的，因此同一页面上
 * 任何一条入口（重拉、子图、候选笔记…）的漂移都会走到这里。
 * 订阅只建立一次（空依赖 + ref 读最新 toast）：没有 Provider 时 `useToast()`
 * 每次渲染都返回新对象，用它当依赖会反复订阅/退订。
 */
export function useContractDriftNotice(): void {
  const toast = useToast();
  const toastRef = useRef(toast);
  useEffect(() => {
    toastRef.current = toast;
  });
  useEffect(
    () =>
      subscribeContractDrift((event) => {
        const { message, detail } = noticeText(event);
        toastRef.current.warning(message, detail);
      }),
    [],
  );
}
