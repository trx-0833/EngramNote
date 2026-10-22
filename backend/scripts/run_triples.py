# -*- coding: utf-8 -*-
"""P2：**逐篇跑完整三元组**（v1+v2+v3 同一篇连着跑）

## 为什么不是「版本在外层」

驱动器的循环是 `for version: for note:` —— 先跑完所有 v1，再跑 v2。
在时间受限的窗口里这很糟：中途没时间就只剩**半份 v2**，什么都结论不了。

改成**逐篇**：每完成一篇就多一组**完整**的三版对照。中途停下时，
已完成的部分**每一篇都是可用的**。

## 顺序（省时间的排序）

1. **已跑过 v1 但缺 v2/v3 的笔记** —— 理解命中缓存，只需重出题，最便宜；
2. 其余笔记 —— 理解要重跑（贵），按卡片数升序（小的先做，多完成几篇）。

## 用法

    python scripts/run_triples.py --tmp data/_uxwalk --minutes 90
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
RERUN = BACKEND / "scripts" / "rerun_understanding.py"
SNAP = BACKEND / "data" / "_uxwalk" / "rerun"


def load_done() -> dict:
    """`{note_id: {version, ...}}` —— 从快照读已完成的轮次"""
    done: dict = {}
    for v in ("1", "2", "3"):
        f = SNAP / f"snapshot-v{v}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text(encoding="utf-8")).get("runs", []):
            if "error" in r:
                continue
            done.setdefault(r["note_id"], set()).add(str(r.get("version")))
    return done


def load_notes() -> list[dict]:
    import sqlite3

    db = BACKEND / "data" / "_uxwalk" / "db" / "engram.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT n.id, n.title,
                      (SELECT count(*) FROM knowledge_cards k WHERE k.note_id = n.id) AS cards
               FROM notes n WHERE n.trashed_at IS NULL ORDER BY cards ASC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _kill_leftover_stacks() -> None:
    """兜底清理：杀掉命令行里带 `_e2e_full` 的 python 进程

    ⚠️ 必须兜底：`rerun_understanding.py` 的 `finally` 只 terminate 监督器，
    子进程在 Windows 下不会被带走（`_e2e_full_runner.py` 的 docstring 警告过）。
    第一版就是因此让端口一直被占、25 篇全部静默失败。
    """
    import subprocess as _sp

    try:
        out = _sp.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match '_e2e_full' } | "
                "ForEach-Object { $_.ProcessId }",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        for line in out.stdout.split():
            if line.strip().isdigit():
                _sp.run(["taskkill", "/F", "/PID", line.strip()], capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        print(f"[triples] 兜底清理失败（不致命）：{e}", flush=True)


def _start_stack(tmp: str) -> "subprocess.Popen":
    """起一次隔离栈并等就绪"""
    import os
    import socket
    import time as _t

    env = dict(os.environ)
    env.update(
        {
            "ENGRAMNOTE_E2E_TMP": str(BACKEND / tmp),
            "ENGRAMNOTE_E2E_KEEP": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, str(BACKEND / "scripts" / "_e2e_full_runner.py")],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("[triples] 已起隔离栈，等控制口 4383…", flush=True)
    deadline = _t.monotonic() + 300
    while _t.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", 4383)) == 0:
                print("[triples] 栈就绪", flush=True)
                return proc
        if proc.poll() is not None:
            raise SystemExit("[triples] 监督器提前退出 —— 端口可能被占，先清理")
        _t.sleep(2)
    proc.terminate()
    raise SystemExit("[triples] 等栈就绪超时")


def main() -> int:
    ap = argparse.ArgumentParser(description="逐篇跑完整三版对照")
    ap.add_argument("--tmp", default="data/_uxwalk")
    ap.add_argument("--minutes", type=float, default=90, help="时间预算（分钟）")
    args = ap.parse_args()

    deadline = time.monotonic() + args.minutes * 60
    done = load_done()
    notes = load_notes()

    # 排序：先补「已跑过 v1 但缺 v2/v3」的（理解命中缓存，最便宜）
    def priority(n: dict) -> tuple:
        have = done.get(n["id"], set())
        cheap = 0 if ("1" in have and not {"2", "3"} <= have) else 1
        return (cheap, n["cards"])

    pending = [n for n in notes if not {"1", "2", "3"} <= done.get(n["id"], set())]
    pending.sort(key=priority)

    print(f"[triples] 待做 {len(pending)} 篇，预算 {args.minutes:.0f} 分钟")
    for n in pending[:12]:
        have = sorted(done.get(n["id"], set()))
        print(f"   {n['cards']:4d} 卡  已有 v{','.join(have) or '—':6s} {n['title'][:34]}")

    completed = 0
    failed: list = []
    stack = None
    try:
        stack = _start_stack(args.tmp)
    except SystemExit as e:
        print(f"[triples] {e}", flush=True)
        return 1
    for i, n in enumerate(pending, 1):
        if time.monotonic() >= deadline:
            print(f"[triples] 时间到，停在 {i-1}/{len(pending)}")
            break
        have = done.get(n["id"], set())
        need = [v for v in ("1", "2", "3") if v not in have]
        if not need:
            continue
        remain = (deadline - time.monotonic()) / 60
        print(
            f"[triples] {i}/{len(pending)} 剩 {remain:.0f} 分钟 | {n['title'][:30]} "
            f"（{n['cards']} 卡）补 v{','.join(need)}",
            flush=True,
        )
        t0 = time.monotonic()
        # ★ `--no-stack`：栈由本脚本起一次（每篇各起一次会让第二篇起端口被占）
        rc = subprocess.run(
            [
                sys.executable,
                str(RERUN),
                "--tmp",
                args.tmp,
                "--note-id",
                n["id"],
                "--versions",
                ",".join(need),
                "--no-stack",
            ],
            cwd=str(BACKEND),
            check=False,
        ).returncode
        took = (time.monotonic() - t0) / 60
        # ★★ 校验"真的产出了"：只看返回码不够 —— 第一版就是每篇 0.1 分钟
        #    却报"完成"。所以比对快照里该 note 的版本集合是否**真的增加了**。
        after = load_done().get(n["id"], set())
        gained = sorted(after - have)
        if rc != 0 or not gained:
            failed.append((n["title"], need, rc, round(took, 1)))
            print(
                f"[triples]   ✘ 未产出（rc={rc}，{took:.1f} 分钟，"
                f"版本集合没变）—— 记入失败清单",
                flush=True,
            )
            continue
        print(f"[triples]   ✔ 新增 v{','.join(gained)}，用时 {took:.1f} 分钟", flush=True)
        completed += 1

    if stack is not None:
        print("[triples] 停栈…", flush=True)
        stack.terminate()
        try:
            stack.wait(timeout=20)
        except subprocess.TimeoutExpired:
            stack.kill()
    _kill_leftover_stacks()

    print(f"[triples] 完成 {completed} 篇，失败 {len(failed)} 篇")
    if failed:
        print("[triples] ★ 失败清单（这些**没有**产出，不要当成完成）：")
        for title, need, rc, took in failed:
            print(f"    {title[:34]}  需补 v{','.join(need)}  rc={rc}  {took} 分钟")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
