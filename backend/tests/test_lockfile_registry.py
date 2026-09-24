"""`frontend/package-lock.json` 的取源守卫（供应链判据）

## 这个守卫守的是什么

`frontend/package-lock.json` 里每个条目都带 `resolved`（tarball 地址）与 `integrity`
（内容哈希）。**`integrity` 能防篡改，但防不住"从哪拿"这件事** ——
如果 `resolved` 指向第三方镜像，那么：

- 访客/审阅者在锁文件里看到一批陌生主机名，无法判断这是不是有意的；
- 镜像可以滞后、可以不可用，于是"本地装得上、CI 装不上"；
- 这正是 `docs/open-source-readiness.md` §2.9 修过一次的问题。

## 为什么它会反复复发（这不是假想）

1. **开发机的全局 `.npmrc`**：本机实测 `npm config get registry` =
   `https://registry.npmmirror.com/`。只要一次 `npm install` **不带** `--registry`，
   npm 就会把镜像 URL **批量写回锁文件**。实测证据：`_cacache` 索引里
   `registry.npmmirror.com` 有 1014 条、`registry.npmjs.org` 只有 77 条。
2. **Dependabot 每次重写锁文件**：都是一次重犯机会。

失效形态是**静默**的：锁文件照样 `npm ci` 成功，测试照样全绿，
没人会注意到包里换了取源主机。

## 判据

- 每个带 `resolved` 的条目，主机必须是 `frontend/.npmrc` 声明的那个（见下）；
- 每个带 `resolved` 的条目必须带 `integrity`（缺了就没法校验内容）；
- 项目内的 `frontend/.npmrc` **不得**存在"镜像源配置"——
  它是本守卫的判据来源，不能被本地习惯改掉。

**失败时怎么修**（错误信息里也会打印）：

    # 删掉锁文件后用官方源重装（必须显式带 --registry）
    npm install --registry=https://registry.npmjs.org
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
LOCKFILE = FRONTEND / "package-lock.json"
NPMRC = FRONTEND / ".npmrc"

#: 唯一允许的取源主机。它必须与 `frontend/.npmrc` 里的 `registry=` 一致（下面的用例会断言）。
ALLOWED_HOST = "registry.npmjs.org"

#: 已知的第三方镜像主机（出现即失败，且错误信息里点名"这是镜像"）。
MIRROR_HOSTS = {
    "registry.npmmirror.com",
    "registry.npm.taobao.org",
    "npmmirror.com",
    "registry.yarnpkg.com",
    "skimdb.npmjs.com",
}


def _lock() -> dict:
    assert LOCKFILE.is_file(), f"找不到锁文件：{LOCKFILE}"
    return json.loads(LOCKFILE.read_text(encoding="utf-8"))


def _resolved_entries() -> list[tuple[str, str]]:
    """返回 [(条目名, resolved 主机)]，只含带回 resolved 的条目。"""
    out: list[tuple[str, str]] = []
    for name, meta in _lock()["packages"].items():
        if not name:
            continue  # 根条目没有 resolved
        resolved = meta.get("resolved")
        if isinstance(resolved, str):
            out.append((name, urlparse(resolved).netloc))
    return out


class TestLockfileRegistry:
    def test_锁文件版本是_3(self):
        """lockfileVersion 3 = 有 `packages` 段，本守卫的所有判据都建立在它上面。"""
        assert _lock()["lockfileVersion"] == 3, (
            "lockfileVersion 变了（npm 大版本升级可能改它）——本守卫读的是 `packages` 段，"
            "换版本前请先确认结构"
        )

    def test_每个条目都来自允许的主机(self):
        entries = _resolved_entries()
        assert entries, "锁文件里一个带 resolved 的条目都没有？结构可能变了"

        offenders = [(n, h) for n, h in entries if h != ALLOWED_HOST]
        assert not offenders, (
            f"{len(offenders)} 个条目的取源主机不是 {ALLOWED_HOST}：\n  "
            + "\n  ".join(f"{n} → {h}" for n, h in offenders[:15])
            + f"\n\n（共 {len(entries)} 个条目）"
            "\n修法：npm install --registry=https://registry.npmjs.org"
            " —— 本机全局 npm 源可能是镜像，必须显式带 --registry"
        )

    def test_没有已知镜像主机(self):
        """与上一条重叠，但失败信息不同：这条会**点名**镜像。"""
        mirrors = [(n, h) for n, h in _resolved_entries() if h in MIRROR_HOSTS]
        assert not mirrors, (
            "锁文件里出现了镜像源条目（这是 §2.9 修过的问题，别让它回来）：\n  "
            + "\n  ".join(f"{n} → {h}" for n, h in mirrors[:15])
        )

    def test_每个条目都带_integrity(self):
        missing = [
            name
            for name, meta in _lock()["packages"].items()
            if name and isinstance(meta.get("resolved"), str) and not meta.get("integrity")
        ]
        assert not missing, (
            f"{len(missing)} 个条目有 resolved 却没有 integrity（无法校验内容）：\n  "
            + "\n  ".join(missing[:15])
        )

    def test_直接依赖都在锁文件里(self):
        """声明了就必须有一条锁文件记录 —— 防"锁文件与 package.json 脱节"。"""
        pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
        packages = _lock()["packages"]
        declared = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }
        missing = [name for name in declared if f"node_modules/{name}" not in packages]
        assert not missing, (
            f"这些依赖在 package.json 里声明了，锁文件里却没有记录：{missing}"
            "\n（症状：本地能跑，`npm ci` 会失败或装出不同的树）"
        )


class TestNpmrcIsTheJudgementSource:
    """`frontend/.npmrc` 是本守卫判据的来源，不能被本地习惯改掉。"""

    def test_npmrc_存在且指向官方源(self):
        assert NPMRC.is_file(), (
            f"缺少 {NPMRC}。它必须存在并把 registry 钉在 {ALLOWED_HOST}："
            "否则本机全局 npm 源（可能是镜像）会成为默认值，"
            "一次不带 --registry 的 npm install 就会污染锁文件。"
        )
        text = NPMRC.read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s*registry\s*=\s*(\S+)\s*$", text)
        assert match, f"{NPMRC} 里没有 `registry=` 一行，内容：{text!r}"
        host = urlparse(match.group(1)).netloc
        assert host == ALLOWED_HOST, (
            f"{NPMRC} 把 registry 指向了 {host}，而本守卫的判据是 {ALLOWED_HOST}。"
            "\n若确实要换源，两处必须一起改，并且要想清楚锁文件里的 resolved 会变成什么。"
        )
