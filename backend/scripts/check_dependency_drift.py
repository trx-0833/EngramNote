#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""依赖漂移检查：requirements*.txt 的**声明** vs 本机**实际安装**的版本

================================================================================
为什么这很重要（读这一段就够）
================================================================================
`requirements.txt` 用 `~=`（兼容版本）声明直接依赖，不锁死具体版本。于是存在
**两套并存、且可以差出好几个大版本**的版本集合：

  (A) 声明路径 —— 把 requirements 文件交给 pip 解析（含传递依赖）之后得到的
      集合。`pip-audit -r requirements.txt` 扫描的是它，CI 的
      `pip install -r requirements*.txt` 装的也是它。
  (B) 实际路径 —— 本机 conda 环境与 CI 缓存里**已经装好的**那一套，
      也就是真正在跑的那套代码。

对 (A) 的漏洞结论会被当成对 (B) 的结论用，而两者根本不是同一个环境：

  * "扫描显示没问题" 描述的可能是一个**尚不存在**的部署；
  * 反过来，真正在跑的版本从未被那条扫描路径看过一眼。

这类错位之所以难发现，是因为**两边都不会报错**：声明语法合法、导入成功、
测试全绿。它只在一个地方留下痕迹 —— 版本号本身。本脚本就是把版本号并排
摆出来，除此之外什么都不做。

================================================================================
它**不**做什么
================================================================================
不升级、不降级、不修改任何版本或文件；不联网；不调用 pip；不 import app
（因此不需要 JWT/数据库配置，毫秒级返回）。

================================================================================
用法
================================================================================
    python backend/scripts/check_dependency_drift.py      # 仓库根或 backend/ 下均可
    python backend/scripts/check_dependency_drift.py --json
    python backend/scripts/check_dependency_drift.py --fail-on-drift   # 当门禁用

退出码：

    0  检查跑完了。**漂移存在与否都不影响退出码** —— 今天的漂移是本仓库
       已记录的事实（见 docs/security-scan.md），让这个脚本常红只会得到
       一句 `|| true`，那比没有检查更糟。想当门禁请显式 --fail-on-drift。
    1  --fail-on-drift 且确实存在漂移。
    2  **检查自身什么都没查到**：清单文件缺失、解析出 0 条、
       一个已安装版本都没查到、packaging 不可用。
       这一条是刻意的：一个永远"没发现问题"的检查比没有检查更危险。
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Optional

try:
    from packaging.requirements import Requirement
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version

    PACKAGING_ERROR: Optional[str] = None
except ImportError as exc:  # pragma: no cover - pytest 本身就依赖 packaging
    PACKAGING_ERROR = str(exc)

#: 本文件在 <repo>/backend/scripts/ 下
BACKEND = Path(__file__).resolve().parents[1]

#: 要对比的声明文件（相对 backend/）。顺序即报告顺序。
REQUIREMENT_FILES = ("requirements.txt", "requirements-test.txt")

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_VACUOUS = 2

#: `名字[extras] 版本约束  # 注释` 里的前两段
_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?\s*(.*)$")

#: 判定结果的取值
V_IN_RANGE = "in_range"
V_OUT_OF_RANGE = "out_of_range"
V_UNPINNED = "unpinned"
V_NOT_INSTALLED = "not_installed"
V_UNKNOWN = "unknown"

_VERDICT_LABEL = {
    V_OUT_OF_RANGE: "!! 超出声明范围",
    V_UNKNOWN: "?? 无法判定",
    V_NOT_INSTALLED: "?? 未安装",
    V_IN_RANGE: "OK 在声明范围内",
}


# ---------------------------------------------------------------------------
# 等宽对齐（中文字符占两列，`%-16s` 按字符数补齐会错位）
# ---------------------------------------------------------------------------
def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


# ---------------------------------------------------------------------------
# 解析与查询
# ---------------------------------------------------------------------------
def parse_requirements(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """解析一份 requirements 文件，返回 (声明条目, 未参与对比的行)

    只认"名字[extras] 约束"这种普通行。注释行与空行跳过；`-r` / `-e` /
    其它选项行记进第二项，由调用方汇总（不猜、不静默吞掉）。
    """
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    if not path.exists():
        return entries, skipped

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            skipped.append(f"{path.name}:{lineno} {line}")
            continue
        match = _REQ_RE.match(line)
        if not match:
            skipped.append(f"{path.name}:{lineno} {line}")
            continue
        name, extras, rest = match.group(1), match.group(2) or "", match.group(3).strip()
        entries.append(
            {
                "name": name,
                "extras": [e.strip() for e in extras.split(",") if e.strip()],
                "specifier": rest,
                "source": path.name,
                "line": lineno,
                "raw": line,
            }
        )
    return entries, skipped


def installed_version(name: str) -> Optional[str]:
    """当前解释器里已安装的版本；未安装返回 None

    用 `importlib.metadata` 而不是 `pip list`：无需子进程，也不需要网络。
    """
    import importlib.metadata as md

    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - 元数据损坏时不应让整个检查崩掉
        return None


def classify(specifier: str, installed: Optional[str]) -> str:
    """声明约束是否接纳已安装版本

    `~=X.Y.Z` 按 PEP 440 是 `>=X.Y.Z, ==X.Y.*`（`~=X.Y` 则是 `==X.*`）——
    这正是本仓库"允许补丁浮动"的写法，也正是漂移能被容忍到今天的入口。
    """
    if installed is None:
        return V_NOT_INSTALLED
    if not specifier:
        return V_UNPINNED
    try:
        return V_IN_RANGE if Version(installed) in SpecifierSet(specifier) else V_OUT_OF_RANGE
    except InvalidVersion:
        return V_UNKNOWN


def transitive_dependencies(
    declared: list[dict[str, Any]],
    declared_names: set[str],
    out_of_range_parents: set[str],
) -> list[dict[str, Any]]:
    """清单里没有、但会被带上来的传递依赖（约束取自**已安装**的父包元数据）

    ⚠ 这一段是本次检查里最能说明问题的证据：传递依赖的版本约束**来自父包的
    版本**。父包本身已经与声明不符时，本机/CI 看到的约束就不再是声明路径上的
    那条约束 —— 于是"声明路径全新安装会得到 X"与"本机在跑 Y"必然分叉，
    而分叉点正是 starlette / pydantic-core 这类从不直接写进清单的包。

    extra 的处理：`uvicorn[standard]` 的依赖带 `extra == "standard"` 标记，
    只有被声明请求过的 extra 才真的会被安装；标记在 extra="" 与各声明 extra
    下各求值一次。
    """
    extras_by_name: dict[str, set[str]] = {}
    for entry in declared:
        extras_by_name.setdefault(canonicalize_name(entry["name"]), set()).update(
            entry["extras"]
        )

    collected: dict[str, dict[str, Any]] = {}
    import importlib.metadata as md

    for canonical in sorted(declared_names):
        try:
            dist = md.distribution(canonical)
        except Exception:  # noqa: BLE001 - 未安装的父包没有元数据可读
            continue
        from_out_of_range_parent = canonical in out_of_range_parents
        for raw_req in dist.requires or []:
            try:
                req = Requirement(raw_req)
            except Exception:  # noqa: BLE001 - 元数据里的怪行跳过即可
                continue
            if req.marker is not None:
                extras = [""] + sorted(extras_by_name.get(canonical, set()))
                if not any(req.marker.evaluate({"extra": e}) for e in extras):
                    continue
            dep = canonicalize_name(req.name)
            if dep in declared_names:
                continue
            item = collected.setdefault(
                dep,
                {
                    "name": req.name,
                    "installed": installed_version(req.name),
                    "required_by": [],
                    "constraint_from_out_of_range_parent": False,
                },
            )
            if from_out_of_range_parent:
                item["constraint_from_out_of_range_parent"] = True
            item["required_by"].append(
                {
                    "parent": canonical,
                    "parent_installed": dist.version,
                    "parent_in_declared_range": not from_out_of_range_parent,
                    # 原始约束字符串（空串 = 任意版本）；显示时再加括号
                    "specifier": str(req.specifier),
                }
            )

    for item in collected.values():
        item["version_ok"] = _satisfies_all(item["installed"], item["required_by"])

    return sorted(
        collected.values(),
        key=lambda i: (not i["constraint_from_out_of_range_parent"], i["name"].lower()),
    )


def _satisfies_all(installed: Optional[str], required_by: list[dict[str, Any]]) -> Optional[bool]:
    """已安装版本是否满足所有已安装父包给出的约束（全部父包都没给约束则为 None）"""
    if installed is None:
        return None
    ok: Optional[bool] = None
    for req in required_by:
        spec = req["specifier"]
        if not spec:
            continue  # 父包没写约束
        try:
            satisfied = Version(installed) in SpecifierSet(spec)
        except (InvalidVersion, InvalidSpecifier):
            satisfied = False
        ok = satisfied if ok is None else (ok and satisfied)
    return ok


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------
def analyze(files: Optional[list[Path]] = None) -> dict[str, Any]:
    """跑完整检查，返回报告 dict（**不打印、不退出**，便于测试直接调用）"""
    paths = files if files is not None else [BACKEND / f for f in REQUIREMENT_FILES]

    report: dict[str, Any] = {
        "interpreter": sys.executable,
        "python": platform.python_version(),
        "files": [str(p) for p in paths],
        "entries": [],
        "pinned": [],
        "unpinned": [],
        "transitive": [],
        "skipped_lines": [],
        "problems": [],
        "counts": {},
    }

    if PACKAGING_ERROR:
        report["problems"].append(f"packaging 不可用（无法判定版本范围）: {PACKAGING_ERROR}")
        return report

    declared: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            report["problems"].append(f"声明文件缺失: {path}")
            continue
        entries, skipped = parse_requirements(path)
        declared.extend(entries)
        report["skipped_lines"].extend(skipped)

    for entry in declared:
        entry["installed"] = installed_version(entry["name"])
        entry["verdict"] = classify(entry["specifier"], entry["installed"])
    report["entries"] = declared
    report["pinned"] = [e for e in declared if e["specifier"]]
    report["unpinned"] = [e for e in declared if not e["specifier"]]

    known = [e for e in declared if e["installed"] is not None]
    # ---- 空洞守卫：检查自身"什么都没查到"时必须响，而不是报"无漂移" ----
    if not declared:
        report["problems"].append(
            "没有解析到任何依赖声明 —— 解析逻辑失效或清单为空，"
            "此时报告'无漂移'是假结论"
        )
    if declared and not known:
        report["problems"].append(
            "所有声明条目都查不到已安装版本 —— 元数据查询失效，"
            "此时报告'无漂移'同样是假结论"
        )

    declared_names = {canonicalize_name(e["name"]) for e in declared}
    counts = {
        "declared": len(declared),
        "installed_known": len(known),
        "pinned": len(report["pinned"]),
        "unpinned": len(report["unpinned"]),
        V_IN_RANGE: sum(1 for e in declared if e["verdict"] == V_IN_RANGE),
        V_OUT_OF_RANGE: sum(1 for e in declared if e["verdict"] == V_OUT_OF_RANGE),
        V_NOT_INSTALLED: sum(1 for e in declared if e["verdict"] == V_NOT_INSTALLED),
        V_UNKNOWN: sum(1 for e in declared if e["verdict"] == V_UNKNOWN),
    }
    counts["drift"] = counts[V_OUT_OF_RANGE]
    report["counts"] = counts

    out_of_range_parents = {
        canonicalize_name(e["name"]) for e in declared if e["verdict"] == V_OUT_OF_RANGE
    }
    if declared_names:
        report["transitive"] = transitive_dependencies(
            declared, declared_names, out_of_range_parents
        )
    report["transitive_total"] = len(report["transitive"])
    report["transitive_from_out_of_range_parent"] = sum(
        1 for i in report["transitive"] if i["constraint_from_out_of_range_parent"]
    )
    return report


def exit_code(report: dict[str, Any], *, fail_on_drift: bool = False) -> int:
    """退出码：2 = 检查自身失效；1 = --fail-on-drift 且确有漂移；0 = 其余"""
    if report.get("problems"):
        return EXIT_VACUOUS
    if fail_on_drift and report["counts"].get("drift"):
        return EXIT_DRIFT
    return EXIT_OK


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def print_report(report: dict[str, Any]) -> None:
    bar = "=" * 92
    thin = "-" * 92
    counts = report.get("counts") or {}
    print(bar)
    print("依赖漂移检查：requirements*.txt 的声明  vs  当前解释器实际安装的版本")
    print(bar)
    print("为什么这很重要")
    print("  扫描器（pip-audit -r requirements.txt）与 CI 的全新安装面对的是**声明被解析后**")
    print("  的那套版本；本机与 CI 实际运行的却是**已经装好**的那套。两者可以差出好几个")
    print("  大版本，而两边都不报错 —— 于是'扫描显示没问题'描述的可能是**一个尚不存在的")
    print("  部署**，而真正在跑的版本从未被那条路径看过。本脚本不升级任何东西，只把两套")
    print("  版本号并排摆出来（漂移的处置是另一个决定，不在本脚本内）。")
    print(thin)
    print(f"解释器    : {report['interpreter']}  (Python {report['python']})")
    for path in report["files"]:
        print(f"声明文件  : {path}")

    if report.get("problems"):
        print(thin)
        print("[ABORT] 检查自身没有跑起来（这**不是**'无漂移'）：")
        for problem in report["problems"]:
            print("  -", problem)
        print(bar)
        return

    print(thin)
    print("[1] 声明了版本约束的条目 vs 实际安装（漂移就在这里）")
    print(
        "  "
        + _pad("包", 26)
        + _pad("声明", 18)
        + _pad("实际安装", 16)
        + "判定"
    )
    for entry in sorted(report["pinned"], key=lambda e: e["name"].lower()):
        name = entry["name"] + (f"[{','.join(entry['extras'])}]" if entry["extras"] else "")
        print(
            "  "
            + _pad(name, 26)
            + _pad(entry["specifier"], 18)
            + _pad(entry["installed"] or "—", 16)
            + _VERDICT_LABEL[entry["verdict"]]
        )

    if report["unpinned"]:
        print(thin)
        print("[2] 只写了包名、没有版本约束的条目（CI 走的正是这条路径）")
        print("    `pip install -r` 会装**当时的最新版**，因此这里没有'声明版本'可比 ——")
        print("    这本身就是 CI 与本机之间另一条漂移通道，列出来以免被忽略。")
        for entry in sorted(report["unpinned"], key=lambda e: e["name"].lower()):
            name = entry["name"] + (f"[{','.join(entry['extras'])}]" if entry["extras"] else "")
            print(
                "  "
                + _pad(name, 26)
                + _pad(entry["source"], 22)
                + "已安装 "
                + (entry["installed"] or "—（未安装）")
            )

    print(thin)
    print("[3] 清单里没有、但会被带上来的传递依赖（约束取自**已安装**的父包）")
    print("    父包自己已超出声明范围时（下面带 ⚑ 的行），这里看到的约束**不是**声明路径")
    print("    上的那条约束 —— starlette / pydantic-core 这类包正是从这里开始分叉的。")
    if not report["transitive"]:
        print("  （无）")
    for item in report["transitive"]:
        parents = ", ".join(
            f"{r['parent']} {r['parent_installed']} "
            + (f"({r['specifier']})" if r["specifier"] else "(任意)")
            for r in item["required_by"]
        )
        mark = "⚑ " if item["constraint_from_out_of_range_parent"] else "  "
        note = ""
        if item["version_ok"] is False:
            note = "   ← 连已安装的父包都不接受这个版本"
        print(f"  {mark}{_pad(item['name'], 26)}已安装 {_pad(str(item['installed']), 14)}{parents}{note}")

    if report["skipped_lines"]:
        print(thin)
        print("[4] 未参与对比的行（选项行 / 无法解析）：")
        for line in report["skipped_lines"]:
            print("   ", line)

    print(bar)
    print("汇总")
    print(f"  声明条目                : {counts['declared']}"
          f"（带版本约束 {counts['pinned']} / 未锁版本 {counts['unpinned']}）")
    print(f"  查到已安装版本          : {counts['installed_known']}")
    print(f"  在声明范围内            : {counts[V_IN_RANGE]}")
    print(f"  超出声明范围（漂移）    : {counts[V_OUT_OF_RANGE]}")
    print(f"  未安装                  : {counts[V_NOT_INSTALLED]}")
    print(f"  无法判定                : {counts[V_UNKNOWN]}")
    print(f"  传递依赖条目            : {report['transitive_total']}"
          f"（其中 {report['transitive_from_out_of_range_parent']}"
          f" 条的约束来自已超出声明范围的父包）")
    print(bar)
    print("说明：漂移**存在与否都不影响退出码 0** —— 今天这些差异是本仓库的既有事实")
    print("      （requirements.txt 用 ~= 声明、环境早已更新，两者都不是本脚本能改的）。")
    print("      要把它当门禁：--fail-on-drift（退出码 1）；检查自身失效：退出码 2。")
    print("      漏洞结论请以 docs/security-scan.md 记录的扫描口径为准，并注意它扫的是哪一套。")
    print(bar)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对比 requirements*.txt 的声明版本与本机实际安装版本（只读、不升级）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="存在超出声明范围的版本时退出码 1（默认只报告）",
    )
    parser.add_argument(
        "--requirements",
        action="append",
        default=None,
        help="只检查指定的声明文件（可重复；默认 requirements.txt + requirements-test.txt）",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    args = build_parser().parse_args(argv)
    files = (
        [Path(p) if Path(p).is_absolute() else BACKEND / p for p in args.requirements]
        if args.requirements
        else None
    )
    report = analyze(files)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    code = exit_code(report, fail_on_drift=args.fail_on_drift)
    if code == EXIT_VACUOUS:
        print("\n退出码 2：检查自身没有跑起来（**不是**'无漂移'）", file=sys.stderr)
    elif code == EXIT_DRIFT:
        print(
            f"\n退出码 1：{report['counts']['drift']} 个包的实际安装版本超出声明范围",
            file=sys.stderr,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
