# EngramNote 后端 Dockerfile
# 多阶段构建：安装依赖 → 运行服务
# 注意：mineru 和嵌入模型不在镜像中（过大），通过卷挂载

# ---- 阶段1: 安装依赖 ----
FROM python:3.10-slim AS builder

# 使用清华源加速 pip 安装
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 安装 Python 依赖（排除 mineru 等本地专用包）
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
COPY alembic.ini .
COPY alembic/ ./alembic/

# 创建数据目录
RUN mkdir -p /app/data/db /app/data/storage /app/data/celery /app/data/chroma

# 暴露端口
EXPOSE 8000

# 环境变量默认值
ENV PYTHONUNBUFFERED=1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
