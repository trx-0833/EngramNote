# EngramNote

AI 驱动的学习笔记管理与知识库工具 —— 从原始资料到长期记忆的完整学习闭环。

## 功能概览

- **资料摄入**：上传 PDF/图片/Office 文件，自动转换为 Markdown
- **AI 清洗**：规则去噪 + 向量相似度去重，生成干净的学习副本
- **AI 理解**：章节摘要、知识点提取（概念/公式/问答对）、自动出题
- **智能问答**：基于知识库的 RAG 问答
- **间隔重复**：SM-2 算法调度复习，薄弱点优先推送
- **学习报告**：每日报告、7天趋势、薄弱点分析

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端 | FastAPI + SQLAlchemy (async) + SQLite |
| 异步任务 | Celery + 文件系统 broker |
| AI | DeepSeek/GLM API + BGE-M3 嵌入模型 |
| 文档解析 | mineru_plus (PDF → Markdown) |
| 向量存储 | Chroma |
| 前端 | React 18 + TypeScript + Vite |
| 容器化 | Docker + Docker Compose + Nginx |

## 快速开始

### 环境要求

- Python 3.10+（推荐 conda 环境 `mineru_env`）
- Node.js 18+
- Git

### 安装

```bash
# 1. 克隆项目
git clone <repo-url>
cd EngramNote

# 2. 后端：创建 conda 环境并安装依赖
conda create -n mineru_env python=3.10
conda activate mineru_env
pip install -r backend/requirements.txt

# 3. 前端：安装依赖
cd frontend
npm install
```

### 配置

在 `backend/` 目录下创建 `.env` 文件：

```env
# 必填：AI API 密钥
DEEPSEEK_API_KEY=your_deepseek_api_key
GLM_API_KEY=your_glm_api_key

# 可选：JWT 密钥（生产环境务必更换）
JWT_SECRET_KEY=your-secret-key
```

### 启动

**方式一：一键启动脚本（推荐）**

Windows:
```bash
start.bat
```

Linux/Mac:
```bash
chmod +x start.sh
./start.sh
```

**方式二：手动启动**

```bash
# 终端1：后端
cd backend
C:\Users\admin\anaconda3\envs\mineru_env\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload-dir app

# 终端2：Celery worker
cd backend
C:\Users\admin\anaconda3\envs\mineru_env\python.exe -m celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q celery

# 终端3：前端
cd frontend
npm run dev
```

### 访问

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

## Docker 部署

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

Docker 配置使用国内镜像源：
- pip: 清华源 `https://pypi.tuna.tsinghua.edu.cn/simple`
- npm: 淘宝源 `https://registry.npmmirror.com`

> 注意：Docker 镜像不包含 mineru 和嵌入模型（文件过大），生产环境需挂载本地模型目录。

## 项目结构

```
EngramNote/
├── backend/                 # 后端 (FastAPI)
│   ├── app/
│   │   ├── api/             # API 路由
│   │   ├── models/          # 数据模型
│   │   ├── schemas/         # Pydantic Schema
│   │   ├── services/        # 业务逻辑
│   │   ├── tasks/           # Celery 异步任务
│   │   ├── middleware/      # 中间件
│   │   ├── config.py        # 配置管理
│   │   ├── database.py      # 数据库连接
│   │   └── main.py          # 应用入口
│   ├── tests/               # 测试
│   ├── Dockerfile           # 后端 Docker 镜像
│   └── requirements.txt     # Python 依赖
├── frontend/                # 前端 (React + TypeScript + Vite)
│   ├── src/
│   │   ├── api/             # API 请求封装
│   │   ├── components/      # 通用组件
│   │   ├── contexts/        # React Context
│   │   ├── pages/           # 页面组件
│   │   ├── utils/           # 工具函数
│   │   └── styles/          # 样式
│   ├── Dockerfile           # 前端 Docker 镜像
│   ├── nginx.conf           # Nginx 配置
│   └── package.json         # Node.js 依赖
├── docker-compose.yml       # Docker Compose 配置
├── start.bat                # Windows 一键启动
├── start.sh                 # Linux/Mac 一键启动
├── .gitignore               # Git 忽略规则
├── 开发时间表.md             # 12周开发记录
├── 项目架构.md               # 架构设计文档
└── 新手教学.md               # 新手完全教学
```

## 核心流程

1. **上传** → PDF 自动转换为 Markdown
2. **清洗** → 去噪去重，生成干净副本
3. **理解** → AI 提取知识点、生成题目
4. **复习** → SM-2 间隔重复，薄弱点优先
5. **报告** → 每日学习统计与趋势分析

## 文档

- [开发时间表](开发时间表.md) — 12周完整开发记录
- [项目架构](项目架构.md) — 架构设计与技术决策
- [新手教学](新手教学.md) — 面向初学者的代码讲解
