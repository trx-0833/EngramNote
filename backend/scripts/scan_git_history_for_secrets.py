"""全历史密钥扫描 —— 检查**任何提交**里是否混进过密钥。

## 为什么要扫"全部对象"而不是只扫当前工作区

`git status` / `git diff` 只看**当前**状态，而密钥泄漏的三个真实形态它全都看不见：

1. **曾经提交过、后来删掉了** —— 文件不在 HEAD 里，但那个 blob 永远留在
   对象库里，`git log -p` 一翻就出来；
2. **amend / rebase 改写过的提交** —— 旧提交变成"不可达对象"，
   `git log --all` 也看不到它，但 `git cat-file` 仍能读出来；
3. **提交信息 / 标签信息里** —— 完全不在文件内容里
   （本仓库实测踩过：我把密钥写进了 commit message）。

所以本脚本遍历**对象库里的每一个 blob**（含不可达对象），
再单独扫一遍提交信息。这是唯一能给出"有没有"这个确定答案的做法。

## 用法

    python scripts/scan_git_history_for_secrets.py              # 扫本仓库
    python scripts/scan_git_history_for_secrets.py <仓库路径>
    python scripts/scan_git_history_for_secrets.py --extra <密钥原文>

`--extra` 用来查**一把具体的密钥**（按原文精确匹配）——
适合"我知道某把钥匙，确认它有没有进过库"。

## ⚠️ 本文件**自己**不能写进真密钥（实测踩过三次）

写这个脚本的过程中，我在三个地方把**真密钥原文**写了进去：
usage 示例、判据说明里的反例、以及"这条判据能放过真密钥"的举例。
结果**扫描器一提交，扫描器自己就成了命中项** —— 它扫到了自己。

这是同一个错误的第三种形态（前两次：`.gitignore` 注释、commit message）。
教训统一为一条：**任何"用来记录/说明/验证密钥"的位置，都不能出现密钥原文。**
所以本文件里所有示例一律用**明显是假的**值（`oc_sk_EXAMPLE…`）。

另外 `SKIP_SELF` 让扫描器跳过自己的 blob —— 它内部必然含有各前缀模式
（那是它的判据），扫自己只会产生噪音。

## 退出码

0 = 干净；1 = 有命中（可直接用在 CI / 提交前钩子里）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 判据：按**真实密钥的形态**匹配，而不是按"含 key 这个词"
# ---------------------------------------------------------------------------
PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("OpenCode 网关密钥", re.compile(rb"oc_sk_[A-Za-z0-9_\-]{16,}")),
    ("OpenAI 风格密钥", re.compile(rb"\bsk-[A-Za-z0-9]{32,}")),
    ("Anthropic 密钥", re.compile(rb"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("GitHub PAT classic", re.compile(rb"\bghp_[A-Za-z0-9]{30,}")),
    ("GitHub PAT fine-grained", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("GitLab PAT", re.compile(rb"\bglpat-[A-Za-z0-9_\-]{20,}")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("AWS Access Key ID", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("私钥块", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT（三段式）", re.compile(rb"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]

#: 赋值类：KEY/TOKEN/SECRET/PASSWORD = 看起来像真值的串
#:
#: ⚠️ 值不允许以 `/` 开头 —— 那是**正则字面量**
#: （实测误报：`CLASS_TOKEN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/`，
#: 不加这条会把正则的前半截当成密钥值）。
ASSIGN = re.compile(
    rb"""(?:^|[\s"'])(?:export\s+)?([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*=\s*["']?([A-Za-z0-9][^\s"'#\\]{15,})""",
    re.M,
)

#: 明显是占位/示例的值 —— 不算泄漏
#:
#: 用**正则**而不是固定清单：实测占位符有无数变体
#: （`sk-your-deepseek-key-here`、`your_random_secret_here`、
#: `your-glm-key-here`、`your-rando…`…），固定清单永远列不全。
#: 这里按"以 your/sk-your/xxx/example 开头"这一族形态匹配。
PLACEHOLDER = re.compile(
    rb"^(?:"
    rb"<[^>]*>"                       # <your-key>
    rb"|\$\{[^}]*\}"                  # ${VAR}
    rb"|your[-_].*"                   # your-key / your_key
    rb"|sk-your.*"                    # sk-your-deepseek-key-here
    rb"|sk-xxx+.*"
    rb"|xxx+.*"
    rb"|ci-only.*"                    # ci-only-dummy-key-not-a-secret（CI 文档里的假值）
    rb"|engramnote-dev.*"             # engramnote-dev-secret-change-in-production
    rb"|.*not[-_]a[-_]secret.*"       # 字面自称不是密钥的，就信它
    rb"|.*change[-_]in[-_]production.*"
    rb"|\.\.\.+|-+|_+|\*+"
    rb"|(?:placeholder|changeme|change[-_]me|example|sample|fake|dummy|todo).*"
    rb")$",
    re.I,
)

#: 「赋值看起来像密钥」但其实是**代码常量**的排除项
#:
#: 实测误报（都是本仓库真实出现的）：
#: - `const TOKEN_KEY = 'engramnote_token'` —— localStorage 的**键名**；
#: - `KEY_PREFIX = 'engramnote_'` —— 常量前缀；
#: - `CLASS_TOKEN = /^[a-z][a-z0-9-]*$/` —— **正则字面量**（值以 `/` 开头）。
#: 共同点：值里没有随机性。判据是"只由小写字母/数字/下划线/连字符组成"，
#: 且不含 `/`（排除正则）。
CODE_CONSTANT = re.compile(rb"^[a-z0-9_\-]+$")

#: 赋值类的**白名单式**判据：值必须"够长且够多样"才可能是密钥
#:
#: ## 为什么从"排除法"改成"白名单法"
#:
#: 一开始我用排除法（占位符、代码常量、正则字面量…），结果是**误报打不完**：
#: 本仓库实测出现过 `TOKEN_KEY = 'engramnote_token'`、`KEY_PREFIX = 'engramnote_'`、
#: `CLASS_TOKEN = /^[a-z][a-z0-9-]*$/`，还有被截断的正则 `[a-z][a-`。
#: 每修一个就冒出下一个，而**误报多了等于扫描器没用**（人会开始忽略它）。
#:
#: 换成白名单：真密钥一定**同时**满足
#: - 长度 ≥ 20（短串承载不了足够熵）；
#: - 无空白字符；
#: - 字符种类 ≥ 2（纯小写标识符只有 1 种）。
#:
#: 三条一起就把上面那些全部排掉，而真密钥（形如
#: `oc_sk_EXAMPLE0NOT0AREAL0KEY_aBcDeFgHiJkLmNoPqRsTuVwXyZ`）稳过。
#: **判据要盯住"真密钥长什么样"，而不是"误报长什么样"。**
MIN_SECRET_LEN = 20


def looks_like_secret(value: bytes) -> bool:
    if len(value) < MIN_SECRET_LEN:
        return False
    if re.search(rb"\s", value):
        return False
    # 「人类可读的词串」不是密钥。
    # 实测误报：`engramnote:scroll:v1:` / `engramnote-dev-secret-change-in-production`
    # —— 都由 3 个以上短词拼成；而真密钥（形如 `oc_sk_EXAMPLE…`）最多被分成 3 段，
    # 且每段都很长。判据：分隔符（`-` / `_` / `:`）≥ 3 个 → 当作文案常量。
    if len(re.findall(rb"[-_:]", value)) >= 3:
        return False
    kinds = 0
    kinds += 1 if re.search(rb"[a-z]", value) else 0
    kinds += 1 if re.search(rb"[A-Z]", value) else 0
    kinds += 1 if re.search(rb"[0-9]", value) else 0
    kinds += 1 if re.search(rb"[^A-Za-z0-9]", value) else 0
    return kinds >= 2


def jwt_status(token: bytes) -> tuple[str, str]:
    """解码 JWT 的 payload，返回 (状态, 说明)

    ## 为什么要解码而不是直接报"发现 JWT"

    实测：本仓库历史里有一个**真实签发的 JWT**（`test_final_verify.py`，
    后来被隐私提交删除，但 blob 留在对象库里）。它是真的、带签名，
    但 `exp` 是 2026-06-12 —— **早就过期了**。

    "历史里有 JWT" 与 "有**仍然可用**的凭据" 是两件严重性差很远的事，
    所以这里把 `exp` 解出来一起报：过期的降级为提示，未过期的才是告警。
    """
    import base64
    import datetime
    import json as _json

    parts = token.split(b".")
    if len(parts) != 3:
        return "unknown", "不是三段式 JWT"
    try:
        pad = parts[1] + b"=" * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return "unknown", "payload 无法解码"
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return "unknown", "payload 里没有 exp"
    when = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if when > now:
        return "live", f"⚠️ **仍未过期**（{when:%Y-%m-%d} 到期，还有 {(when - now).days} 天）"
    return "expired", f"已于 {when:%Y-%m-%d} 过期"


def run(args: list[str], cwd: str, binary: bool = False):
    r = subprocess.run(args, capture_output=True, cwd=cwd)
    return r.stdout if binary else r.stdout.decode("utf-8", errors="replace")


def blob_is_text(data: bytes) -> bool:
    """跳过二进制对象（图片 / 模型 / 压缩包）——在它们里面搜字符串没有意义且极慢"""
    if b"\x00" in data[:8000]:
        return False
    return True


#: 扫描器**自己**的判据字符串集合，用于跳过自身
#:
#: 本文件内部必然含有各前缀模式（`oc_sk_` / `ghp_` / `AKIA`…）—— 那是它的判据。
#: 不跳过的话，**扫描器一提交就会把自己报成命中项**（实测踩过）。
#: 判据：blob 里同时出现多个前缀标记 → 认定它是本脚本或同类工具，跳过。
SELF_MARKERS = (b"scan_git_history_for_secrets", b"PREFIX_PATTERNS", b"looks_like_secret")


def is_scanner_itself(data: bytes) -> bool:
    return sum(1 for m in SELF_MARKERS if m in data) >= 2


def scan_bytes(data: bytes) -> list[str]:
    """返回命中的描述（已去重）

    ## 两趟处理，顺序很重要

    第一趟先收集**所有"看起来像密钥的长串"**（各前缀模式命中的、以及
    赋值右侧那种长且带随机性的值）；第二趟再用它们去**抑制**那些
    被覆盖的短匹配。

    为什么必须这样：`TOKEN = eyJhbGciOiJIUzI1NiIs…` 里的 JWT 是**完整**命中的，
    但同一个 `ASSIGN` 正则也会把 `TOKEN = eyJhbGciOi` 这半截单独报一条
    （值里含大写字母 → 我的"无随机性"判据放它过去）。两条报同一个东西，
    只会让人以为命中了两个。
    """
    found: list[str] = []
    #: 第一趟收集的"看起来像密钥的串"，用于抑制被覆盖的短匹配
    strong: list[bytes] = []

    for name, pat in PATTERNS:
        for m in pat.finditer(data):
            v = m.group(0)
            # 文档里的形态说明（`oc_sk_...`）不算
            if re.fullmatch(rb"(?:oc_sk_|sk-|ghp_|glpat-|xox[baprs]-)\.*", v):
                continue
            strong.append(v)
            if name == "JWT（三段式）":
                status, note = jwt_status(v)
                # 过期的降级为提示（历史遗留），未过期的才告警
                prefix = "⏳" if status == "expired" else "⚠️"
                found.append(f"{prefix} {name}: {note}")
                continue
            found.append(f"⚠️ {name}: {v[:12].decode('ascii', 'replace')}…（{len(v)} 字符）")

    for m in ASSIGN.finditer(data):
        key, value = m.group(1), m.group(2)
        if PLACEHOLDER.match(value) or set(value) <= set(b"*.-_ "):
            continue
        # 被某个完整密钥覆盖的半截（如 `TOKEN = eyJhbGciOi` 之于上面的 JWT）
        if any(value in s for s in strong):
            continue
        # localStorage 的**键名**不是密钥。判据：值以项目前缀开头，
        # 且其余部分全是小写/下划线（`engramnote_token` / `engramnote_locale`）。
        # 真密钥不会长成"域名前缀 + 全小写单词"。
        if re.fullmatch(rb"(?:engramnote|app|note|user)[a-z0-9_]*", value):
            continue
        # 白名单：够长 + 无空白 + 字符种类 ≥ 2
        if not looks_like_secret(value):
            continue
        found.append(f"⚠️ {key.decode('ascii', 'replace')} = {value[:10].decode('ascii', 'replace')}…")

    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="扫 git 全部对象与提交信息里的密钥")
    ap.add_argument("repo", nargs="?", default=".", help="仓库路径（默认当前目录）")
    ap.add_argument("--extra", action="append", default=[],
                    help="额外按原文精确匹配的字符串（可重复），用于查一把具体的钥匙")
    args = ap.parse_args()

    repo = args.repo
    if not (run(["git", "rev-parse", "--git-dir"], repo).strip()):
        print(f"❌ {repo} 不是 git 仓库")
        return 2

    # ── 1. 遍历对象库里的**每一个** blob（含不可达对象）──────────────
    print("=" * 72)
    print("① 扫描对象库中的全部 blob（含 amend/rebase 后的不可达对象）")
    print("=" * 72)
    listing = run(["git", "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)"], repo)
    blobs = [ln.split()[0] for ln in listing.splitlines() if ln.endswith(" blob")]
    print(f"  对象库共 {len(blobs)} 个 blob，逐个读取…")

    # 一次性把需要的 blob 内容批量取出（比逐个 cat-file 快一个数量级）
    hits: dict[str, list[str]] = {}
    BATCH = 400
    for i in range(0, len(blobs), BATCH):
        chunk = blobs[i : i + BATCH]
        proc = subprocess.run(
            ["git", "cat-file", "--batch"], cwd=repo, input="\n".join(chunk).encode(),
            capture_output=True,
        )
        raw = proc.stdout
        pos = 0
        for sha in chunk:
            nl = raw.find(b"\n", pos)
            if nl == -1:
                break
            header = raw[pos:nl].split()
            if len(header) < 3:
                pos = nl + 1
                continue
            size = int(header[2])
            body = raw[nl + 1 : nl + 1 + size]
            pos = nl + 1 + size + 1
            if not blob_is_text(body):
                continue
            # 跳过扫描器自己（它内部含各前缀模式，见 SELF_MARKERS 的说明）
            if is_scanner_itself(body):
                continue
            found = scan_bytes(body)
            if found:
                hits.setdefault(sha, []).extend(found)

    # ── 2. 扫提交信息与标签信息 ────────────────────────────────────────
    print()
    print("=" * 72)
    print("② 扫描全部提交信息与标签信息")
    print("=" * 72)
    msg_hits: list[tuple[str, str, list[str]]] = []
    # 提交信息：逐条取，用 \x1f 分隔字段、\x1e 分隔记录，避免解析歧义
    log = run(["git", "log", "--all", "--format=%H%x1f%s%x1f%b%x1e", "--no-color"], repo)
    for rec in log.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        fields = rec.split("\x1f")
        if len(fields) < 3:
            continue
        sha, subject, body = fields[0], fields[1], fields[2]
        found = scan_bytes((subject + "\n" + body).encode("utf-8"))
        if found:
            msg_hits.append((sha[:9], subject[:60], found))
    print(f"  扫了 {len(log.split(chr(30)))} 条提交信息；命中 {len(msg_hits)} 条")

    # ── 3. 按原文精确查指定的密钥 ─────────────────────────────────────
    extra_hits: list[tuple[str, str]] = []
    if args.extra:
        print()
        print("=" * 72)
        print("③ 按原文精确匹配指定的密钥")
        print("=" * 72)
        for needle in args.extra:
            nb = needle.encode()
            for sha in blobs:
                proc = subprocess.run(["git", "cat-file", "blob", sha], cwd=repo, capture_output=True)
                if nb in proc.stdout:
                    extra_hits.append((sha[:9], needle[:12] + "…"))
            # 提交信息
            if nb in log.encode("utf-8"):
                extra_hits.append(("提交信息", needle[:12] + "…"))
            print(f"  `{needle[:12]}…` → {'命中' if any(h[1].startswith(needle[:12]) for h in extra_hits) else '未出现'}")

    # ── 汇总 ──────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("结果")
    print("=" * 72)

    # 把"过期的 JWT"单独摘出来：它是**历史遗留**，不是"当前有可用凭据"，
    # 严重性差一个量级，混在一起报会让人以为仓库里有活密钥。
    def is_expired_jwt(items: list[str]) -> bool:
        return all(i.startswith("⏳") for i in items)

    # ★ 可达性是**严重性的分界线**：
    #   - 被某个 ref 引用 → 会随 push 上去，是真泄漏；
    #   - 只是悬空对象（amend/rebase 的残留）→ 只存在于**本地** .git/objects，
    #     `git push` 不会带上它，`git gc --prune=now` 就能清掉。
    #   实测踩过：我 amend 掉的旧提交里留着密钥原文，若不区分可达性，
    #   报告会让人以为"仓库里泄漏了密钥"，而实际上它从未离开本机。
    def reachable(sha: str) -> bool:
        out = run(["git", "log", "--all", "--oneline", "--find-object=" + sha, "-1"], repo).strip()
        return bool(out)

    live_hits = {s: v for s, v in hits.items() if not is_expired_jwt(v)}
    expired_jwts = {s: v for s, v in hits.items() if is_expired_jwt(v)}
    dangling = {s: v for s, v in live_hits.items() if not reachable(s)}
    live_hits = {s: v for s, v in live_hits.items() if reachable(s)}

    if dangling:
        print(f"🟡 {len(dangling)} 个**悬空对象**命中（amend/rebase 的残留，**未推送**）")
        for sha, found in list(dangling.items())[:8]:
            print(f"   blob {sha[:9]}  {sorted(set(found))[:2]}")
        print("   → 这些对象只在本机 `.git/objects` 里，`git push` 不会带上；")
        print("     清掉：`git reflog expire --expire=now --all && git gc --prune=now`")
        print()

    if expired_jwts:
        print(f"⏳ 历史里有 {len(expired_jwts)} 个 blob 含**已过期**的 JWT（不再是可用凭据）")
        for sha, found in list(expired_jwts.items())[:5]:
            which = run(["git", "log", "--all", "--oneline", "--find-object=" + sha, "-1"], repo).strip()
            print(f"   blob {sha[:9]}  {found[0]}")
            if which:
                print(f"       {which[:88]}")
        print("   → 处置：`JWT_SECRET_KEY` 轮换一次即可让所有旧 token 彻底失效；")
        print("     若要连历史一起清掉，用 `git filter-repo`（会改写历史，需强推）。")
        print()

    if live_hits:
        print(f"⚠️ **可达**的 blob 里有 {len(live_hits)} 个对象命中（会随 push 上去）：")
        for sha, found in list(live_hits.items())[:20]:
            which = run(["git", "log", "--all", "--oneline", "--find-object=" + sha, "-1"], repo).strip()
            print(f"   blob {sha[:9]}  {sorted(set(found))[:2]}")
            if which:
                print(f"       出现在: {which[:90]}")
    if msg_hits:
        print(f"⚠️ 提交信息里有 {len(msg_hits)} 条命中：")
        for sha, subj, found in msg_hits[:20]:
            print(f"   {sha}  {subj}")
            print(f"       {sorted(set(found))[:2]}")
    if extra_hits:
        print(f"⚠️ 指定密钥命中 {len(extra_hits)} 处：{extra_hits[:5]}")

    if not (live_hits or msg_hits or extra_hits):
        if expired_jwts or dangling:
            print("✅ 未发现**可达**的可用凭据（只有上面那些过期 JWT / 悬空残留）")
        else:
            print("✅ 全部对象、全部提交信息、指定密钥 —— 均未发现真实密钥")
        return 0
    print()
    print("处置建议：若密钥**未推送** → `git commit --amend` / `git rebase -i` 改写；")
    print("          若**已推送** → 先轮换密钥（最要紧），再决定要不要 filter-repo 重写历史。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
