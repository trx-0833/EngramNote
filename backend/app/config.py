"""
EngramNote 配置管理模块

本模块负责从环境变量和 .env 文件中读取所有配置项，为整个应用提供统一的配置访问入口。
使用 pydantic-settings 实现类型安全的配置管理，支持环境变量覆盖和默认值。

主要职责：
- 定义项目目录结构（根目录、数据目录、存储目录、数据库目录）
- 管理数据库连接配置（默认 SQLite，可选 PostgreSQL）
- 管理文件存储配置（默认本地文件系统，可选 MinIO 对象存储）
- 管理 Celery 异步任务队列配置（默认文件系统 broker，可选 Redis）
- 管理 JWT 认证配置
- 管理 DeepSeek AI 和 Mineru API 密钥配置
- 管理文件上传限制配置

设计决策：
- 默认使用 SQLite + 本地文件系统 + 文件系统 broker，实现零外部依赖启动
- 通过 get_settings() 配合 lru_cache 实现单例模式，避免重复解析配置
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache


# 项目根目录（backend/ 的上一级，即 EngramNote/backend/）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 数据存储根目录，所有持久化数据（数据库、文件、Celery 结果）均在此目录下
DATA_DIR = PROJECT_ROOT / "data"
# 用户上传文件的存储目录（本地模式使用）
STORAGE_DIR = DATA_DIR / "storage"
# SQLite 数据库文件目录
DB_DIR = DATA_DIR / "db"


class Settings(BaseSettings):
    """
    应用配置类

    通过 pydantic-settings 从 .env 文件和环境变量中自动加载配置。
    所有字段均有默认值，确保开发环境可零配置启动。

    生产环境务必通过 .env 文件或环境变量覆盖以下关键配置：
    - jwt_secret_key: JWT 签名密钥
    - deepseek_api_key: DeepSeek API 密钥
    - mineru_api_token: Mineru API 令牌
    """

    # ---- 数据库配置 ----
    # 默认使用 SQLite（无需安装 PostgreSQL），留空时自动使用 data/db/engramnote.db
    database_url: str = ""

    # ---- 文件存储配置 ----
    # 存储后端选择："local" 使用本地文件系统，"minio" 使用 MinIO 对象存储
    storage_backend: str = "local"  # "local" 或 "minio"
    # 本地存储目录（兼容旧配置，优先使用 vault_dir），默认为 data/storage
    storage_dir: str = ""
    # Vault 根目录：项目隔离 + 状态旁载的目录结构根（如 ~/MarkdownVault），
    # 为空时默认 data/vault；storage_dir 已配置时以其作为 vault 根
    vault_dir: str = ""

    # ---- MinIO 配置（仅在 storage_backend="minio" 时使用） ----
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    # 原始文件桶：存储用户上传的原始文件
    minio_bucket_original: str = "original-files"
    # Markdown 桶：存储转换后的 Markdown 文件
    minio_bucket_markdown: str = "markdown"
    # 是否使用 HTTPS 连接 MinIO
    minio_secure: bool = False

    # ---- Celery 异步任务配置 ----
    # Celery 后端选择："local" 使用文件系统，"redis" 使用 Redis
    celery_backend: str = "local"  # "local" 或 "redis"
    # Redis 模式下的 broker URL（如 redis://localhost:6379/0）
    celery_broker_url: str = ""
    # Redis 模式下的结果后端 URL
    celery_result_backend: str = ""

    # ---- JWT 认证配置 ----
    # JWT 签名密钥，生产环境务必更换为强随机字符串
    jwt_secret_key: str = "engramnote-dev-secret-change-in-production"
    # JWT 签名算法
    jwt_algorithm: str = "HS256"
    # Token 过期时间（分钟），默认 1440 分钟 = 24 小时
    jwt_expire_minutes: int = 1440

    # ---- DeepSeek AI API 配置 ----
    # DeepSeek API 密钥，用于文本润色和标题生成
    deepseek_api_key: str = ""
    # DeepSeek 模型名称
    deepseek_model: str = "deepseek-v4-flash"
    # DeepSeek API 基础 URL
    deepseek_base_url: str = "https://api.deepseek.com"

    # ---- GLM API 配置 ----
    # 智谱 GLM API 密钥，debug 模式下使用（免费额度）
    glm_api_key: str = ""
    # GLM 模型名称（注意：API 调用时模型名必须全小写）
    glm_model: str = "glm-4.7-flash"
    # GLM API 基础 URL（兼容 OpenAI 格式）
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # ---- Mineru API 配置 ----
    # Mineru API 令牌，用于文档解析转换
    mineru_api_token: str = ""
    # Mineru 服务器 URL
    mineru_server_url: str = "https://mineru.net/api/v4/extract/task"
    # Mineru 解析后端选择："pipeline" 使用本地模型，"vlm-http-client" 使用云端API，"hybrid-http-client" 使用混合模式
    mineru_backend: str = "vlm-http-client"  # "pipeline" 或 "vlm-http-client" 或 "hybrid-http-client"

    # ---- 文件上传限制 ----
    # 最大上传文件大小（MB）
    max_upload_size_mb: int = 500
    # 每用户存储配额（MB），超出后拒绝上传，防止磁盘被写满（0 表示不限制）
    max_storage_per_user_mb: int = 5000
    # 允许的文件扩展名（逗号分隔）
    allowed_extensions: str = ".pdf,.png,.jpg,.jpeg,.docx,.pptx,.xlsx,.mp4,.mp3,.wav,.m4a,.md"

    # ---- AI 清洗管道配置 ----
    # 嵌入模型名称，用于文本向量化（去重检测）
    # 推荐模型：BAAI/bge-m3（多语言，效果好）或 paraphrase-multilingual-MiniLM-L12-v2（更轻量）
    embedding_model: str = "BAAI/bge-m3"
    # 去重相似度阈值（0-1），高于此值视为重复
    similarity_threshold: float = 0.92
    # 文本分块大小（字符数）
    chunk_size: int = 500
    # 分块重叠大小（字符数），避免语义在边界断裂
    chunk_overlap: int = 50
    # Chroma 向量数据库持久化目录（空则默认 data/chroma/）
    chroma_dir: str = ""

    # ---- AI 理解管道配置 ----
    # LLM 最大重试次数
    llm_max_retries: int = 5
    # LLM 重试延迟（秒）
    llm_retry_delay: float = 1.0
    # LLM 每分钟最大请求数 (0 = 不限流)
    llm_max_rpm: int = 10

    # ---- ASR 语音转写配置 ----
    # ASR 模型路径（空则使用默认 modelscope 缓存路径）
    asr_model_path: str = ""
    # ASR 转写语言（空字符串为自动检测，"Chinese" 强制中文）
    asr_language: str = "Chinese"
    # ASR 是否启用标点恢复
    asr_enable_punctuation: bool = True
    # ASR 是否启用标题生成
    asr_enable_title_generation: bool = True
    # ASR 缓存目录（空则使用默认 ~/.cache/asr_converter）
    asr_cache_dir: str = ""
    # Silero VAD 模型本地目录（空则使用 data/models/silero-vad/）
    vad_model_dir: str = ""

    # ---- SMTP 邮件配置（可选，配置后用于复习提醒邮件） ----
    # SMTP 服务器地址（如 smtp.qq.com、smtp.gmail.com），留空则禁用邮件提醒
    smtp_host: str = ""
    # SMTP 服务器端口（587 为 STARTTLS 常用端口，465 为 SSL 常用端口）
    smtp_port: int = 587
    # SMTP 登录用户名（通常为邮箱地址）
    smtp_user: str = ""
    # SMTP 登录密码（部分邮箱需使用授权码而非登录密码）
    smtp_password: str = ""
    # 发件人邮箱地址（留空时使用 smtp_user）
    smtp_from: str = ""
    # 是否启用 STARTTLS 加密传输
    smtp_use_tls: bool = True

    # ---- 复习提醒配置 ----
    # 提醒轮询间隔（秒），Celery 定时任务扫描到期复习的频率
    reminder_poll_interval_seconds: int = 600
    # 免打扰时段开始时间（24 小时制，22 表示 22:00 之后不发送提醒）
    reminder_quiet_hours_start: int = 22
    # 免打扰时段结束时间（24 小时制，8 表示 08:00 之后恢复发送提醒）
    reminder_quiet_hours_end: int = 8
    # 是否启用邮件复习提醒
    email_reminder_enabled: bool = False
    # 每日邮件提醒发送时间（24 小时制，9 表示每天 09:00 发送）
    email_reminder_hour: int = 9

    # ---- 日志配置 ----
    log_level: str = "INFO"
    log_dir: str = ""
    log_max_bytes: int = 10 * 1024 * 1024  # 10MB
    log_backup_count: int = 30

    # ---- 应用基本配置 ----
    app_name: str = "EngramNote"
    # 调试模式，开启后 SQLAlchemy 会输出 SQL 日志
    debug: bool = True

    # pydantic-settings 配置：从 .env 文件加载，忽略多余字段
    # 使用绝对路径确保 Celery worker 等子进程也能正确找到 .env 文件
    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        # "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def get_database_url(self) -> str:
        """
        获取数据库连接 URL

        如果显式配置了 database_url（如 PostgreSQL 连接串），则直接返回；
        否则默认使用 SQLite，数据库文件路径为 data/db/engramnote.db。
        会自动创建数据库目录。

        Returns:
            str: 数据库连接 URL，格式如 "sqlite+aiosqlite:///path/to/db" 或 "postgresql+asyncpg://..."
        """
        if self.database_url:
            return self.database_url
        # 默认使用 SQLite，自动创建数据库目录
        db_path = DB_DIR / "engramnote.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    def get_llm_config(self) -> dict:
        """
        获取当前应使用的 LLM 配置

        debug 模式（settings.debug=True）使用 GLM-4.7-flash（免费，适合开发调试）
        非 debug 模式使用 DeepSeek v4-flash（生产环境，效果更稳定）

        Returns:
            dict: {"api_key": str, "model": str, "base_url": str, "provider": str}
        """
        if self.debug:
            return {
                "api_key": self.glm_api_key,
                "model": self.glm_model,
                "base_url": self.glm_base_url,
                "provider": "glm",
            }
        return {
            "api_key": self.deepseek_api_key,
            "model": self.deepseek_model,
            "base_url": self.deepseek_base_url,
            "provider": "deepseek",
        }

    def get_storage_dir(self) -> Path:
        """
        获取本地存储目录路径

        如果显式配置了 storage_dir，则使用配置值；
        否则默认使用 data/storage 目录。

        Returns:
            Path: 本地存储目录的 Path 对象
        """
        if self.storage_dir:
            return Path(self.storage_dir)
        return STORAGE_DIR

    def get_vault_dir(self) -> Path:
        """
        获取 Vault 根目录路径（项目隔离 + 状态旁载结构的根）

        优先级：vault_dir > storage_dir（旧配置兼容）> 旧默认存储 data/storage。

        注意：空配置时回落 STORAGE_DIR（data/storage）而非 data/vault，
        以兼容存量数据——历史笔记文件均位于 data/storage/markdown/、
        data/storage/original-files/ 等 bucket 子目录下，若默认指向空的
        data/vault 会导致所有旧笔记内容读取不到（前端显示空白）。

        Returns:
            Path: Vault 根目录的 Path 对象
        """
        if self.vault_dir:
            return Path(self.vault_dir)
        if self.storage_dir:
            return Path(self.storage_dir)
        return STORAGE_DIR

    def get_log_dir(self) -> Path:
        """
        获取日志目录路径

        如果显式配置了 log_dir，则使用配置值；
        否则默认使用 data/logs 目录。

        Returns:
            Path: 日志目录的 Path 对象
        """
        if self.log_dir:
            return Path(self.log_dir)
        return DATA_DIR / "logs"

    def get_celery_broker_url(self) -> str:
        """
        获取 Celery broker URL

        当 celery_backend 为 "redis" 且配置了 celery_broker_url 时，使用 Redis；
        否则默认使用文件系统 broker（零依赖模式），broker 数据存储在 data/celery/broker/ 目录。

        Returns:
            str: Celery broker URL，格式如 "redis://localhost:6379/0" 或 "filesystem://"
        """
        if self.celery_backend == "redis" and self.celery_broker_url:
            return self.celery_broker_url
        # 默认使用文件系统 broker，自动创建目录
        broker_dir = DATA_DIR / "celery" / "broker"
        broker_dir.mkdir(parents=True, exist_ok=True)
        return f"filesystem://"

    def get_celery_result_backend(self) -> str:
        """
        获取 Celery 结果后端 URL

        当 celery_backend 为 "redis" 且配置了 celery_result_backend 时，使用 Redis；
        否则默认使用文件系统存储结果，结果存储在 data/celery/results/ 目录。

        Returns:
            str: Celery 结果后端 URL，格式如 "redis://localhost:6379/1" 或 "file:///path/to/results"
        """
        if self.celery_backend == "redis" and self.celery_result_backend:
            return self.celery_result_backend
        result_dir = DATA_DIR / "celery" / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        # Windows 路径需要转为 POSIX 格式（正斜杠），并使用 file:/// 三斜杠前缀
        # 否则 kombu 的 URL 解析器会把反斜杠路径误解析为端口号
        return f"file:///{result_dir.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    """
    获取全局配置单例

    使用 lru_cache 装饰器确保 Settings 只实例化一次，
    后续调用直接返回缓存实例，避免重复解析 .env 文件。

    Returns:
        Settings: 全局配置实例
    """
    return Settings()


if __name__ == '__main__':
    get_settings()
