"""
国际化（i18n）基础模块
======================

## 这个模块负责什么

只做两件事：

1. **解析 `Accept-Language`**（`parse_accept_language`）—— 纯函数，无副作用；
2. **回答"当前该用哪种语言"**（`get_locale`）—— 读 `core/context` 里的请求上下文。

错误文案的**查表**在 `core/error_messages.py`，不在这里：那张表会长到几百行，
和"解析请求头"是两件事。

## 为什么只有两种语言

`zh-CN` 与 `en`。繁体（`zh-TW` / `zh-Hant`）**刻意归到简体**而不是英文 ——
繁体用户读简体远好过读英文，而项目没有繁体文案。这条取舍在
`parse_accept_language` 里写明了，前端 `i18n/locales.ts` 的 `normalizeLocale`
用的是**同一条规则**（两处必须一致，否则会出现"界面繁体化、错误英文"的错配）。

## 为什么不用 babel / langcodes

只需识别两个语言，按项目「零新依赖优先」的口径
（`docs/archive/开发时间表.md:475`：「算法不复杂时（< 100 行），
手写实现比引入依赖更易维护」）手写即可。`babel` 会带来一个
几 MB 的依赖树，而它在这里能提供的只有"解析 `q=` 权重"这一件事。

## Celery worker 里会怎样

worker 里**没有请求上下文**（任务签名只有 `note_id`），于是
`context.get_locale()` 返回空串 → `get_locale()` 归一成默认语言。
这是**正确**的：worker 要用的语言应当来自数据库里的 `users.locale`，
由调用方显式传给 `LLMService`（见 `prompts/__init__.py`），
而不是靠一个不存在的请求头。
"""

from __future__ import annotations

from typing import Optional

from . import context

#: 支持的语言。新增一种时：本元组、`error_messages.py` 的表、
#: 前端 `i18n/locales.ts` 的 `SUPPORTED_LOCALES` 三处都要加。
SUPPORTED_LOCALES = ("zh-CN", "en")

#: 默认语言。**必须是 `zh-CN`** —— 现有用户与现有测试的观感都建立在它上面，
#: 改成 `en` 会让所有现有客户端突然收到英文错误（而它们没要求过）。
DEFAULT_LOCALE = "zh-CN"


def parse_accept_language(header: Optional[str]) -> str:
    """
    解析 `Accept-Language` 请求头，返回受支持的语言

    ## 处理的形态

    | 输入 | 结果 | 说明 |
    |---|---|---|
    | `en-US,en;q=0.9,zh-CN;q=0.8` | `en` | 按 `q` 权重排序 |
    | `zh-CN,zh;q=0.9,en;q=0.8` | `zh-CN` | |
    | `zh` / `zh-Hans` / `zh-Hant` / `zh-TW` | `zh-CN` | **繁体也归简体**（见下） |
    | `en-GB` | `en` | 只取主语言子标签 |
    | `ja,ko;q=0.9` | `zh-CN` | 都不支持 → 默认语言 |
    | `None` / `""` / 畸形值 | `zh-CN` | 缺头保持现状 |

    ## 为什么繁体归到简体而不是英文

    项目没有繁体文案。繁体用户面对"简体"与"英文"两个选择时，简体是明显更好的
    那个 —— 这是**产品判断**，不是技术判断。前端 `normalizeLocale` 用同一条规则。

    ## 为什么 `q=0` 要单独处理

    `q=0` 在 RFC 7231 里的含义是"**明确不要**这个语言"
    （`Accept-Language: en;q=0` 是"给我别的，别给英文"）。
    把它当成"权重最低但仍可接受"会让一个明确拒绝英文的客户端拿到英文 ——
    所以权重为 0 的条目**直接丢弃**。

    ## 为什么按 `q` 排序而不是取第一个

    `Accept-Language: en;q=0.5,zh-CN;q=0.9` 的语义是"中文优先"，
    而列表里 `en` 排在最前。取第一个会得到用户**第二想要**的语言。

    Args:
        header: `Accept-Language` 头的原始值，缺失时传 `None`

    Returns:
        受支持的语言标记；都不支持或头缺失时返回 `DEFAULT_LOCALE`
    """
    if not header:
        return DEFAULT_LOCALE

    # (权重, 出现次序, 语言标记) —— 次序用于权重相同时保持稳定（先出现的优先）
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        piece = part.strip()
        if not piece:
            continue
        # `en-US;q=0.9` / `en-US; q=0.9` / `en-US`
        tag, _, params = piece.partition(";")
        tag = tag.strip()
        if not tag:
            continue

        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() != "q":
                continue
            try:
                quality = float(value.strip())
            except ValueError:
                # 畸形 `q`（`q=abc`）按 RFC 的宽松姿态处理：当成 1.0 而不是丢弃 ——
                # 丢弃会让一个语法小错的客户端拿到默认语言，而它的意图很清楚
                quality = 1.0
        # `q=0` = 明确拒绝（见 docstring）
        if quality <= 0:
            continue
        candidates.append((quality, index, tag))

    # 权重降序、次序升序
    candidates.sort(key=lambda item: (-item[0], item[1]))

    for _, _, tag in candidates:
        normalized = normalize_locale(tag)
        if normalized:
            return normalized

    return DEFAULT_LOCALE


def normalize_locale(tag: Optional[str]) -> Optional[str]:
    """
    把一个语言标记归一成受支持的一种；不支持返回 `None`

    ⚠️ 与前端 `i18n/locales.ts` 的 `normalizeLocale` 是**同一条规则的两份实现**。
    两边必须一致，否则会出现"界面是中文、错误是英文"这种错配。
    改动其一时记得同步另一处（两处都有指向对方的注释）。
    """
    if not tag:
        return None
    # 只取主语言子标签：`en-US` → `en`、`zh-Hans-CN` → `zh`
    primary = tag.strip().lower().replace("_", "-").split("-")[0]
    if primary == "zh":
        return "zh-CN"
    if primary == "en":
        return "en"
    return None


def get_locale() -> str:
    """
    当前该用哪种语言

    优先取请求上下文里的值（由 `RequestContextMiddleware` 从 `Accept-Language`
    解析后写入）；没有请求上下文时（Celery worker、启动期日志、脚本）
    返回 `DEFAULT_LOCALE`。

    Returns:
        受支持的语言标记，保证**非空**
    """
    stored = context.get_locale()
    if stored in SUPPORTED_LOCALES:
        return stored
    # 存了不受支持的值（理论上不该发生，中间件已归一）—— 归一到默认语言，
    # 而不是把它透传出去（透传会让下游查表全部 miss）
    return DEFAULT_LOCALE


def is_supported(locale: str) -> bool:
    """判断一个语言标记是否受支持（供 API 校验用）"""
    return locale in SUPPORTED_LOCALES
