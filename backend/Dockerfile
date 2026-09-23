# EngramNote 后端 Dockerfile
# ========================
#
# ⚠️ **未验证：本项目的容器化是历史遗留，从未构建或运行过**（本机没有 Docker）。
#    见 `README.md:324-336` 与 `AGENTS.md:18`。
#    CI 里的 `docker-nginx-config` job（`.github/workflows/ci.yml:481-529`）只对
#    `frontend/` 的几个文件做**文本 grep 守卫**，它不校验这个文件，
#    也不代表这条路走得通 —— 要用请先自行验证。
#
# 多阶段构建：安装依赖 → 运行服务
#
# ## 镜像里有什么、没有什么（2026-09-23 逐条核对）
#
# 镜像装的是 `requirements.txt` 的**全部** pip 依赖（下面第 20 行的
# `pip install -r requirements.txt`）。因此要说得准确些：
#   - **库在镜像里**：`sentence-transformers`（`requirements.txt:59`）等；
#   - **模型权重不在镜像里**：BGE-M3 / MinerU / ASR 的权重都下载到
#     `data/models/`（`check_env.py:43` 的 `MODELS_DIR = DATA_DIR / "models"`），
#     运行时靠 `docker-compose.yml` 的 `backend-data` 卷挂进 `/app/data` 才可见；
#   - **MinerU 本地 pipeline 的 pip 依赖不在镜像里**：`pyyaml` / `requests`
#     单独登记在 `requirements-pdf-local.txt:20-21`，只在
#     `MINERU_BACKEND=pipeline` 时才需要；默认走云端 `vlm-http-client`
#     （`requirements-pdf-local.txt:5-7`），只需 `MINERU_API_TOKEN`。
#
# ## 已知缺口（写在这里，免得被当成"已加固"）
#
#   - **没有非 root `USER`**（Trivy DS-0002）
#   - **没有 `HEALTHCHECK`**（Trivy DS-0026）
#   这两条在 `docs/security-scan.md:415-417` 的"本轮没有做的事"里登记为独立批次，
#   今天**都没有做**。`docker-compose.yml` 里的探针是 compose 层的，
#   镜像本身不带。

# ---- 阶段1: 安装依赖 ----
FROM python:3.10-slim AS builder

# 使用清华源加速 pip 安装
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 安装 Python 依赖。
# ⚠️ 这份清单里**没有** MinerU 本地专用包（见文件头：它们在
#    `requirements-pdf-local.txt:20-21`），也**没有** torch / qwen-asr
#    （在 `requirements-asr.txt`）与 chromadb（阶段 2.4 收尾已移除，
#    `requirements.txt:60`）。所以"排除 mineru 等本地专用包"这句描述，
#    在 2026-09-23 核对时**仍然成立** —— 它成立的原因是那些包**从来就不在**
#    `requirements.txt` 里，而不是这里做了什么过滤。
RUN pip install --no-cache-dir -r requirements.txt

# ---- 阶段2: 运行服务 ----
FROM python:3.10-slim

# 安装运行时系统依赖（libgl1-mesa-glx 在 Debian trixie 中已更名为 libgl1）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libgl1 \
       libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 阶段复制已安装的 Python 包
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# 复制应用代码
COPY app/ ./app/

# alembic 是**历史遗留**：启动路径**不调用**它，真实迁移靠
# `app/database.py` 的 `init_db()` + `_migrate_sqlite()`（`requirements.txt:69-72`
# 把这条写在了依赖清单里）。这里保留复制是为了历史对照与将来的重建基线，
# 不是运行依赖 —— 升级口径见 `UPGRADING.md`。
COPY alembic.ini .
COPY alembic/ ./alembic/

# 创建数据目录。
# ⚠️ 这里此前还有 `/app/data/chroma`：那是 Chroma 时代的遗留目录，
#    而 chromadb 已从依赖清单移除（`requirements.txt:60`），
#    向量改存 `chunks` 表（`backend/app/models/chunk.py:5-26`），
#    运行期**没有任何读取方** —— 所以不再创建它。
RUN mkdir -p /app/data/db /app/data/storage /app/data/celery

# 暴露端口
EXPOSE 8000

# 环境变量默认值
ENV PYTHONUNBUFFERED=1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
