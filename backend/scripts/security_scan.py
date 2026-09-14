#!/usr/bin/env python
"""依赖安全扫描入口（overhaul-plan 阶段 6.7）

## 为什么需要这个脚本

6.7 的验收是"无高危 CVE"，但项目里此前**一个扫描器都没跑过** ——
这正是本仓库反复踩到的形态：能力"配置了"却从没有人执行，于是没有任何
断言能发现它其实没生效（对照 6.5 的 rate limit 规则从未匹配上、
CI 的依赖清单与实际漂移）。

所以这里的目标不是"再写一份报告文本"，而是给出一条**任何人都能一条命令
重跑**的路径，并且让"扫描器根本没跑起来"变成**非零退出码**，而不是
报告里一句容易被忽略的"跳过"。

## 用法

    # 仓库根目录或 backend/ 下均可
    python backend/scripts/security_scan.py
    python scripts/security_scan.py --json          # 机器可读（供 CI 归档）
    python scripts/security_scan.py --fail-on high  # 把高危/严重当阻断门禁

退出码：

    0  扫描完成，且没有达到 `--fail-on` 阈值的发现
    1  有发现达到 `--fail-on` 阈值
    2  **必需的扫描器没能运行**（未安装 / 网络失败）—— 不是"没发现问题"

`--fail-on` 默认 `none`（只报告不阻断）。理由：本脚本今天在本仓库上
必然会报出 11 条 npm 建议（都在构建/测试工具链里）与 1 条上游明确
"不会有修复"的 Python 建议，若默认就红灯，CI 会长期常红，最终被人
`|| true` 掉 —— 那比没有门禁更糟。门禁阈值留给使用方显式选择。

## 依赖

只依赖标准库。被调用的扫描器各自需要：

    pip-audit   →  python -m pip install pip-audit
    npm audit   →  随 npm 提供（注意：镜像源可能不实现 audit 端点，见下）
    trivy       →  https://github.com/aquasecurity/trivy/releases （可选）

## 两个真实的坑（都已在本脚本内处理，不要"简化"掉）

1. **Windows + 中文 locale 下 pip-audit 直接崩溃。**
   `pip-audit` 用 `pip-requirements-parser` 读 requirements 文件，后者按
   `locale.getpreferredencoding()` 解码 —— 本机是 GBK，而
   `requirements.txt` / `requirements-test.txt` 是 UTF-8 且带中文注释，
   于是 `UnicodeDecodeError: 'gbk' codec can't decode byte ...`。
   修法不是"把注释删了"，而是给子进程设 `PYTHONUTF8=1`（Python UTF-8 模式
   会让 getpreferredencoding() 返回 utf-8）。见 `_scanner_env()`。

2. **本机 npm 源是 registry.npmmirror.com，它没有实现 audit 端点。**
   `npm audit` 会返回
   `404 ... /-/npm/v1/security/audits/quick - [NOT_IMPLEMENTED]`。
   这不是"审计通过"，而是"审计没跑"。脚本会显式回退到官方源并把
   "用了哪个源"写进报告，避免把 404 误读成 0 条发现。

## 覆盖不到的部分（不要当成"已扫描 = 安全"）

- **无 CVE 数据 ≠ 无漏洞**：三个扫描器都只比对公开公告库，
  业务逻辑漏洞、越权、SSRF 这类它们一律看不见。
- **仅 SQLite / 无容器运行时**：镜像扫描在本项目没有实际对象
  （`docker-compose.yml` 只在部署时才 build）；Trivy 即使可用，
  有价值的是 `fs` 的 misconfig/secret 部分。
- **运行时行为**：本脚本不发起任何请求，不验证漏洞是否真的可达。
  "可达性"判断写在 `docs/security-scan.md` 里，由人做。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 路径 ────────────────────────────────────────────────────────────────
# 本文件在 <root>/backend/scripts/ 下，parents[0]=scripts, [1]=backend, [2]=root
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# ── 严重度归一化 ────────────────────────────────────────────────────────
# 三个工具各用各的词汇：npm 用 moderate、Trivy 用 MEDIUM、pip-audit 干脆
# 不提供严重度。统一成一套词表才排得出"最严重的是什么"。
#
# ⚠️ 曾经的 bug：这里原本是 `rank -> label` 的反查表，于是任何**认不出**的键
# （尤其是 npm 的 `metadata.vulnerabilities.total`）都会落到 "unknown"，
# 导致汇总表里 npm 那一行出现 `unknown 11`、合计 22 而不是 11。
# 现在改成白名单映射：只有列出的词才被认，其余一律 unknown，
# 调用方也只遍历已知的等级键，不再把 `total` 之类的统计键喂进来。
_SEVERITY_ALIASES = {
    "critical": "critical",
    "high": "high",
    "moderate": "moderate",
    "medium": "moderate",  # Trivy 用 MEDIUM
    "low": "low",
    "info": "info",  # npm 的最低档，低于 low
}
SEVERITY_ORDER = ["critical", "high", "moderate", "low", "info", "unknown"]
# --fail-on 的比较基准。info / unknown 记 0：它们永远不会触发 >= low 的阈值
_SEVERITY_RANK = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "info": 0, "unknown": 0}

# 进程启动时刻（main 末尾用它算总耗时）
_T0 = time.monotonic()


def _norm_severity(raw: str | None) -> str:
    """把各家的严重度词表归一化成本脚本的 6 档（认不出的一律 unknown）。"""
    return _SEVERITY_ALIASES.get((raw or "").strip().lower(), "unknown")


def _scanner_env() -> dict:
    """子进程环境：修掉 Windows/GBK 下 pip-audit 的 UnicodeDecodeError。

    `PYTHONUTF8=1` 打开 Python UTF-8 模式，使
    `locale.getpreferredencoding()` 返回 utf-8；
    `PYTHONIOENCODING` 保证子进程自身的中文输出不会用 GBK 编码回来。
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # npm 输出里带颜色转义会让 JSON 解析失败
    env["NO_COLOR"] = "1"
    return env


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> tuple[int, str, str]:
    """跑一个子进程，返回 (returncode, stdout, stderr)。

    超时/找不到可执行文件都返回 rc=-1 并带上原因，而不是抛异常 ——
    调用方需要"扫描器没跑起来"这个信息本身。
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=_scanner_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return -1, "", f"找不到可执行文件: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"超时（>{timeout}s）: {' '.join(cmd)}"


def _python_exe() -> str:
    """当前解释器。pip-audit 必须装在**同一个**环境里才有意义。"""
    return sys.executable


def _configure_console() -> None:
    """让报告里的字符不会因为控制台编码而把整个扫描结果吞掉。

    Windows 中文控制台默认 GBK，打印非 GBK 字符（如 ✗ / ⚠）会直接抛
    UnicodeEncodeError —— 注意失败点在**打印阶段**：扫描已经跑完了，
    但一个字都出不来，使用者看到的是 traceback 而不是"有问题 / 没问题"。
    这与本模块头部记的 pip-audit 那个 GBK 崩溃是同一类坑。

    策略分两种，依据是"谁来读这些字节"：

    - **stdout 不是终端（管道 / CI 日志 / GUI 输出面板）**：显式用 UTF-8。
      Python 在这种情形下仍按 `locale.getpreferredencoding()` 编码（本机是 GBK），
      而读它的那一端按 UTF-8 解码 —— 中文全变乱码。管道场景几乎总是 UTF-8 消费方。
    - **stdout 是终端**：保持终端自己的编码不动，只把错误策略改成 replace
      （表达不了的符号降级成 `?`）。硬写 UTF-8 会在真 GBK 控制台上把中文
      变成乱码 —— 那是把问题换了个方向，不是解决。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # 非文本流（被重定向到某些包装器）时放弃，不值得为此失败
            pass


# ── pip-audit ───────────────────────────────────────────────────────────
def pip_audit_available() -> bool:
    rc, _, _ = _run([_python_exe(), "-s", "-m", "pip_audit", "--version"], BACKEND, timeout=120)
    return rc == 0


def scan_python(requirements: Path) -> dict:
    """对一份 requirements 文件跑 pip-audit。

    [!]️ 语义要点：pip-audit 的 `-r` 模式是**先解析（含传递依赖）再查公告库**，
    所以报告反映的是"今天全新安装会拿到的版本集合"，而不是本机 conda 环境
    里已装的版本。对 CI 而言前者才是要审计的对象（CI 就是全新安装）。
    本机环境与解析结果不一致时，差异由 `docs/security-scan.md` 记录。
    """
    rc, out, err = _run(
        [
            _python_exe(),
            "-s",
            "-m",
            "pip_audit",
            "-r",
            str(requirements),
            "--progress-spinner",
            "off",
            "--format=json",
        ],
        cwd=requirements.parent,
    )
    result: dict = {
        "scanner": "pip-audit",
        "target": str(requirements.relative_to(ROOT)).replace("\\", "/"),
        "ok": False,
        "error": None,
        "resolved_packages": 0,
        "findings": [],
    }
    if rc == -1:
        result["error"] = err.strip()
        return result

    # pip-audit: 0 = 无发现，1 = 有发现（不是错误），>1 = 真的出错
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        result["error"] = f"pip-audit 输出不是 JSON（rc={rc}）: {(err or out).strip()[:400]}"
        return result

    deps = payload.get("dependencies") or []
    result["ok"] = True
    result["resolved_packages"] = len(deps)
    for dep in deps:
        for vuln in dep.get("vulns") or []:
            result["findings"].append(
                {
                    "package": dep.get("name"),
                    "installed": dep.get("version"),
                    # pip-audit 不提供严重度，显式标 unknown（见模块头）
                    "severity": "unknown",
                    "id": vuln.get("id"),
                    "aliases": vuln.get("aliases") or [],
                    "fix_versions": vuln.get("fix_versions") or [],
                    "description": (vuln.get("description") or "").strip(),
                }
            )
    return result


# ── npm audit ───────────────────────────────────────────────────────────
def _npm_cmd() -> str | None:
    """Windows 上必须用 npm.cmd：npm.ps1 被执行策略拦，npm 无扩展名解析不到。"""
    for name in ("npm.cmd", "npm"):
        found = shutil.which(name)
        if found:
            return found
    return None


def scan_node() -> dict:
    """`npm audit --json`。

    先按项目配置的源跑；若该源没实现 audit 端点（npmmirror 就是如此，
    返回 404 NOT_IMPLEMENTED），自动回退官方源并把两件事都记进报告：
    "用了哪个源"和"为什么换源"。绝不能把 404 当成"0 条发现"。
    """
    result: dict = {
        "scanner": "npm audit",
        "target": "frontend/package-lock.json",
        "ok": False,
        "error": None,
        "registry": None,
        "registry_fallback": False,
        "counts": {k: 0 for k in SEVERITY_ORDER},
        "total": 0,
        "dependency_counts": {},
        "findings": [],
    }
    npm = _npm_cmd()
    if not npm or not (FRONTEND / "package-lock.json").is_file():
        result["error"] = "找不到 npm 可执行文件，或 frontend/package-lock.json 不存在"
        return result

    rc, out, err = _run([npm, "audit", "--json"], cwd=FRONTEND, timeout=900)
    used_official = False
    if rc != 0 and "NOT_IMPLEMENTED" in (out + err):
        # 镜像源没有 audit 端点 —— 换官方源重跑
        rc, out, err = _run(
            [npm, "audit", "--json", "--registry=https://registry.npmjs.org"],
            cwd=FRONTEND,
            timeout=900,
        )
        used_official = True

    # npm 在 stdout 前面可能夹警告行，定位第一个 '{'
    start = out.find("{")
    if start < 0:
        result["error"] = f"npm audit 未返回 JSON（rc={rc}）: {(err or out).strip()[:400]}"
        return result
    try:
        payload = json.loads(out[start:])
    except json.JSONDecodeError as exc:
        result["error"] = f"npm audit JSON 解析失败: {exc}"
        return result

    meta = payload.get("metadata") or {}
    result["ok"] = True
    result["registry"] = "https://registry.npmjs.org" if used_official else "项目配置的 registry"
    result["registry_fallback"] = used_official
    result["dependency_counts"] = meta.get("dependencies") or {}
    raw_counts = meta.get("vulnerabilities") or {}
    # 只遍历已知的等级键。npm 的同一对象里还有 `total`（以及将来可能的别的
    # 统计键），把整份 dict 直接归一化会把它算成一条 unknown（实测过这个 bug）。
    for key in ("critical", "high", "moderate", "low", "info"):
        if key in raw_counts:
            norm = _norm_severity(key)
            result["counts"][norm] = result["counts"].get(norm, 0) + int(raw_counts[key] or 0)

    for name, entry in (payload.get("vulnerabilities") or {}).items():
        severity = _norm_severity(entry.get("severity"))
        advisories = []
        via_deps = []
        for via in entry.get("via") or []:
            if isinstance(via, dict):
                advisories.append(
                    {
                        "title": via.get("title"),
                        "severity": _norm_severity(via.get("severity")),
                        "url": via.get("url"),
                        "range": via.get("range"),
                        "cvss": (via.get("cvss") or {}).get("score"),
                    }
                )
            else:
                via_deps.append(str(via))
        fix = entry.get("fixAvailable")
        if isinstance(fix, dict):
            fix_text = f"{fix.get('name')}@{fix.get('version')}" + ("（跨大版本）" if fix.get("isSemVerMajor") else "")
        elif fix is True:
            fix_text = "有"
        else:
            fix_text = "无"
        result["findings"].append(
            {
                "package": name,
                "severity": severity,
                "advisories": advisories,
                "via_dependencies": via_deps,
                "fix_available": fix_text,
                "vulnerable_range": entry.get("range"),
                "is_direct": entry.get("isDirect"),
            }
        )
    result["total"] = len(result["findings"])
    return result


# ── Trivy（可选） ───────────────────────────────────────────────────────
# trivy-db 拉取失败的典型特征。命中它就降级到不需要 DB 的扫描器，
# 而不是把整项标成失败 —— 见 scan_trivy 里的说明。
_DB_FAILURE_MARKERS = (
    "failed to download vulnerability DB",
    "DB error",
    "OCI artifact error",
    "no such host",
    "connection attempt failed",
    "i/o timeout",
    "context deadline exceeded",
)


def _is_db_download_failure(text: str) -> bool:
    return any(marker in text for marker in _DB_FAILURE_MARKERS)


def _short_db_error(text: str) -> str:
    """把 trivy 那一大段 OCI 报错压成一行，报告里够用即可。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and _is_db_download_failure(stripped):
            return stripped[:200]
    return " ".join(text.split())[:200]


def find_trivy() -> str | None:
    """找 Trivy：PATH 优先，其次本项目记录过的本机安装位置。"""
    found = shutil.which("trivy")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "trivy" / "trivy.exe",
        Path(os.environ.get("ProgramFiles", "")) / "trivy" / "trivy.exe",
        Path.home() / ".local" / "bin" / "trivy",
        Path("/usr/local/bin/trivy"),
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def scan_trivy() -> dict:
    """`trivy fs` —— 依赖 + 配置 + 密钥三合一。

    刻意跳过 node_modules/data/dist/.git：
    - node_modules 的依赖结论 npm audit 已经给了，Trivy 再遍历一遍只是慢；
    - backend/data 是真实用户资料（也可能含 .env），不应被上传/遍历。
    """
    result: dict = {
        "scanner": "trivy fs",
        "target": ".",
        "available": False,
        "ok": False,
        "error": None,
        "path": None,
        "vuln_skipped_reason": None,
        "counts": {k: 0 for k in SEVERITY_ORDER},
        "findings": [],
    }
    trivy = find_trivy()
    if not trivy:
        return result
    result["available"] = True
    result["path"] = trivy

    def _trivy_fs(scanners: str, extra: list[str]) -> tuple[int, str, str]:
        return _run(
            [
                trivy,
                "fs",
                "--quiet",
                "--format",
                "json",
                "--scanners",
                scanners,
                "--skip-dirs",
                "frontend/node_modules",
                "--skip-dirs",
                ".git",
                "--skip-dirs",
                "backend/data",
                "--skip-dirs",
                "frontend/dist",
                "--skip-dirs",
                "data",
                "--timeout",
                "15m",
                *extra,
                ".",
            ],
            cwd=ROOT,
            timeout=1800,
        )

    rc, out, err = _trivy_fs("vuln,misconfig,secret", [])
    # 漏洞库拉不下来时**降级**而不是整项失败：
    # misconfig / secret 两个扫描器用的是内置策略，不需要 trivy-db。
    # 实测本机 mirror.gcr.io 不通（IPv6 连接被拒），整项失败会让这两个
    # 真正能出结果的扫描器一起白跑 —— 而那正是"配置了但没生效"的温床。
    # 降级后必须**显式记下 vuln 没跑**，否则报告里"0 条漏洞"会被读成"没问题"。
    if rc != 0 and _is_db_download_failure(err + out):
        result["vuln_skipped_reason"] = _short_db_error(err + out)
        rc, out, err = _trivy_fs("misconfig,secret", ["--skip-db-update"])

    start = out.find("{")
    if rc != 0 or start < 0:
        result["error"] = f"trivy 执行失败 rc={rc}: {(err or out).strip()[:400]}"
        return result
    try:
        payload = json.loads(out[start:])
    except json.JSONDecodeError as exc:
        result["error"] = f"trivy JSON 解析失败: {exc}"
        return result

    result["ok"] = True
    for entry in payload.get("Results") or []:
        target = entry.get("Target")
        for vuln in entry.get("Vulnerabilities") or []:
            severity = _norm_severity(vuln.get("Severity"))
            result["findings"].append(
                {
                    "kind": "vuln",
                    "severity": severity,
                    "target": target,
                    "id": vuln.get("VulnerabilityID"),
                    "package": vuln.get("PkgName"),
                    "installed": vuln.get("InstalledVersion"),
                    "fixed": vuln.get("FixedVersion") or "",
                    "title": vuln.get("Title"),
                }
            )
        for mis in entry.get("Misconfigurations") or []:
            severity = _norm_severity(mis.get("Severity"))
            result["findings"].append(
                {
                    "kind": "misconfig",
                    "severity": severity,
                    "target": target,
                    "id": mis.get("ID") or mis.get("AVDID"),
                    "package": mis.get("Title"),
                    "title": (mis.get("Description") or "").strip()[:200],
                }
            )
        for secret in entry.get("Secrets") or []:
            # 只留规则名与位置，**绝不**把匹配到的密钥内容写进报告
            severity = _norm_severity(secret.get("Severity"))
            result["findings"].append(
                {
                    "kind": "secret",
                    "severity": severity,
                    "target": target,
                    "id": secret.get("RuleID"),
                    "package": secret.get("Title"),
                    "title": f"第 {secret.get('StartLine')} 行（内容已由 Trivy 脱敏，本脚本不落盘）",
                }
            )
    for finding in result["findings"]:
        result["counts"][finding["severity"]] = result["counts"].get(finding["severity"], 0) + 1
    return result


# ── 报告 ────────────────────────────────────────────────────────────────
def _severity_row(source: str, counts: dict) -> str:
    """汇总表的一行（列顺序由 SEVERITY_ORDER 决定，避免手抄列时漏掉某一档）。"""
    total = sum(counts.get(key, 0) for key in SEVERITY_ORDER)
    cells = "".join(f"{counts.get(key, 0):>9}" for key in SEVERITY_ORDER)
    return f"  {source:<26}{cells}{total:>7}"


def _severity_table(counts_by_source: dict) -> list:
    """把各来源的计数拼成一张表（每行一个来源，每列一个严重度）。"""
    rows = []
    for name, counts in counts_by_source.items():
        rows.append(
            {
                "source": name,
                **{key: counts.get(key, 0) for key in SEVERITY_ORDER},
                "total": sum(counts.get(key, 0) for key in SEVERITY_ORDER),
            }
        )
    return rows


def print_report(report: dict) -> None:
    out = sys.stdout
    bar = "=" * 78
    print(bar, file=out)
    print("EngramNote 依赖安全扫描（overhaul-plan 6.7）", file=out)
    print(f"  时间    : {report['started_at']}", file=out)
    print(f"  仓库    : {ROOT}", file=out)
    print(f"  解释器  : {_python_exe()}", file=out)
    print(bar, file=out)

    # ── Python ──
    print("\n[1/3] Python 依赖（pip-audit）", file=out)
    if report["python"].get("skipped"):
        # 刻意把"跳过"和"没装"分开报：混在一起会让人以为环境缺工具，
        # 而实际上是本次调用主动没跑（那正是"配置了但从没执行"的温床）
        print("  [-] 已用 --skip-python 跳过 —— 本次**没有**扫描 Python 依赖", file=out)
    elif not report["python"]["available"]:
        print("  [X] pip-audit 未安装 —— 本次**没有**扫描 Python 依赖", file=out)
        print(f"    安装：{_python_exe()} -m pip install pip-audit", file=out)
    else:
        for scan in report["python"]["scans"]:
            if scan["ok"]:
                print(
                    f"  · {scan['target']}：解析 {scan['resolved_packages']} 个包"
                    f" → 原始记录 {len(scan['findings'])} 条",
                    file=out,
                )
            else:
                print(f"  [X] {scan['target']}：扫描失败 —— {scan['error']}", file=out)
        uniq: dict = {}
        for scan in report["python"]["scans"]:
            for finding in scan["findings"]:
                uniq.setdefault((finding["package"], finding["id"]), finding)
        print(
            f"  汇总：原始 {sum(len(s['findings']) for s in report['python']['scans'])} 条 / "
            f"去重后 {len(uniq)} 条 / 涉及 {len({k[0] for k in uniq})} 个包",
            file=out,
        )
        for (pkg, vid), finding in sorted(uniq.items()):
            aliases = ", ".join(finding["aliases"])
            fix = ", ".join(finding["fix_versions"]) if finding["fix_versions"] else "无"
            print(f"    - {pkg} {finding['installed']}  {vid}  ({aliases})  修复版本: {fix}", file=out)

    # ── Node ──
    print("\n[2/3] Node 依赖（npm audit）", file=out)
    node = report["node"]
    if node.get("skipped"):
        print("  [-] 已用 --skip-node 跳过 —— 本次**没有**扫描 Node 依赖", file=out)
    elif not node["ok"]:
        print(f"  [X] 扫描失败 —— {node['error']}", file=out)
    else:
        if node["registry_fallback"]:
            print("  [!] 项目配置的 registry 未实现 audit 端点（NOT_IMPLEMENTED），已回退官方源", file=out)
        print(f"  · registry: {node['registry']}", file=out)
        deps = node["dependency_counts"]
        print(
            f"  · 依赖总数 {deps.get('total', '?')}（prod {deps.get('prod', '?')} / dev {deps.get('dev', '?')}）",
            file=out,
        )
        print(
            "  · 发现："
            + " / ".join(f"{k} {node['counts'].get(k, 0)}" for k in SEVERITY_ORDER)
            + f"  = 共 {node['total']} 条",
            file=out,
        )
        for finding in sorted(node["findings"], key=lambda f: -_SEVERITY_RANK[f["severity"]]):
            first = finding["advisories"][0]["title"] if finding["advisories"] else "(传递依赖)"
            print(
                f"    - [{finding['severity']:>8}] {finding['package']}  {first}  可修: {finding['fix_available']}",
                file=out,
            )

    # ── Trivy ──
    print("\n[3/3] Trivy（文件系统 / 配置 / 密钥）", file=out)
    trivy = report["trivy"]
    if not trivy["available"]:
        print("  [!] 未安装 —— 本项跳过（**不是**通过）", file=out)
        print("    它补的是：Dockerfile / compose / nginx 配置错误、仓库内硬编码密钥、", file=out)
        print("    以及不经 npm/pip 清单的依赖。镜像扫描在本项目无对象（不跑容器）。", file=out)
    elif not trivy["ok"]:
        print(f"  [X] 执行失败 —— {trivy['error']}", file=out)
    else:
        print(f"  · 可执行文件: {trivy['path']}", file=out)
        if trivy.get("vuln_skipped_reason"):
            # 必须显式说出来：否则下面的"0 条漏洞"会被读成"依赖没问题"
            print("  [!] vuln 扫描**未执行** —— trivy-db 拉取失败，已降级为 config/secret：", file=out)
            print(f"      {trivy['vuln_skipped_reason']}", file=out)
            print("      依赖漏洞结论以本报告上面的 pip-audit / npm audit 为准。", file=out)
        else:
            print("  · 扫描器: vuln + misconfig + secret", file=out)
        print(
            "  · 发现："
            + " / ".join(f"{k} {trivy['counts'].get(k, 0)}" for k in SEVERITY_ORDER)
            + f"  = 共 {len(trivy['findings'])} 条",
            file=out,
        )
        for finding in sorted(trivy["findings"], key=lambda f: -_SEVERITY_RANK[f["severity"]])[:25]:
            print(
                f"    - [{finding['severity']:>8}] {finding['kind']:<9} {finding.get('id')}"
                f"  {finding.get('package')}  ({finding.get('target')})",
                file=out,
            )

    # ── 严重度汇总表 ──
    print("\n" + bar, file=out)
    print("严重度汇总", file=out)
    header = f"  {'来源':<26}" + "".join(f"{k:>9}" for k in SEVERITY_ORDER) + f"{'合计':>7}"
    print(header, file=out)
    print("  " + "-" * (len(header) - 2), file=out)
    for row in report["severity_table"]:
        print(_severity_row(row["source"], row), file=out)
    total = {k: sum(r[k] for r in report["severity_table"]) for k in SEVERITY_ORDER}
    print(_severity_row("合计", total), file=out)
    print(bar, file=out)
    print("说明：pip-audit 的告警源不含 CVSS，故 Python 侧一律计入 unknown，不做猜测。", file=out)
    print("      两边的计数粒度不同（Python 按公告条数，npm 按包），不要相加。", file=out)
    print("      0 条发现 ≠ 没有漏洞 —— 三个工具都只比对公开公告库。", file=out)
    print("      可达性判断（哪些实际上打不到）见 docs/security-scan.md。", file=out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="依赖安全扫描（pip-audit / npm audit / Trivy），见 docs/security-scan.md",
    )
    parser.add_argument(
        "--fail-on",
        choices=["none", "low", "moderate", "high", "critical"],
        default="none",
        help="达到该严重度即退出码 1（默认 none：只报告不阻断）",
    )
    parser.add_argument("--json", action="store_true", help="额外输出机器可读 JSON")
    parser.add_argument(
        "--json-out",
        default=None,
        help="把 JSON 报告写到指定文件（供 CI 归档）",
    )
    parser.add_argument("--skip-python", action="store_true", help="跳过 pip-audit")
    parser.add_argument("--skip-node", action="store_true", help="跳过 npm audit")
    parser.add_argument("--skip-trivy", action="store_true", help="跳过 Trivy")
    return parser


def main() -> int:
    _configure_console()
    args = build_parser().parse_args()
    started = datetime.now().astimezone()

    # ── Python ──
    python_section = {"available": False, "skipped": bool(args.skip_python), "scans": []}
    if not args.skip_python:
        python_section["available"] = pip_audit_available()
        if python_section["available"]:
            for req in (BACKEND / "requirements.txt", BACKEND / "requirements-test.txt"):
                if req.is_file():
                    python_section["scans"].append(scan_python(req))

    # ── Node ──
    node_section = {
        "ok": False,
        "skipped": bool(args.skip_node),
        "error": "已用 --skip-node 跳过",
        "counts": {},
        "findings": [],
        "total": 0,
    }
    if not args.skip_node:
        node_section = scan_node()

    # ── Trivy ──
    trivy_section = {
        "available": False,
        "ok": False,
        "error": None,
        "vuln_skipped_reason": None,
        "counts": {},
        "findings": [],
    }
    if not args.skip_trivy:
        trivy_section = scan_trivy()

    counts_by_source = {}
    if python_section["available"]:
        py_counts: dict = {}
        for scan in python_section["scans"]:
            for finding in scan["findings"]:
                py_counts[finding["severity"]] = py_counts.get(finding["severity"], 0) + 1
        counts_by_source["pip-audit（原始记录）"] = py_counts
    if node_section.get("ok"):
        counts_by_source["npm audit"] = node_section["counts"]
    if trivy_section.get("ok"):
        counts_by_source["trivy fs"] = trivy_section["counts"]

    report = {
        "started_at": started.isoformat(timespec="seconds"),
        "elapsed_s": None,
        "root": str(ROOT),
        "python": python_section,
        "node": node_section,
        "trivy": trivy_section,
        "severity_table": _severity_table(counts_by_source),
    }
    report["elapsed_s"] = round(time.monotonic() - _T0, 1)

    print_report(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON 报告已写入 {args.json_out}")

    # ── 退出码 ──
    # 2 = 必需扫描器没跑起来。这一条是本脚本存在的主要理由：
    # 把"配置了但从未真正执行"从一句易被忽略的"跳过"变成红灯。
    #
    # ⚠️ 这一段必须排在 `--json-out` **之后**：CI 把 JSON 报告当构建产物归档，
    # 而"扫描器没跑起来"恰恰是最需要被人看到的时刻。若在这里提前 return，
    # 归档步骤拿不到文件 —— 报告在最该存在的时候缺失（artifact 上传会因
    # 文件不存在而失败或归档空目录，两种都不是想要的结果）。
    required_failures = []
    if not args.skip_python and not python_section["available"]:
        required_failures.append("pip-audit 未安装")
    if not args.skip_python:
        for scan in python_section["scans"]:
            if not scan["ok"]:
                required_failures.append(f"pip-audit 扫描失败（{scan['target']}）")
    if not args.skip_node and not node_section.get("ok"):
        required_failures.append(f"npm audit 扫描失败（{node_section.get('error')}）")
    if required_failures:
        print("\n退出码 2：必需的扫描器未能运行 → " + "；".join(required_failures), file=sys.stderr)
        return 2

    if args.fail_on != "none":
        threshold = _SEVERITY_RANK[args.fail_on]
        hits = []
        for scan in python_section["scans"]:
            for finding in scan["findings"]:
                if _SEVERITY_RANK[finding["severity"]] >= threshold:
                    hits.append(finding)
        for source in (node_section, trivy_section):
            for finding in source.get("findings") or []:
                if _SEVERITY_RANK[finding["severity"]] >= threshold:
                    hits.append(finding)
        if hits:
            print(f"\n退出码 1：有 {len(hits)} 条发现达到 --fail-on {args.fail_on}", file=sys.stderr)
            return 1
    return 0


_T0 = time.monotonic()

if __name__ == "__main__":
    raise SystemExit(main())
