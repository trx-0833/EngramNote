"""笔记导出的纯函数层（想法 4「设计导出功能」）

## 这个模块为什么单独存在

导出要做的三件事里，只有一件与数据库/存储有关（读出正文），另外两件是
**纯字符串加工**：把标题变成合法文件名、把元信息拼成 front-matter。
把纯函数放在这里、端点只做"取数据 + 调用它们"，是为了让**边界条件可以离线测**：
文件名安全化与 YAML 引号规则全是边界条件的活（Windows 非法字符、结尾的点、
超长标题、标题里带冒号/引号/换行），而那些条件在端点级测试里很难穷举。

因此本模块**不 import 数据库、不 import 存储** —— 只有 `re` 与 `datetime`。

## 为什么不用 pyyaml 生成 front-matter

`pyyaml` **不在主依赖里**：`backend/requirements.txt:30` 注明它只属 MinerU 本地
pipeline（`requirements-pdf-local.txt`），而 `app/services/mineru/config_loader.py`
是它唯一的用户。为一个 6 行的元信息块把一个可选依赖拉进主环境，不符合本项目
"零非必要依赖"的口径（见 `AGENTS.md` 与 `visual-design-spec.md` 的同类判断）。

代价是必须自己保证 YAML 正确性。**只有 `title` 需要处理** —— 它是唯一的
用户可控字段，其余全是枚举 / 整数 / ISO 时间戳（安全标量）。
所以下面只实现"最小引号规则"：**标题永远用双引号包起来并转义**，
不去实现一个通用的 YAML 序列化器。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 文件名安全化
# ---------------------------------------------------------------------------

#: Windows 上**绝对**不能出现在文件名里的字符（`\ / : * ? " < > |`）。
#: 这些在 Linux/macOS 上大多合法，但导出文件是给人拿到别处用的 ——
#: 按最严的那个平台处理，否则"在我的 Mac 上好好的"到了 Windows 上就存不下来。
#:
#: ⚠️ **全角变体不在这个名单里**：`：` `？` `＊` `＂` `＜` `＞` `｜` 在
#: Windows 上是合法文件名字符，而中文标题里它们很常见（本项目 24 篇笔记的
#: 标题全是中文）。把它们一并替换掉会平白改动用户看得见的文件名。
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

#: 控制字符（含 `\n` `\t` `\r` 与 C0/C1 区）。
#: 它们不该出现在文件名里，也不该出现在 front-matter 的裸标量里。
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

#: 连续空白（含全角空格 U+3000）压成一个半角空格
_WHITESPACE_RUN = re.compile(r"[\s\u3000]+")

#: 文件名的最大长度（按**字符**计，不是字节）。
#:
#: 取值理由：Windows 的路径上限是 260 字符，而下载目录本身要占掉几十个字符；
#: 100 个字符的中文标题已经是极端情况（本项目最长的一条约 30 字），
#: 留到 80 既能装下真实标题，又给路径留足余量。
MAX_FILENAME_CHARS = 80

#: 标题被清空（或本来就为空）时的回退名。**不带 `.md`** —— 后缀由调用方拼。
_FALLBACK_STEM = "note"


def sanitize_filename(title: str, fallback_id: str = "") -> str:
    """把笔记标题变成安全的文件名主干（**不含扩展名**）

    处理顺序是刻意的，每一步都为下一步服务：

    1. **控制字符 → 空格**（不是删除）：`"a\\nb"` 删掉换行会变成 `"ab"`，
       而用户的本意是两个词。换成空格再由第 4 步压成单个空格。
    2. **Windows 非法字符 → `_`**：用下划线而不是删除，保留"这里原来有个字符"
       的信息（`"a/b"` → `"a_b"` 而不是 `"ab"`）。
    3. **压空白**：连续的空白与全角空格统一成一个半角空格。
    4. **去掉首尾空白**。
    5. **去掉结尾的点**：`"标题."` → `"标题"`。这是 Windows 的硬限制 ——
       结尾带点的文件**创建不出来**（资源管理器会自动吞掉它，而 API 调用会失败）。
    6. **截断到 `MAX_FILENAME_CHARS`**（截断后再去一次尾部空白与点 ——
       截断点可能正好落在一个空格上）。
    7. **空结果回退**：全是不合法字符的标题会变成空串，此时用
       `note-<id 前 8 位>`；连 id 都没有就用 `note`。
       回退名里带上 id 前缀是为了让"两篇标题都空的笔记"不会互相覆盖。

    Args:
        title: 笔记标题（用户可控，可能含任意字符）
        fallback_id: 笔记 ID，用于标题被清空时的回退名

    Returns:
        安全的文件名主干，**保证非空**、不含非法字符、不以点或空格结尾

    Examples:
        >>> sanitize_filename("拉哇水电站运行规程学习")
        '拉哇水电站运行规程学习'
        >>> sanitize_filename('a/b:c*d?')
        'a_b_c_d_'
        >>> sanitize_filename("   ")
        'note'
    """
    name = _CONTROL_CHARS.sub(" ", title or "")
    name = _ILLEGAL_FILENAME_CHARS.sub("_", name)
    name = _WHITESPACE_RUN.sub(" ", name)
    name = name.strip()
    # 结尾的点：Windows 上"标题."建不出文件。先处理它再截断，
    # 这样"标题.   "（点+空格）这类组合也能被一次收掉。
    name = name.rstrip(". ")
    if len(name) > MAX_FILENAME_CHARS:
        name = name[:MAX_FILENAME_CHARS].rstrip(". ")
    if not name:
        # 回退名：带 id 前缀，避免多篇空标题笔记导出后互相覆盖
        short_id = (fallback_id or "").replace("-", "")[:8]
        return f"{_FALLBACK_STEM}-{short_id}" if short_id else _FALLBACK_STEM
    return name


def build_content_disposition(filename_stem: str) -> str:
    """构造 `Content-Disposition` 头的值（附件下载）

    ## 为什么要写两种 `filename`

    HTTP 头的值是 **latin-1** 的（RFC 7230），而中文标题**不是** ——
    直接写 `filename="拉哇水电站…"` 会让 Starlette 在编码响应头时抛
    `UnicodeEncodeError`，或者在更宽松的实现里变成乱码。

    所以按 RFC 6266 / RFC 5987 写**两份**：

    - `filename="…"` —— 纯 ASCII 回退，给不认 `filename*` 的老客户端。
      非 ASCII 字符在这里被替换成 `_`（**不是**删除，否则
      `"拉哇水电站"` 会变成一个空串，回退名反而不可用）。
    - `filename*=UTF-8''<percent-encoded>` —— 现代浏览器优先用它，
      中文标题原样还原。

    ## 为什么用 `urllib.parse.quote` 而不是手写百分号编码

    RFC 5987 的 `ext-value` 有明确的 attr-char 集合，`quote` 的
    `safe` 参数正好可以表达它（`''` + 字母数字 + 少量符号）。
    手写编码表是那种"看起来对、遇到某个字符才错"的活。

    Args:
        filename_stem: 文件名主干（**不含** `.md`，由本函数补）

    Returns:
        可直接塞进 `headers={"Content-Disposition": …}` 的字符串
    """
    from urllib.parse import quote

    filename = f"{filename_stem}.md"
    # ASCII 回退：非 ASCII 一律换成 `_`，并顺带处理掉引号与反斜杠
    # （它们是 filename 参数里的转义字符，留着会破坏头的结构）
    ascii_fallback = "".join(
        ch if (ch.isascii() and ch not in '"\\') else "_" for ch in filename
    )
    # RFC 5987 的 attr-char：这里取 `quote` 默认的安全集（字母数字 + `_.-~`），
    # 其余全部百分号编码。空格会被编码成 %20（对 `filename*` 是正确的）。
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


# ---------------------------------------------------------------------------
# front-matter
# ---------------------------------------------------------------------------


def _quote_yaml_string(value: str) -> str:
    """把一个字符串渲染成**永远安全**的 YAML 双引号标量

    ## 为什么一律加引号，而不是"需要时才加"

    "只在必要时加引号"需要判断几十种情况（前导 `-` / `?` / `:`、`#` 出现的位置、
    看起来像数字的字符串、`yes`/`no`/`null` 这些 YAML 1.1 的布尔词……），
    判断漏一种就产出一个**语法错误或语义漂移**的文件 —— 而 front-matter
    解析失败会让整个文件在 Obsidian 里显示成"元信息坏掉"。

    一律加引号则只有一条规则：**转义 `\\` 与 `"`，其余原样**。
    多两个引号字符的成本可以忽略，换来的是"不可能出错"。

    ## 为什么换行要被转义成 `\\n` 而不是保留

    YAML 的双引号标量**支持**真实换行，但那样 front-matter 会跨行，
    而"front-matter 到第一个 `---` 结束"这个约定在跨行标量下仍然成立、
    却极难人眼核对。转义成 `\\n` 后整块 front-matter 是**一行一个字段**，
    任何编辑器里都一眼能读。
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    escaped = _CONTROL_CHARS.sub("", escaped)
    return f'"{escaped}"'


def build_front_matter(
    *,
    title: str,
    source_type: str,
    status: str,
    variant: str,
    page_count: Optional[int],
    created_at: Optional[datetime],
    updated_at: Optional[datetime],
    exported_at: datetime,
) -> str:
    """拼出 front-matter 块（**含首尾的 `---` 行与结尾换行**）

    ## 字段取舍

    只写"用户拿到文件后还用得上"的字段：

    | 字段 | 为什么写 |
    |---|---|
    | `title` | 文件名会被系统改（重名加 `(1)`、手动改名），标题本身不该跟着丢 |
    | `source_type` | 知道这是 PDF 转来的还是自己写的笔记 |
    | `status` | 处理到哪一步了（cleaned / archived / …） |
    | `variant` | **这份文件是原始版还是清洗版** —— 导出时二选一，事后必须能分辨 |
    | `page_count` | 资料型笔记的规模 |
    | `created_at` / `updated_at` | 时间线 |
    | `exported_at` | "这份文件是什么时候导的" —— 有了它才能判断内容是否已过期 |

    **刻意不写** `user_id` / `folder_id` / 对象存储路径：内部标识对用户无用，
    而路径会泄露存储布局。

    ## `page_count` 为空时整行省略，不写 `null`

    对使用 front-matter 的人（Obsidian 的 Dataview、Logseq 的属性面板）来说，
    "这个字段没有值"与"这个字段等于 null"是两种意思：前者是"这篇笔记没有页数
    这个概念"（音频、Markdown），后者容易被当成"页数未知、需要补"。
    省略更诚实。

    Args:
        各字段见上；时间戳为 `None` 时整行省略

    Returns:
        以 `---\\n` 开头、以 `---\\n` 结尾的完整块（调用方直接与正文拼接）
    """
    lines = ["---"]
    lines.append(f"title: {_quote_yaml_string(title)}")
    lines.append(f"source_type: {source_type}")
    lines.append(f"status: {status}")
    lines.append(f"variant: {variant}")
    if page_count is not None:
        lines.append(f"page_count: {page_count}")
    if created_at is not None:
        lines.append(f"created_at: {_quote_yaml_string(created_at.isoformat())}")
    if updated_at is not None:
        lines.append(f"updated_at: {_quote_yaml_string(updated_at.isoformat())}")
    lines.append(f"exported_at: {_quote_yaml_string(exported_at.isoformat())}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def build_export_text(front_matter: str, content: str) -> str:
    """把 front-matter 与正文拼成最终文件内容

    规则只有一条：**两者之间恰好一个空行**。

    - front-matter 本身以 `---\\n` 结尾（见 `build_front_matter`）；
    - 正文**逐字不改** —— 不规范化行尾、不补标题、不 trim。
      理由：导出是"把应用里的东西拿出来"，任何加工都会让用户怀疑
      "我看到的和导出的到底是不是同一份"。front-matter 是**唯一**新增的内容，
      而且它是显式声明的元信息块，不是对正文的改写。
    """
    return f"{front_matter}\n{content}"


# ===========================================================================
# 批量导出（B4）：ZIP 的目录页与文件名
# ===========================================================================

def build_zip_index(entries: List[Dict[str, str]], *, variant: str) -> str:
    """生成批量导出的 `index.md`（目录页）

    ## 为什么要有它

    一个 ZIP 里摊着几十个 `.md` 时，用户解压后**不知道从哪看起**，
    也不知道每篇是什么来源、哪一版。目录页把这几件事一次说清：
    文件名 ←→ 标题、来源类型、笔记状态。

    ## 链接必须相对、且要 URL 编码

    目录页是给 Obsidian / Typora 这类 Markdown 阅读器点的，
    所以链接用 `./文件名.md`。文件名里可能有中文、空格、括号 ——
    这些字符在 Markdown 链接里必须**百分号编码**，否则链接会断在第一个空格处。
    用 `urllib.parse.quote` 而不是"自己替换几个字符"：
    前者是唯一能覆盖全部保留字符的做法。

    ## 为什么不写"导出时间"以外的统计

    统计数字（多少篇、多少字）要看就会过期，而这份目录是**静态文件**。
    只写导出时间与总数 —— 它们描述"这一份快照"，不会随时间变得不准。
    """
    from urllib.parse import quote

    lines = [
        "# 导出目录",
        "",
        f"- 导出时间：{datetime.now(timezone.utc).replace(microsecond=0).isoformat()}",
        f"- 正文版本：{'清洗版（clean）' if variant == 'clean' else '原始版（original）'}",
        f"- 笔记数量：{len(entries)}",
        "",
        "| 标题 | 文件名 | 来源 | 状态 |",
        "|---|---|---|---|",
    ]
    for entry in entries:
        filename = entry["filename"]
        # 表格里的 `|` 会破坏列结构 —— 标题里出现竖线时必须转义
        title = entry["title"].replace("|", "\\|")
        lines.append(
            f"| [{title}](./{quote(filename)}) | `{filename}` "
            f"| {entry.get('source_type', '')} | {entry.get('status', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def unique_zip_filename(title: str, used: set, fallback_id: str = "") -> str:
    """给 ZIP 内的文件取一个**不重名**的名字（不含目录，`.md` 结尾）

    ## 为什么必须去重

    同一个库里有 72 种标题重复（实测），其中 `标准适用范围` 出现了 9 次。
    直接用标题当文件名的话，`zipfile` **不报错**、后写的静默覆盖先写的 ——
    用户导出一个 ZIP，解压后发现少了 8 篇，而**全程没有任何提示**。

    做法：撞名时追加 ` (2)`、` (3)`…。与操作系统"复制副本"的命名习惯一致，
    所以用户不用学新规则。
    """
    stem = sanitize_filename(title, fallback_id=fallback_id)
    candidate = f"{stem}.md"
    if candidate not in used:
        used.add(candidate)
        return candidate
    n = 2
    while f"{stem} ({n}).md" in used:
        n += 1
    candidate = f"{stem} ({n}).md"
    used.add(candidate)
    return candidate
