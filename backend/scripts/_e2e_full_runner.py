"""全链路 E2E（Playwright `e2e-full`）的**单进程监督器**

`playwright.config.ts` 的 `webServer.command` 只接受**一条命令**，而真实链路需要
两个后端进程：uvicorn（API）与 Celery worker（转换/清洗/理解/出题/嵌入全部在
worker 里跑）。本脚本就是"那一条命令"：

    子进程 1  uvicorn --factory scripts._e2e_full_app:create_app   （隔离后的 API）
    子进程 2  python scripts/_e2e_full_worker.py                   （隔离后的 worker）
    本进程    控制端口上的就绪探针（Playwright 的 webServer.url 探它）

## 就绪判定不是"端口开了"

Playwright 只探一个 URL。如果这里在 uvicorn 一监听就返回 200，测试会在 worker
还没订阅 broker 时开始上传 —— 任务投出去没人接，表现为"转换超时"，
与真实原因（worker 没起来）毫无关系。因此这里等两件事：

  1. `GET http://127.0.0.1:<backend>/health` 返回 200；
  2. worker **自己的日志**打印了 Celery 的 ready 行（`celery@... ready.`）——
     这是"已连上 broker 并开始消费"的权威信号。

## 一次性的临时根

数据库、存储（vault）、broker、结果后端、日志、上传暂存全部落在
`<临时根>` 下，`backend/data/**`（真实知识库）一个字节都不写：

    db/engram.db          DATABASE_URL（sqlite+aiosqlite）
    vault/                STORAGE_DIR / VAULT_DIR（原始文件与 Markdown）
    broker/ results/      ENGRAMNOTE_E2E_BROKER_DIR（见 _e2e_full_bootstrap）
    logs/                 LOG_DIR
    tmp/upload/           两阶段上传暂存（见 _e2e_full_bootstrap）

临时根默认为 `%TEMP%/engramnote-e2e-full`，可用 `ENGRAMNOTE_E2E_TMP` 指定；
设置 `ENGRAMNOTE_E2E_KEEP=1` 时跑完不删除（排查失败用）。

## 清理

Playwright 结束时会杀掉本进程。子进程（uvicorn / worker）不保证一起被带走
（Windows 下尤其如此），所以这里有两条兜底：向子进程发 terminate 后等待，
以及一个**父进程消失看门狗**（Playwright 先死 → 本进程也退出并把子进程带走）。

## 明确不做的事

* 不设置 `ENGRAMNOTE_ALLOW_NETWORK_TESTS`：那条守卫只属于 `backend/tests/`，
  与本脚本无关（本脚本跑的是真实服务，本来就要联网调 LLM）。
* 不碰 `LLM_PROVIDER` / `DEEPSEEK_*`：一切按 `backend/.env`（OpenCode 网关）。
  这里只会把它们**打印出来**（不含密钥），便于失败时定位路由。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


#: 后端要求 Python >= 3.10：`app/**` 大量使用 `X | None` 这类 PEP 604 写法，
#: 3.9 上会在 import 阶段就炸（实测报 `unsupported operand type(s) for |`，
#: 而且报错出现在 uvicorn 的 "Error loading ASGI app factory" 里，指向
#: 一个与真实原因无关的地方）。
MIN_PYTHON = (3, 10)

#: 找后端解释器时优先看的环境变量（值 = python.exe 的绝对路径）
ENV_PYTHON = "ENGRAMNOTE_E2E_PYTHON"

_CONDA_ENVS = (
    Path.home() / "anaconda3" / "envs",
    Path.home() / "miniconda3" / "envs",
    Path("C:/ProgramData/anaconda3/envs"),
)

#: 项目的启动脚本（start.bat / start.sh）用的就是这个环境名
PREFERRED_CONDA_ENV = "mineru_env"


def _probe_python(exe: Path | str) -> str | None:
    """返回该解释器能否 import 后端依赖（能则返回版本串，否则 None）"""
    probe = TMP_ROOT / "_probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import sys, fastapi, uvicorn, celery, sqlalchemy, httpx, pydantic_settings, aiosqlite\n"
        "print(sys.version.split()[0])\n",
        encoding="utf-8",
    )
    try:
        done = subprocess.run(
            [str(exe), str(probe)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(BACKEND_ROOT),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return (done.stdout or "").strip().splitlines()[-1] if done.stdout.strip() else None


def resolve_python() -> tuple[Path | str, str]:
    """决定用哪个解释器跑后端/worker

    为什么要找而不是直接用 `sys.executable`：Playwright 由 Node 启动，`python`
    落在 PATH 上的是 **anaconda base（3.9）**，而本项目后端要求 >= 3.10 且依赖装在
    conda 环境 `mineru_env` 里（`start.bat` 用的就是它）。在这里找一次，
    比让 E2E 依赖"你先 activate 对环境"可靠得多 —— 后者会以
    "Error loading ASGI app factory" 这种与真实原因无关的形式失败。
    """
    explicit = os.environ.get(ENV_PYTHON, "").strip()
    if explicit:
        resolved = _probe_python(explicit)
        if not resolved:
            raise SystemExit(
                f"[e2e-full] {ENV_PYTHON}={explicit} 无法 import 后端依赖（或版本 < 3.10）"
            )
        return explicit, resolved

    if sys.version_info >= MIN_PYTHON:
        resolved = _probe_python(sys.executable)
        if resolved:
            return sys.executable, resolved

    candidates: list[Path] = []
    for envs_dir in _CONDA_ENVS:
        preferred = envs_dir / PREFERRED_CONDA_ENV / "python.exe"
        if preferred.is_file():
            candidates.append(preferred)
        if envs_dir.is_dir():
            candidates.extend(sorted(envs_dir.glob("*/python.exe")))
    for name in ("python3.12", "python3.11", "python3.10", "python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved = _probe_python(candidate)
        if resolved:
            return candidate, resolved

    raise SystemExit(
        "[e2e-full] 找不到可用的后端解释器（需要 Python >= 3.10 且已装后端依赖）。\n"
        f"  试过：{[str(c) for c in candidates]}\n"
        f"  可用 {ENV_PYTHON}=<python.exe 绝对路径> 显式指定。"
    )


# ---- 端口与临时根：默认值与 playwright.config.ts 保持一致，全部可覆盖 ----
BACKEND_PORT = _env_int("ENGRAMNOTE_E2E_API_PORT", 4382)
CONTROL_PORT = _env_int("ENGRAMNOTE_E2E_CONTROL_PORT", 4383)
TMP_ROOT = Path(
    os.environ.get("ENGRAMNOTE_E2E_TMP", "").strip()
    or (Path(tempfile.gettempdir()) / "engramnote-e2e-full")
)
KEEP_TMP = os.environ.get("ENGRAMNOTE_E2E_KEEP", "").strip() in ("1", "true", "True")

#: 被显式禁止的端口：3080 是人类手上的 DSH Web GUI
FORBIDDEN_PORTS = {3080}


def _guard_ports() -> None:
    bad = FORBIDDEN_PORTS & {BACKEND_PORT, CONTROL_PORT}
    if bad:
        raise SystemExit(
            f"[e2e-full] 端口 {sorted(bad)} 是人类正在使用的 DSH Web GUI 端口，拒绝占用。"
        )


def _log(message: str) -> None:
    print(f"[e2e-full] {message}", flush=True)


class Child:
    """一个被监督的子进程（保留日志尾部，失败时能说清原因）"""

    def __init__(self, name: str, argv: list[str], env: dict[str, str], cwd: Path) -> None:
        self.name = name
        self.log_path = TMP_ROOT / "logs" / f"{name}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self._tail: list[str] = []
        self.ready = False
        _log(f"启动 {name}: {' '.join(argv)}")
        creationflags = 0
        if os.name == "nt":
            # 独立进程组：父进程被强杀时子进程不会跟着收到 Ctrl 事件，
            # 由本脚本的 terminate 与看门狗负责回收
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        self.proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader = threading.Thread(target=self._pump, name=f"{name}-log", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._log_file.write(line)
            self._log_file.flush()
            stripped = line.rstrip("\n")
            self._tail.append(stripped)
            if len(self._tail) > 40:
                self._tail.pop(0)
            self._observe(stripped)

    def _observe(self, line: str) -> None:
        """子类可覆写：从日志里判定"真的就绪了" """

    @property
    def tail(self) -> str:
        return "\n".join(self._tail[-15:])

    def alive(self) -> bool:
        return self.proc.poll() is None

    def terminate(self, timeout: float = 15.0) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _log(f"{self.name} 未在 {timeout:.0f}s 内退出，强杀")
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._log_file.close()


class Api(Child):
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        super().__init__("api", argv, env, BACKEND_ROOT)


class Worker(Child):
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        super().__init__("worker", argv, env, BACKEND_ROOT)

    def _observe(self, line: str) -> None:
        # Celery 的 ready 横幅：`celery@HOST ready.`
        if " ready." in line or line.endswith("ready."):
            self.ready = True


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 本机固定地址
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _build_env() -> dict[str, str]:
    """子进程环境：临时根重定向 + 一切 LLM 配置原样继承（只增不改）"""
    db_path = TMP_ROOT / "db" / "engram.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    vault = TMP_ROOT / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    logs = TMP_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            # ---- 一次性数据库 / 存储 / 日志（真实库一个字节都不碰）----
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "STORAGE_BACKEND": "local",
            "STORAGE_DIR": str(vault),
            "VAULT_DIR": str(vault),
            "LOG_DIR": str(logs),
            # ---- broker / 结果后端 / 上传暂存的隔离引导（见 _e2e_full_bootstrap）----
            "ENGRAMNOTE_E2E_BROKER_DIR": str(TMP_ROOT / "celery"),
            # ---- 应用姿态：prod + 显式 JWT 密钥（不生成、不泄露、不写盘）----
            "APP_ENV": "prod",
            "JWT_SECRET_KEY": "e2e-full-throwaway-secret-not-used-anywhere-else-0123456789",
            # ---- 前后端同源：浏览器只访问 Vite，由它代理到本后端 ----
            "CORS_ORIGINS": (
                f"http://127.0.0.1:{_env_int('E2E_FULL_PORT', 4381)},"
                f"http://localhost:{_env_int('E2E_FULL_PORT', 4381)}"
            ),
            "PYTHONPATH": str(BACKEND_ROOT) + os.pathsep + str(SCRIPTS_DIR),
            "PYTHONIOENCODING": "utf-8",
            # ---- 不用 SQL 日志：它会打印卡片/题目正文（本文件只做最小姿态设置）----
            "LOG_SQL": "false",
        }
    )
    return env


class ControlHandler(BaseHTTPRequestHandler):
    """控制端口：Playwright 的 webServer.url 探这里"""

    ready = False
    detail: dict[str, object] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口
        if self.path.startswith("/health"):
            code = 200 if ControlHandler.ready else 503
            body = json.dumps(
                {"ready": ControlHandler.ready, **ControlHandler.detail}, ensure_ascii=False
            ).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - 父类接口
        return  # 静默：探针每秒一次，不需要刷屏


def _start_control_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), ControlHandler)
    thread = threading.Thread(target=server.serve_forever, name="control", daemon=True)
    thread.start()
    return server


def _wait_for_ready(api: Api, worker: Worker, child_env: dict[str, str]) -> None:
    health_url = f"http://127.0.0.1:{BACKEND_PORT}/health"
    deadline = time.monotonic() + float(os.environ.get("ENGRAMNOTE_E2E_READY_TIMEOUT", "240"))
    api_ok = False
    while time.monotonic() < deadline:
        if not api.alive():
            raise SystemExit(f"[e2e-full] API 进程已退出（code={api.proc.returncode}）\n{api.tail}")
        if not worker.alive():
            raise SystemExit(
                f"[e2e-full] worker 进程已退出（code={worker.proc.returncode}）\n{worker.tail}"
            )
        if not api_ok and _http_ok(health_url):
            api_ok = True
            _log("API 就绪（/health 200）")
        if api_ok and worker.ready:
            ControlHandler.detail = {
                "api": health_url,
                "worker_ready": True,
                "database": child_env.get("DATABASE_URL", ""),
                "storage": child_env.get("STORAGE_DIR", ""),
            }
            ControlHandler.ready = True
            _log(f"worker 就绪（Celery ready）→ 控制端口 {CONTROL_PORT} 开始返回 200")
            return
        time.sleep(0.5)
    raise SystemExit(
        "[e2e-full] 就绪超时："
        f"api_ok={api_ok} worker_ready={worker.ready}\n"
        f"--- api 日志尾部 ---\n{api.tail}\n--- worker 日志尾部 ---\n{worker.tail}"
    )


def _print_routing(env: dict[str, str]) -> None:
    """打印**生效的** LLM 路由（不含密钥）—— 报告里要证明走的是 OpenCode 网关"""
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        from app.config import get_settings

        cfg = get_settings()
        llm = cfg.get_llm_config()
        key = llm.get("api_key") or ""
        _log(
            "LLM 路由（来自 backend/.env）| provider=%s | base_url=%s | model=%s | key=%s"
            % (
                llm.get("provider"),
                llm.get("base_url"),
                llm.get("model"),
                f"已配置(len={len(key)})" if key else "缺失",
            )
        )
        _log(f"APP_ENV={cfg.app_env} | llm_max_rpm={cfg.llm_max_rpm}")
    except Exception as exc:  # noqa: BLE001
        _log(f"读取 LLM 路由失败（不影响启动）: {exc}")


def main() -> int:
    _guard_ports()

    if TMP_ROOT.exists() and not KEEP_TMP:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    _log(f"临时根: {TMP_ROOT}")

    for port, what in ((BACKEND_PORT, "后端"), (CONTROL_PORT, "控制")):
        if not _port_free(port):
            _log(
                f"端口 {port}（{what}）已被占用 —— 可能是上一次 e2e-full 的进程没退干净。"
                "本脚本不会去杀别人的进程；请手工确认后重试"
                "（或换 E2E_FULL_API_PORT / E2E_FULL_CONTROL_PORT）。"
            )
            return 2

    api_env = _build_env()
    worker_env = dict(api_env)
    worker_env["APP_ENV"] = "prod"

    python_exe, python_version = resolve_python()
    _log(f"后端解释器: {python_exe}（Python {python_version}）")

    api = Api(
        [
            str(python_exe),
            "-m",
            "uvicorn",
            "--factory",
            "scripts._e2e_full_app:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(BACKEND_PORT),
            "--log-level",
            "info",
        ],
        api_env,
    )
    worker = Worker([str(python_exe), str(SCRIPTS_DIR / "_e2e_full_worker.py")], worker_env)

    control = _start_control_server()
    children = [api, worker]

    def shutdown(signum=None, frame=None) -> None:  # noqa: ARG001
        _log(f"收到停止信号 {signum}，正在回收子进程")
        ControlHandler.ready = False
        for child in children:
            child.terminate()
        if not KEEP_TMP:
            shutil.rmtree(TMP_ROOT, ignore_errors=True)
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except (ValueError, OSError):  # pragma: no cover - 非主线程/平台差异
            pass

    # 父进程（Playwright）消失 → 一起退出。Playwright 有时只杀本进程，
    # 留下 uvicorn/worker 会让下一次运行卡在"端口被占用"。
    parent_pid = os.getppid()

    try:
        _wait_for_ready(api, worker, api_env)
        _print_routing(api_env)
        _log("全部就绪，进入监督循环（Ctrl+C / 父进程退出即停止）")
        while True:
            if not api.alive():
                _log(f"API 进程退出（code={api.proc.returncode}）\n{api.tail}")
                return 1
            if not worker.alive():
                _log(f"worker 进程退出（code={worker.proc.returncode}）\n{worker.tail}")
                return 1
            if os.getppid() != parent_pid:
                _log("父进程已退出，正在回收子进程")
                return 0
            time.sleep(0.5)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 0
    finally:
        ControlHandler.ready = False
        for child in children:
            child.terminate()
        control.shutdown()
        if not KEEP_TMP:
            shutil.rmtree(TMP_ROOT, ignore_errors=True)
        _log("已停止")


if __name__ == "__main__":
    raise SystemExit(main())
