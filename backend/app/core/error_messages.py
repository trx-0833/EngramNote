"""
错误文案的双语表
================

## 这张表解决什么

`AppError(code, message, status)` 的 `message` 会作为响应体的 `detail` 返回给用户。
硬编码中文时，英文界面下的错误提示是中文 —— 这就是本表要修的东西。

## 迁移是**增量**的（这是本设计的要点）

`AppError` 的签名**没有改**，`message` 仍然是必填参数。查表发生在
`middleware/error_handler.py`：

```python
detail = resolve_message(exc.code, fallback=exc.message, locale=...)
```

于是：

| 情况 | 行为 |
|---|---|
| code 在表里，且表项的唯一文案与 `fallback` 相符 | 按当前语言返回 |
| code 在表里，但 `fallback` 是**另一条**文案（同码多文案） | 按 `(code, fallback)` 找到那一条 |
| code **不在表里**，或那条文案没登记 | 用 `raise` 时写的 `message`（= 现有行为，一字不变） |

所以 90 处 `raise AppError(...)` 可以**分批迁移**：加一条表项就等于翻译了一处，
而没加的照旧。不需要一次改完，也不会出现"改了一半、另一半报 KeyError"。

## ⚠️ 表里的中文必须与 `raise` 时那句**逐字相同**

否则迁移一处就等于**悄悄改了现有用户看到的中文提示**。
加表项时的做法是：从调用点把那个字符串复制过来，而不是重新写一遍。
`tests/test_i18n.py` 用 AST 扫全 `app/` 的调用点逐字比对 —— 那条守卫
**验证过不是空转**（故意改错一个字立刻红）。

## 同一错误码有多条文案时怎么办（`_ALT` 后缀）

实测有 6 个码在不同调用点用了**不同的文案**，例如：

- `CARD_NOT_FOUND`：`卡片不存在` / `知识卡片不存在` / `知识卡片不存在或无权访问`
- `FOLDER_NOT_FOUND`：`文件夹不存在` / `文件夹不存在或无权访问`

一个码只能有一条"主文案"，所以其余的登记成 `<CODE>_ALT` / `<CODE>_ALT2`。
查表顺序是**先按 `(code, fallback)` 精确匹配、再退回主项** ——
这样 `raise` 的代码一个字都不用改，翻译也不会串。

## 哪些**没有**进表（以及为什么）

`AppError` 的 `message` 有 40 处是**表达式**而不是字面量，分两种：

1. **含运行期值的入参校验**（约 31 处），如
   `f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}"`。
   这类要进表就得给 `AppError` 加 `params` 并把 31 处 raise 改写成模板 ——
   而它们的**受众是开发者/调用方**（拼错枚举值的 API 调用者），
   正常使用中撞不到。本轮按"风险 > 收益"保留原样，在
   `UNTRANSLATED_CODES` 里登记，并有测试断言"它们确实不在表里"
   —— 这样"漏了"与"刻意没做"能被区分开。

2. **上游异常透传**（约 9 处），如 `str(e)`（LLM 失败、PDF 解析失败）。
   文案来自第三方库，翻译它等于给别人的报错编中文。

## 新增语言时

`SUPPORTED_LOCALES`（`core/i18n.py`）加一种，本表每种 code 加一个键。
`resolve_message` 在缺该语言的翻译时**回退到默认语言**（不是回退到 `fallback`）——
因为表里登记了就说明"这句该被翻译"，只是还没翻。
"""

from __future__ import annotations

from typing import Dict, Optional

from .i18n import DEFAULT_LOCALE

#: 结构：`{key: {locale: 文案}}`，`key` 是错误码或 `<错误码>_ALT[n]`
ERROR_MESSAGES: Dict[str, Dict[str, str]] = {
    # ── 笔记 ──
    "NOTE_NOT_FOUND": {"zh-CN": "笔记不存在", "en": "Note not found"},
    "NOTE_NOT_VIDEO": {"zh-CN": "该笔记不是视频类型", "en": "This note is not a video"},
    "NOTE_VIDEO_FILE_MISSING": {
        "zh-CN": "视频文件不存在",
        "en": "The video file does not exist",
    },
    "NOTE_ORIGINAL_NOT_EDITABLE": {
        "zh-CN": "原始版内容不可编辑，请切换到清洗版后编辑",
        "en": "The original version cannot be edited. Switch to the cleaned version first.",
    },
    "NOTE_CONTENT_TOO_LARGE": {
        "zh-CN": "内容过大（超过 5MB），请缩短后重试",
        "en": "Content is too large (over 5 MB). Shorten it and try again.",
    },
    "NOTE_MARKDOWN_PATH_MISSING": {
        "zh-CN": "目标 Markdown 路径为空，资料可能尚未转换完成",
        "en": "The target Markdown path is empty. The material may not be converted yet.",
    },
    "NOTE_EXPORT_EMPTY": {
        "zh-CN": "这篇笔记还没有清洗版，请先清洗或改导原始版",
        "en": "This note has no cleaned version yet. Clean it first, or export the original version.",
    },
    # `NOTE_EXPORT_EMPTY` 的文案来自一个变量（`list_detail.py:562/565` 的
    # `empty_message`），有两条取值，所以两条都要登记 —— 只登记一条的话，
    # 另一条会精确匹配失败、回退成中文。
    "NOTE_EXPORT_EMPTY_ALT": {
        "zh-CN": "这篇笔记的原始版内容读不出来",
        "en": "The original version of this note could not be read",
    },
    "NOTE_NOT_TRASHED": {"zh-CN": "该笔记不在回收站中", "en": "This note is not in the trash"},
    "NOTE_RESTORE_CONFLICT": {
        "zh-CN": "恢复失败：目标位置存在同名笔记，且自动改名已用尽",
        "en": "Restore failed: a note with the same name exists and auto-renaming was exhausted",
    },
    # ⚠️ `NOTE_VERSION_RESTORE_REJECTED` **刻意不进表**：它的 message 是
    # `str(e)`（`versions.py:215`），内容是 `笔记不存在: note_id=...` 这类
    # 带标识符的诊断串。翻译它没有意义（受众是排查问题的人），
    # 所以登记在 UNTRANSLATED_CODES 里。
    "NOTE_NOT_PERSONAL": {
        "zh-CN": "仅个人笔记可设置关联资料",
        "en": "Only personal notes can have linked materials",
    },
    "NOTE_MATERIAL_LINK_NOT_FOUND": {
        "zh-CN": "笔记-资料关联不存在或无权访问",
        "en": "The note–material link does not exist or you have no access to it",
    },

    # ── 批注 ──
    "ANNOTATION_NOT_FOUND": {"zh-CN": "批注不存在", "en": "Annotation not found"},
    "ANNOTATION_TOO_LONG": {"zh-CN": "批注内容过长", "en": "The annotation is too long"},
    "ANNOTATION_TYPE_INVALID": {
        "zh-CN": "type 必须为 highlight 或 underline",
        "en": "type must be highlight or underline",
    },
    "ANNOTATION_VIEW_MODE_INVALID": {
        "zh-CN": "view_mode 必须为 original 或 clean",
        "en": "view_mode must be original or clean",
    },

    # ── 知识图谱 ──
    "GRAPH_NODE_NOT_FOUND": {
        "zh-CN": "节点不存在或无权访问",
        "en": "The node does not exist or you have no access to it",
    },
    "GRAPH_RELATION_IDS_EMPTY": {
        "zh-CN": "关系 ID 列表不能为空",
        "en": "The relation ID list cannot be empty",
    },
    "GRAPH_RELATION_NOT_FOUND": {
        "zh-CN": "关系不存在",
        "en": "The relation does not exist",
    },
    # ⚠️ 这一条**只**对应 `delete_relation` 的返回值 —— 该函数只有这一种失败
    # （实测 `graph_service.py:792`）。确认/拒绝那两条路径的文案在
    # `GRAPH_RELATION_OPERATION_FAILED` 下，那是另一个码、且刻意未翻译
    # （它是"操作没成功"的笼统出口，文案由服务层给，见 UNTRANSLATED_CODES）。

    # ── 知识卡片 ──
    "CARD_NOT_FOUND": {"zh-CN": "卡片不存在", "en": "Card not found"},
    "CARD_NOT_FOUND_ALT": {"zh-CN": "知识卡片不存在", "en": "Knowledge card not found"},
    "CARD_NOT_FOUND_ALT2": {
        "zh-CN": "知识卡片不存在或无权访问",
        "en": "The knowledge card does not exist or you have no access to it",
    },
    "CARD_NOT_EXTENSION": {
        "zh-CN": "仅拓展卡片可使用此端点",
        "en": "Only extension cards can use this endpoint",
    },
    "CARD_REVIEW_STATE_UNAVAILABLE": {
        "zh-CN": "卡片复习状态不可用",
        "en": "Card review state is unavailable",
    },

    # ── 清洗 ──
    "CLEANING_IN_PROGRESS": {
        "zh-CN": "清洗任务正在进行中，请等待完成或先停止",
        "en": "A cleaning task is already running. Wait for it to finish or stop it first.",
    },
    "CLEANING_NOT_DONE": {
        "zh-CN": "笔记尚未完成清洗",
        "en": "This note has not been cleaned yet",
    },
    "CLEANING_CONTENT_UNREADABLE": {
        "zh-CN": "无法读取清洗副本内容",
        "en": "Could not read the cleaned copy",
    },
    "CLEANING_CONTENT_UNREADABLE_ALT": {
        "zh-CN": "无法读取 Markdown 内容",
        "en": "Could not read the Markdown content",
    },

    # ── 理解 / 出题 / 问答 ──
    "UNDERSTANDING_IN_PROGRESS": {
        "zh-CN": "理解任务正在进行中，请等待完成",
        "en": "An understanding task is already running. Wait for it to finish.",
    },
    "UNDERSTANDING_NO_CARDS": {
        "zh-CN": "该笔记暂无知识卡片，请先触发理解管道",
        "en": "This note has no knowledge cards yet. Run the understanding pipeline first.",
    },
    "EMPTY_QUESTION": {"zh-CN": "问题不能为空", "en": "The question cannot be empty"},

    # ── 上传 ──
    "UPLOAD_FILE_NAME_EMPTY": {
        "zh-CN": "文件名不能为空",
        "en": "The file name cannot be empty",
    },
    "UPLOAD_FILE_NAME_INVALID": {"zh-CN": "文件名不合法", "en": "Invalid file name"},
    "UPLOAD_FILE_NAME_INVALID_ALT": {
        "zh-CN": "文件名不能包含路径分隔符",
        "en": "The file name cannot contain path separators",
    },
    "UPLOAD_FILE_NAME_TOO_LONG": {
        "zh-CN": "文件名过长（最多 255 字符）",
        "en": "The file name is too long (255 characters max)",
    },
    "UPLOAD_FORMAT_UNSUPPORTED": {
        "zh-CN": "临时文件格式不受支持",
        "en": "The temporary file format is not supported",
    },
    "UPLOAD_FORMAT_UNSUPPORTED_ALT": {
        "zh-CN": "不支持的文件格式",
        "en": "Unsupported file format",
    },
    "UPLOAD_PDF_PARSE_FAILED": {
        "zh-CN": "PDF 解析失败：文件可能已损坏或加密，请确认后重试",
        "en": "PDF parsing failed: the file may be corrupted or encrypted. Check it and try again.",
    },
    "UPLOAD_PDF_CROP_FAILED": {
        "zh-CN": "PDF 裁剪失败：页码范围可能无效，或文件已损坏",
        "en": "PDF cropping failed: the page range may be invalid, or the file is corrupted.",
    },
    "UPLOAD_STORAGE_WRITE_FAILED": {
        "zh-CN": "文件上传失败，请稍后重试",
        "en": "Upload failed. Please try again later.",
    },
    "UPLOAD_TEMP_ID_INVALID": {
        "zh-CN": "无效的临时上传标识",
        "en": "Invalid temporary upload ID",
    },
    "UPLOAD_TEMP_EXPIRED": {
        "zh-CN": "临时上传已失效，请重新选择文件",
        "en": "The temporary upload has expired. Please select the file again.",
    },
    "UPLOAD_TEMP_DATA_INVALID": {
        "zh-CN": "临时上传数据异常，请重新选择文件",
        "en": "The temporary upload data is invalid. Please select the file again.",
    },

    # ── 文件夹 ──
    "FOLDER_NOT_FOUND": {"zh-CN": "文件夹不存在", "en": "Folder not found"},
    "FOLDER_NOT_FOUND_ALT": {
        "zh-CN": "文件夹不存在或无权访问",
        "en": "The folder does not exist or you have no access to it",
    },
    "FOLDER_DATE_INVALID": {
        "zh-CN": "无效的日期格式，请使用 ISO 格式如 2024-01-15",
        "en": "Invalid date format. Use an ISO date such as 2024-01-15.",
    },

    # ── 项目 ──
    "PROJECT_NOT_FOUND": {"zh-CN": "项目不存在", "en": "Project not found"},

    # ── 学习目标 ──
    #
    # ⚠️ 这两个码的中文**本来就是英文**（实测：`raise AppError(GOAL_NOT_FOUND,
    # "Goal not found", 404)`）。这不是本轮引入的 —— 迁表时**保持现状**，
    # 因为改中文界面的文案属于产品决策，不属于 i18n 基础设施。
    # 于是两种语言下值相同，这是**如实记录既有行为**，不是漏翻。
    "GOAL_NOT_FOUND": {"zh-CN": "Goal not found", "en": "Goal not found"},
    "GOAL_NONE_ACTIVE": {
        "zh-CN": "No active goals, please create a goal first",
        "en": "No active goals. Create a goal first.",
    },
    "GOAL_SCOPE_NOTE_INVALID": {
        "zh-CN": "目标范围包含不存在或无权访问的笔记",
        "en": "The goal scope includes a note that does not exist or is not accessible",
    },
    "GOAL_SCOPE_FOLDER_INVALID": {
        "zh-CN": "目标范围包含不存在或无权访问的文件夹",
        "en": "The goal scope includes a folder that does not exist or is not accessible",
    },

    # ── 版本 ──
    #
    # 两条文案都带运行期值（note_id / version_number），但它们的**可变部分
    # 只是标识符**，句子骨架是固定的 —— 所以拆成模板 + 参数是可行的
    # （`version_service.py:210/228`）。表里存 `{...}` 占位符，
    # 由 `resolve_message` 之外的调用方负责填充；这里先给出双语骨架。
    #
    # ⚠️ 现在**还不会生效**：`version_service` 传的是已格式化好的 f-string，
    # 与本表的 `zh-CN` 不逐字相同，于是精确匹配失败、回退到原中文。
    # 要让它们生效，需要把 f-string 改成传 `params`（属"给 AppError 加
    # params"那一批，见 UNTRANSLATED_CODES 的说明）。
    # 登记在这里是为了让翻译**先就位**，也让"哪些码已决定怎么翻"可查。
    "VERSION_NOT_FOUND": {
        "zh-CN": "版本不存在",
        "en": "Version not found",
    },

    # ── 复习 ──
    "REVIEW_QUIZ_NOT_FOUND": {
        "zh-CN": "题目不存在或不属于该笔记",
        "en": "The question does not exist or does not belong to this note",
    },
    "REVIEW_REMINDERS_FAILED": {
        "zh-CN": "获取复习提醒数据失败",
        "en": "Failed to load review reminders",
    },

    # ── 任务 ──
    "TASK_NOT_FOUND": {"zh-CN": "任务不存在", "en": "Task not found"},

    # ── 认证 ──
    "AUTH_INVALID_CREDENTIALS": {
        "zh-CN": "邮箱或密码错误",
        "en": "Incorrect email or password",
    },
}

#: `HTTPException` 的文案（它们没有业务错误码，只有状态码）
#:
#: ⚠️ 这张表**不能**按状态码给通用文案：401 在这一处是"无效或过期的令牌"，
#: 在另一处可能是别的意思。所以键是**原文案本身**（中文），
#: 值是对应语言的翻译 —— 这样"同一个中文句子在不同端点出现"会自动共用翻译，
#: 而"同一状态码下不同句子"不会互相覆盖。
HTTP_STATUS_MESSAGES: Dict[str, Dict[str, str]] = {
    "无效或过期的令牌": {
        "zh-CN": "无效或过期的令牌",
        "en": "Invalid or expired token",
    },
    "用户不存在或已禁用": {
        "zh-CN": "用户不存在或已禁用",
        "en": "User does not exist or has been disabled",
    },
    "未提供认证令牌": {
        "zh-CN": "未提供认证令牌",
        "en": "Authentication token is required",
    },
    "刷新令牌无效或已失效，请重新登录": {
        "zh-CN": "刷新令牌无效或已失效，请重新登录",
        "en": "Refresh token is invalid or expired. Please sign in again.",
    },
}

#: 中间件里硬编码的那句 500 文案（不在任何 `AppError` 里，所以单独登记）
INTERNAL_ERROR_MESSAGES: Dict[str, str] = {
    "zh-CN": "服务器内部错误，请稍后重试",
    "en": "Internal server error. Please try again later.",
}

#: 参数校验失败（`ValidationError` → 422）的兜底文案。
#: ⚠️ 现有行为是把 Pydantic 的英文报错**原样**放进 detail（`str(exc)`），
#: 那是**英文**的 —— 所以本表只在"拿不到 str(exc)"时兜底，不参与正常路径。
VALIDATION_ERROR_MESSAGES: Dict[str, str] = {
    "zh-CN": "请求参数校验失败",
    "en": "Request validation failed",
}

#: 刻意**没有**进表的错误码 —— 受众是开发者/调用方的入参校验，或上游异常透传
#:
#: 这份名单的作用是让"漏了"与"刻意没做"能被区分开：
#: `tests/test_i18n.py` 断言这些码确实不在 `ERROR_MESSAGES` 里。
#: 哪天给 `AppError` 加了 `params` 并把某一条迁进表，就从这里删掉它。
UNTRANSLATED_CODES: frozenset = frozenset(
    {
        # 含运行期值的入参校验（要进表得先给 AppError 加 params）
        "NOTE_ROLE_INVALID",
        "NOTE_STATUS_INVALID",
        "CARD_CATEGORY_INVALID",
        "LLM_USAGE_GROUP_BY_INVALID",
        "PROMPT_VERSION_INVALID",
        "UPLOAD_FILE_TOO_LARGE",
        "UPLOAD_STORAGE_QUOTA_EXCEEDED",
        "UPLOAD_NOTE_COUNT_LIMIT_REACHED",
        "UPLOAD_CONTENT_MISMATCH",
        "UPLOAD_SCRIPT_CONTENT_REJECTED",
        "UPLOAD_ARCHIVE_REJECTED",
        "UPLOAD_PDF_TOO_MANY_PAGES",
        "UPLOAD_EXTENSION_MISMATCH",
        "UPLOAD_CROP_UNSUPPORTED_TYPE",
        "CLEANING_MARKER_NOT_FOUND",
        "GOAL_ACTIVE_LIMIT_REACHED",
        "MATERIAL_NOT_FOUND",
        "MATERIAL_ROLE_INVALID",
        "FOLDER_NOT_EMPTY",
        "PROJECT_NOTE_LINK_NOT_FOUND",
        "DAILY_REVIEW_LIMIT_REACHED",
        "GRAPH_RELATION_OPERATION_FAILED",
        "GRAPH_SUGGESTION_FAILED",
        "COMBINED_EXTRACT_FAILED",
        "EXTENSION_GENERATE_FAILED",
        "ASSESSMENT_COMPARE_FAILED",
        "ASSESSMENT_QUIZ_GENERATE_FAILED",
        "ASSESSMENT_ANSWER_REJECTED",
        "ASSESSMENT_ANSWER_FAILED",
        "AUTH_REGISTRATION_REJECTED",
        "NOTE_VERSION_RESTORE_REJECTED",
        "VERSION_CONTENT_UNAVAILABLE",
    }
)

#: `(code, fallback) -> key` 的索引，让"同码多文案"能精确命中
_ALT_INDEX: Dict[tuple, str] = {}


def _base_code(key: str) -> str:
    """`CARD_NOT_FOUND_ALT2` → `CARD_NOT_FOUND`"""
    for suffix in ("_ALT2", "_ALT"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _build_alt_index() -> None:
    """把 `<CODE>_ALT[n]` 的表项按其中文文案反查回 `(code, 中文)`

    为什么按**中文**反查而不是按调用点行号：中文就是 `raise` 时传进来的
    `fallback`，这是运行期唯一能拿到的判据。代价是"两条不同文案恰好相同"
    时只命中一条 —— 那时两条的翻译也应当相同，所以无害。
    """
    for key, entry in ERROR_MESSAGES.items():
        base = _base_code(key)
        if base == key:
            continue
        zh = entry.get(DEFAULT_LOCALE)
        if zh:
            _ALT_INDEX[(base, zh)] = key


_build_alt_index()


def resolve_message(code: str, fallback: str, locale: str = DEFAULT_LOCALE) -> str:
    """
    按错误码 + 语言取文案；表里没有就用 `fallback`

    查表顺序（"同码多文案"就靠这个顺序区分）：
    1. `(code, fallback)` 精确命中 `_ALT` 项 → 用那一条；
    2. `code` 的主项存在 → 用主项；
    3. 都没有 → `fallback`（= 现有行为）。

    Args:
        code: 业务错误码（`AppError.code`）
        fallback: `raise` 时写的那句 `message`（= 现有行为）
        locale: 目标语言

    Returns:
        该语言的文案；该 code 未登记、或登记了但没有这个语言的翻译时，
        返回 `fallback`（**不是空串** —— 空串会让用户看到一个没有内容的错误框）
    """
    key = _ALT_INDEX.get((code, fallback)) or code
    entry = ERROR_MESSAGES.get(key)
    if not entry:
        return fallback
    return entry.get(locale) or entry.get(DEFAULT_LOCALE) or fallback


def resolve_http_status_message(detail: str, locale: str = DEFAULT_LOCALE) -> str:
    """
    按**中文原文**取 `HTTPException` 的翻译

    为什么键是原文而不是状态码：见 `HTTP_STATUS_MESSAGES` 的说明。
    没有登记时返回原 `detail`（现有行为）。
    """
    entry = HTTP_STATUS_MESSAGES.get(detail)
    if not entry:
        return detail
    return entry.get(locale) or entry.get(DEFAULT_LOCALE) or detail


def internal_error_message(locale: str = DEFAULT_LOCALE) -> str:
    """500 兜底文案"""
    return INTERNAL_ERROR_MESSAGES.get(locale) or INTERNAL_ERROR_MESSAGES[DEFAULT_LOCALE]


def validation_error_message(locale: str = DEFAULT_LOCALE) -> str:
    """422 兜底文案（正常路径用 Pydantic 自己的英文报错，不走这里）"""
    return VALIDATION_ERROR_MESSAGES.get(locale) or VALIDATION_ERROR_MESSAGES[DEFAULT_LOCALE]


def registered_codes() -> list:
    """已登记的主错误码清单（不含 `_ALT`，供测试断言"表与调用点一致"）"""
    out = []
    for key in ERROR_MESSAGES:
        base = _base_code(key)
        if base not in out:
            out.append(base)
    return sorted(out)


def _lookup_for_test(code: str, locale: str) -> Optional[str]:
    """仅供测试：取某 code 某语言的登记值（未登记返回 None）"""
    return (ERROR_MESSAGES.get(code) or {}).get(locale)
