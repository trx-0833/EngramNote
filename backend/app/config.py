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

import logging
import secrets
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


# 应用根目录（backend/ 本身；注意**不是**仓库根）
# 仓库根 = PROJECT_ROOT.parent，备份目录 _backup/ 放在那里
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 数据存储根目录，所有持久化数据（数据库、文件、Celery 结果）均在此目录下
DATA_DIR = PROJECT_ROOT / "data"
# 用户上传文件的存储目录（本地模式使用）
STORAGE_DIR = DATA_DIR / "storage"
# SQLite 数据库文件目录
DB_DIR = DATA_DIR / "db"
# 两阶段上传的临时文件目录：data/tmp/upload/{uuid}/{原文件名}
# data/ 已被 .gitignore 忽略；由 commit 后清理逻辑与启动时超时清理兜底
TMP_UPLOAD_DIR = DATA_DIR / "tmp" / "upload"
# 开发模式自动生成 JWT 密钥的持久化文件（debug=True 且未配置时写入并复用）
JWT_SECRET_FILE = DATA_DIR / ".jwt-secret"

logger = logging.getLogger(__name__)


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
    # JWT 签名密钥，生产环境务必更换为强随机字符串（生成：python -c "import secrets; print(secrets.token_hex(32))"）
    # 默认空字符串：开发模式（debug=True）自动生成并持久化到 data/.jwt-secret；
    # 生产模式（debug=False）为空时启动即报错，见 docs/decisions.md#F-21a
    jwt_secret_key: str = ""
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
    # 推荐模型：BAAI/bge-m3（多语言，效果好）或 BAAI/bge-small-zh-v1.5（中文轻量，~95MB）
    embedding_model: str = "BAAI/bge-m3"
    # 内存不足时的降级模型（更轻量）：BAAI/bge-small-zh-v1.5（中文，512 维，~95MB）
    # 或 paraphrase-multilingual-MiniLM-L12-v2（多语言，384 维，~470MB，128 token 截断）
    embedding_model_fallback: str = "BAAI/bge-small-zh-v1.5"
    # 空闲内存低于此值（GB）时跳过主模型直接加载降级模型，避免 OOM 崩溃
    # bge-m3 加载峰值约 2×2.3GB，建议阈值 4.0；bge-small-zh-v1.5 仅需约 0.5GB
    embedding_min_free_memory_gb: float = 4.0
    # 嵌入编码批大小，控制编码时的激活内存峰值（CPU 上建议 16）
    embedding_batch_size: int = 16
    # 去重相似度阈值（0-1），高于此值视为重复
    similarity_threshold: float = 0.92
    # 向量检索的相似度地板（余弦，0-1）。低于此值的召回块不进入 RAG 上下文。
    # 作用：阻断"只要召回到就写进 prompt"这一幻觉燃料 —— 检索通道原先
    # 都没有任何下限（BM25 是 score > 0，向量通道是全部返回），
    # 使 top_k 必被填满，模型于是拿着无关内容作答并照样返回引用来源。
    # 说明：计算方式由 Chroma 的 L2 平方距离换算为余弦（单位向量下 cos = 1 - d/2），
    # 因此该阈值与模型无关（两个候选模型都输出单位向量），换模型无需重新校准。
    # 0 表示不过滤（保持旧行为）。
    #
    # 注：另一路 n-gram 检索通道已删除（阶段 2.6）。
    vector_similarity_floor: float = 0.35
    # 文本分块大小（字符数）
    chunk_size: int = 500
    # 分块重叠大小（字符数）。
    #
    # ⚠️ **当前不生效**（阶段 2.1 接线后）。它原先只被
    # `cleaning_service.split_into_chunks` 读取，而该函数已被
    # `markdown_segmenter.segment_with_offsets` 取代 —— 统一后的分块器
    # 只有**块级**重叠（`overlap_blocks`），没有字符级重叠。
    #
    # 保留该配置项而不是删掉，是因为"检索 chunk 之间应该有重叠"这个判断
    # 仍然成立，只是实现方式待定。**在同一篇文档上重块级 vs 字符级重叠
    # 哪个更好，必须先有评测依据**（`scripts/eval_retrieval.py`，阶段 2.9），
    # 否则就是又一次凭直觉调参。在给出依据之前，保持"不重叠"这个可预期行为。
    # 详见 docs/overhaul-plan.md 附录 M.4。
    chunk_overlap: int = 50

    # ---- 混合检索融合配置（阶段 2.6 遗留项，2026-09-11 实测定参）----
    #
    # 背景：原实现照搬 RRF 的默认 k=60 且等权，实测**比 BM25 单通道还差**
    # （严格 Recall@5：等权 k=60 = 57.37%，BM25 单通道 = 59.74%）。
    # 原因：k 越大名次差异被压得越平（k=60 时第 1 名 1/61 与第 5 名 1/65
    # 只差 4%），于是分数主要由"是否两路同时出现"决定 —— RRF 退化为
    # 奖励**共识**而非相关性；两路强弱悬殊时等于把强通道拉向弱通道。
    #
    # 取值来源：在 1058 条真实评测集 + 608 个 chunk 语料上扫参，
    # k=1 / w=0.65 得到严格 Recall@5 **60.87%**（相对原实现 +3.5 个百分点，
    # 且高于任一单通道）。这是一个**稳健区域**而非尖点：
    # k∈[1,10]、w∈[0.6,0.8] 的组合全部在 60% 以上。
    #
    # ⚠️ 这两个值**绑定两路当前的相对强弱**（BM25 59.74% > 向量 48.39%）。
    # 换嵌入模型、换语料或分块策略后**必须重扫**，否则会反过来压制
    # 变强的那一路。重扫方式见 docs/overhaul-plan.md 附录 Q。
    rag_rrf_k: int = 1
    rag_rrf_bm25_weight: float = 0.65
    # 每路送入融合的候选数。融合取 top-5，但候选池要大于 5 ——
    # 池=5 时融合只能在那 10 条里排，答错就出局；池=20 给排序留出余地。
    # 实测：池=5 → 60.18%，池=20 → 60.87%（同参数下）。
    rag_candidate_pool: int = 20

    # ── 已移除：`chroma_dir`（阶段 2.4 收尾，2026-09-11）──
    #
    # 原先指向 Chroma 持久化目录（`data/chroma/`）。检索与去重都已不再使用
    # Chroma：向量存进 `chunks` 表（2.2′），词法检索改用 FTS5（2.5′），
    # 清洗去重改为直接消费已算好的 embeddings。
    #
    # 若外部 `.env` 里仍写着 `CHROMA_DIR=...`，它现在会被 Settings 忽略
    # （pydantic 默认忽略未知键）—— 不会报错，但也不再有任何作用。
    # 磁盘上的 `data/chroma/`（54MB）已是历史数据，可删除；
    # 删除前建议保留 `_backup/20260911-125750-pre-reembed` 那份副本。

    # ---- AI 理解管道配置 ----
    # LLM 最大重试次数
    llm_max_retries: int = 5
    # LLM 重试延迟（秒）
    llm_retry_delay: float = 1.0
    # LLM 每分钟最大请求数 (0 = 不限流)
    #
    # ⚠️ 这是**进程级总闸门**，也是阶段 4.4 之前**唯一**的闸门：
    # 它同时充当"总速率"和"单人速率"，于是一个用户占满桶时其他人只能排队。
    # 4.4 之后它仍是总量保护，单用户公平性由下面两项负责。
    llm_max_rpm: int = 10
    # 单个用户每分钟最大请求数（0 = 不限；阶段 4.4）
    #
    # 默认 **0（不限）** 是刻意的：本项目实际使用人数很少，且"默认收紧"
    # 会悄悄改变既有行为（把一个用户可用的速率砍半）而不给出任何提示。
    # 需要公平性时显式打开，建议值：总限额的一半左右（如 `LLM_USER_MAX_RPM=5`）。
    # 无用户上下文的调用（后台脚本、未接线的路径）共用一个 `__anonymous__` 桶，
    # 而不是"免检" —— 否则"没接上下文"就成了绕过限流的办法。
    llm_user_max_rpm: int = 0
    # 单个供应商（provider）每分钟最大请求数（0 = 不限；阶段 4.4）
    #
    # 与用户桶独立：一个桶按"谁在问"限，另一个按"问的是谁"限。
    # 主要用于多供应商/多密钥时避免单条链路被 429，默认同样不启用。
    llm_provider_max_rpm: int = 0
    # LLM HTTP 请求超时（秒）（共享客户端使用，见 docs/decisions.md#F-05）
    # 实测：reasoning 模型生成 8K 输出 token 约需 195s，120s 会在模型返回前就超时，
    # 比 JSON 截断更早触发失败，故提高默认值，见 docs/decisions.md#F-33
    llm_timeout_seconds: float = 600.0
    # LLM 结构化输出（JSON 场景）单次生成上限（token）。
    # 网关按 max_tokens 精确截断（finish_reason=length 实测验证），该值需明显大于
    # 实际 JSON 体量（实测多数截断发生在 8192，故默认 16384；截断重试时按 2 倍逐级放大）。
    # 不建议设到 200000：超出模型硬上限的部分无效，且会放大超时/成本风险，见 docs/decisions.md#F-33。
    llm_json_max_tokens: int = 16384
    # 截断重试时单次 max_tokens 的放大上限（防止极端值拖死请求）
    llm_json_max_tokens_ceiling: int = 32768

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

    # ---- LLM 成本记账（阶段 4.2）----
    #
    # ## 为什么价格是配置项而不是内置常量
    #
    # 单价随供应商、模型、时期变化，本项目**不内置价格表** ——
    # 内置的那份数字迟早会变成错误的事实，而错误的价格比"不知道价格"更糟：
    # 它会被人当真。
    #
    # 两项都留空（默认）时，`llm_calls.cost` 记 **NULL**，不是 0。
    # 这个区别是刻意的：0 会让"没配价格"与"完全免费"在报表上长得一模一样。
    #
    # 单位：**每 100 万 token 的价格**（与供应商报价单的口径一致，
    # 避免"每 1K"这种容易点错三位小数的小单位）。
    llm_price_input_per_1m: float = 0.0
    llm_price_output_per_1m: float = 0.0
    llm_price_currency: str = "CNY"

    # LLM 调用记账的保留期（天）。0 表示永不清理。
    #
    # 每次调用一行，量级与调用次数同阶（不是与笔记数同阶），
    # 单用户一年通常也就几万行 —— 保留期主要是防止长期运行后无限增长。
    llm_call_retention_days: int = 365

    # ---- LLM 配额（阶段 4.3）----
    #
    # **默认 0 = 不限**。理由：一个默认打开的额度上限会在用户毫无预期时
    # 中断他正在做的事（导入一篇长文档、批量重跑理解），而"被自己的工具
    # 拦住"是最难排查的一类故障。要先能算账，才谈得上有意地设限。
    #
    # 两项都是**按用户、按业务日**（北京时间自然日，与日界一致）计算：
    # 每个用户各自有一份额度，而不是全站共享。
    #
    # ⚠️ `llm_daily_cost_quota` 需要同时配置单价（见上面的
    # `llm_price_*`）才能真正生效；只配上限不配单价时，这个上限
    # **无法执行**，系统会打一次 WARNING 并只按 token 配额拦截 ——
    # 而不是假装它在生效。
    llm_daily_token_quota: int = 0
    llm_daily_cost_quota: float = 0.0

    # ---- 卡片入库门（阶段 4.8 / 4.9）----
    #
    # 判据与设计理由见 `services/card_intake_service.py` 的模块说明。
    #
    # ⚠️ 真库实测（2026-09-11）：这些阈值目前**不会拦下任何存量卡片**
    # （1183 张里只有 4 张正文 < 10 字、2 张缺 source_text，且跨天重跑
    # 理解的笔记数为 0）。它们是**预防性**的 —— 价值在于
    # "谁都可以按那个按钮，而按下去不会把卡片翻一倍、不会灌进脏卡片"。
    #
    # 正文最少字数（规范化后）
    card_min_content_chars: int = 10
    # 标题最少字数
    card_min_title_chars: int = 2
    # 是否要求有原文出处。关闭后"无 source_text 的卡片"也会入库 ——
    # 代价是引用回跳（阶段 2.7 / 3.13）在那些卡上不可用。
    card_require_source_text: bool = True
    # 单次理解最多新建多少张卡（0 = 不限）。
    # 只约束**新建**：复用已有卡片不计入，所以重跑理解不会被它挡住。
    card_max_new_per_run: int = 500

    # ---- LLM 响应缓存（阶段 4.7）----
    #
    # 命中判定是**逐字节相同**的输入（模型 + 端点 + messages + 采样参数），
    # 因此正确性不依赖"相似度"，只依赖调用方是否稳定地构造相同输入。
    # 理解管道满足这一点（提示词模板 + 资料原文，两次重跑逐字节相同）。
    #
    # ## 为什么默认**开**
    #
    # 与语义判分（3.5）、配额（4.3）不同：那两项默认关，是因为它们会改变
    # 用户看到的行为、且出错时难以察觉。缓存不改变"同一输入得到什么"——
    # 它只是把已经付过费的那份答案再取出来一次。
    # 而"重跑理解要再付一次全款"是实打实的浪费，默认关等于这个功能白做。
    #
    # ## 残余风险（无法消除，只能缓解）
    #
    # key 里有模型名，但**供应商可以在同一模型名后换权重**，那时缓存会继续
    # 返回旧模型的输出，而日志上看不出异常。`llm_cache_ttl_days` 是唯一兜底。
    llm_cache_enabled: bool = True
    # 缓存有效期（天）；0 = 不过期（不推荐，理由见上）
    llm_cache_ttl_days: int = 30

    # ---- 复习配置 ----
    # 每日最大答题数（前后端单一来源，经 /review/stats 下发给前端，见 docs/decisions.md#F-12）
    daily_review_limit: int = 10

    # 调度算法（阶段 3.6）。'fsrs' = FSRS-5（默认）；'sm2' = 旧算法（回退开关）
    #
    # 为什么保留 sm2：换调度算法会改变**每个用户**的复习节奏，而节奏错了
    # 用户要过几天才察觉（卡片迟迟不再出现 / 间隔突然暴涨）。留一个配置项
    # 就能在不回滚版本的前提下退回已验证多年的旧行为。
    # 取其他值时按 fsrs 处理并打 WARNING，不静默。
    review_scheduler: str = "fsrs"

    # FSRS 的目标保持率：希望"复习时还能想起来"的概率。
    #
    # 0.9 与 Anki 默认一致，同时也是 FSRS 里 S 的定义点（R(S,S)=0.9），
    # 因此这个值下解出的间隔恰好等于 S —— 便于人工核对实现有没有写错。
    # 调高 → 复习更频繁、记得更牢；调低 → 复习量下降、遗忘变多。
    # 这是**产品权衡**，不是算法常数，所以做成配置。
    fsrs_request_retention: float = 0.9

    # 间隔上限（天）。10 年。
    #
    # FSRS 的 S 会随成功复习持续增长（答对一张已很牢的卡，S 还能再翻几倍），
    # 而 Anki 的默认上限是 36500 天。这里取 10 年：远超任何真实使用周期，
    # 只作为数值护栏，避免写出"22 年后复习这张卡"这种对用户无意义的排期。
    fsrs_max_interval_days: int = 3650

    # 间隔抖动比例（阶段 3.7）。0 表示关闭。
    #
    # 同一次导入的卡片初始状态完全相同，因此此后永远在同一天到期 ——
    # 一篇笔记 20 张卡就会让用户反复经历"0 张"与"20 张"。抖动把同批卡片
    # 摊开到几天里，而**不改变任何一张卡的平均间隔**。
    #
    # 抖动幅度 = max(1, round(间隔 * 本值))，且间隔 < 3 天时不抖
    # （1 天粒度下的最小抖动是 ±1 天 = ±33%，那不是摊负载而是改写节奏）。
    # 详见 fsrs_service.FUZZ_RATIO 的说明。
    review_fuzz_ratio: float = 0.05

    # 到期时刻锚定的整点（业务时区 Asia/Shanghai，0-23；**负值 = 关闭对齐**）
    #
    # ## 为什么需要它
    #
    # 改造前 `next_review_at = now + interval` 天，到期时刻等于"上次复习的
    # 钟点"：今晚 23:40 复习的卡，下次就在 23:40 到期。更要命的是漏一天 ——
    # 用户习惯早上 08:00 复习而卡片 09:00 到期，今天看不到、明天才出现，
    # 间隔凭空多一天。
    #
    # 锚到凌晨 4 点（与 Anki 的 rollover hour 同源）之后，"今天该不该复习"
    # 与用户的日历一致：无论几点开始学习，当天到期的卡都已到期。
    #
    # 留负值开关是因为它改变"今天"的判断边界，万一与作息冲突要能一键退回。
    review_due_hour: int = 4

    # ---- 备份配置 ----
    # 定时备份（Celery Beat 每日 03:30）的快照保留份数；0 表示不清理。
    # 默认 14 份 ≈ 两周，足够覆盖"改坏了过几天才发现"的情况。
    backup_keep: int = 14

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
    # 应用对外访问基础 URL（如渲染邮件提醒中的跳转链接），按实际部署域名配置
    app_base_url: str = "http://localhost:5173"
    # CORS 允许来源（逗号分隔），默认本地前端开发服务器端口；生产环境改为实际前端域名
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    # 调试模式。**默认 False**（见 docs/overhaul-plan.md §2.5 E-5）：
    # 该开关同时控制三件事 —— SQL echo 日志、FastAPI debug 响应、LLM 供应商
    # （debug=True 走 GLM，False 走 DeepSeek）。原先默认 True 的后果是：
    # SQLAlchemy 把**含 bcrypt 哈希与全部知识卡片/题目正文的 SQL 明文**
    # 写进 data/logs/*.log，且任何逃出 ErrorHandlerMiddleware 的异常会回吐 traceback。
    # 开发环境请在 backend/.env 显式设置 DEBUG=true。
    debug: bool = False

    # pydantic-settings 配置：从 .env 文件加载，忽略多余字段
    # 使用绝对路径确保 Celery worker 等子进程也能正确找到 .env 文件
    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        # "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """
        生产/开发模式 JWT 密钥策略

        生产环境（debug=False）必须显式配置 JWT 签名密钥，
        防止使用可预测/空密钥导致 Token 可被伪造。
        开发模式（debug=True）允许空密钥零配置启动，但空密钥不再用于签发：
        此时自动生成随机密钥并持久化到 data/.jwt-secret，重启后复用。
        """
        if not self.debug and not self.jwt_secret_key:
            raise ValueError(
                "生产环境必须配置 JWT_SECRET_KEY（生成方法："
                "python -c \"import secrets; print(secrets.token_hex(32))\"）"
            )
        if self.debug and not self.jwt_secret_key:
            self.jwt_secret_key = self._load_or_generate_jwt_secret()
        return self

    def _load_or_generate_jwt_secret(self) -> str:
        """
        开发模式加载或生成 JWT 密钥

        优先读取 data/.jwt-secret 中已持久化的密钥（重启后复用）；
        文件不存在或内容为空时生成 secrets.token_hex(32) 并写入该文件。

        Returns:
            str: JWT 签名密钥
        """
        if JWT_SECRET_FILE.exists():
            existing = JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        secret = secrets.token_hex(32)
        JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        JWT_SECRET_FILE.write_text(secret, encoding="utf-8")
        logger.warning(
            "开发模式未配置 JWT_SECRET_KEY，已自动生成随机密钥并持久化到 %s",
            JWT_SECRET_FILE,
        )
        return secret

    def get_cors_origins(self) -> list[str]:
        """
        解析 CORS 允许来源列表

        配置项 cors_origins 为逗号分隔字符串，此处拆分为列表供 CORSMiddleware 使用。

        Returns:
            list[str]: 允许的跨域来源列表
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

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

    def get_celery_broker_dir(self) -> Path:
        """
        获取文件系统 broker 的消息目录

        **这是 broker 目录的唯一权威来源**。此前有两处各算各的：
          - 本文件的 get_celery_broker_url() 用 DATA_DIR / "celery" / "broker"
          - app/tasks/celery_app.py 用 get_storage_dir().parent / "celery" / "broker"

        两者只在 storage_dir 未配置时才碰巧相等。一旦配置了 storage_dir
        或 vault_dir（部署时的常规做法），celery_app 那一路会指向
        **用户主目录**（storage 的父目录），结果是 kombu 往主目录写消息、
        而 Celery 读 DATA_DIR —— 任务被投递到无人监听的目录。

        Returns:
            Path: broker 目录（调用方负责创建）
        """
        return DATA_DIR / "celery" / "broker"

    def get_celery_result_dir(self) -> Path:
        """
        获取文件系统结果后端目录（Get_celery_result_backend 的路径来源）

        Returns:
            Path: 结果目录（调用方负责创建）
        """
        return DATA_DIR / "celery" / "results"

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
        broker_dir = self.get_celery_broker_dir()
        broker_dir.mkdir(parents=True, exist_ok=True)
        return "filesystem://"

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
        result_dir = self.get_celery_result_dir()
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
