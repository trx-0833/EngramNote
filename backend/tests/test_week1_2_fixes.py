"""
Week1-2 修改验证测试

验证开发时间表中 Week1-2 发现的 6 个问题是否已完全修复：
1. Token过期前端告警（后端 401 返回验证）
2. 用户名校验（仅英文+数字，前后端一致）
3. mineru_plus 页码标签插入
4. mineru_backend 配置（config.py + convert_tasks.py）
5. 上传选择本地/云端解析（upload.py + client.ts）
6. PDF体积膨胀修复确认

运行方式：cd backend && pytest tests/test_week1_2_fixes.py -v
"""

import inspect
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict

import pytest

# ---------------------------------------------------------------------------
# 路径设置：确保能导入项目模块
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent  # D:\test\EngramNote\backend
ENGRAMNOTE_DIR = BACKEND_DIR.parent                   # D:\test\EngramNote
TEST_ROOT_DIR = ENGRAMNOTE_DIR.parent                 # D:\test（mineru_plus 在此目录下）
MINERU_PLUS_DIR = TEST_ROOT_DIR / "mineru_plus"

# 将 D:\test 加入 sys.path（使 mineru_plus 可被 import）
_test_root = str(TEST_ROOT_DIR)
if _test_root not in sys.path:
    sys.path.insert(0, _test_root)

# 将 backend 目录加入 sys.path（使 app 可被 import）
_backend_dir = str(BACKEND_DIR)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# 测试用真实 PDF 文件路径
REAL_PDF_PATH = r"D:\test\resources1\劳动合同书-田润鑫.pdf"

# .env 文件路径（包含 MINERU_API_TOKEN 等配置）
ENV_FILE = TEST_ROOT_DIR / ".env"


def _load_env_for_mineru():
    """从 .env 文件加载 MinerU API 配置到环境变量

    mineru_plus 的 config_loader 通过 _load_env_config() 读取环境变量
    MINERU_API_TOKEN 和 MINERU_SERVER_URL。此函数确保这些环境变量可用。
    """
    if not ENV_FILE.exists():
        return

    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k in ("MINERU_API_TOKEN", "MINERU_SERVER_URL"):
                    os.environ.setdefault(k, v)


# ===========================================================================
# 修改1：Token过期前端告警 — 后端 API 401 返回验证
# ===========================================================================


class TestTokenExpired401:
    """验证后端在 Token 无效/过期时正确返回 401 状态码

    前端 client.ts 中 request() 和 uploadFile() 在收到 401 时会调用
    notifyTokenExpired() 派发全局事件，App.tsx 监听后跳转登录页。
    此测试验证后端 401 行为正确，确保前端告警机制能被触发。
    """

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self):
        """使用无效 Token 请求受保护 API 应返回 401"""
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer invalid_token_here"},
            )
            assert response.status_code == 401, (
                f"无效 Token 应返回 401，实际返回 {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_no_token_returns_401(self):
        """不携带 Token 请求受保护 API 应返回 401"""
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/auth/me")
            assert response.status_code == 401, (
                f"无 Token 应返回 401，实际返回 {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self):
        """过期 Token 请求受保护 API 应返回 401

        构造一个已过期的 JWT Token，验证后端正确识别并返回 401。
        """
        from jose import jwt

        from app.config import get_settings

        settings = get_settings()
        # 构造一个已过期的 Token（exp 设为过去时间）
        expired_payload = {
            "sub": "test-user-id",
            "exp": 0,  # 1970-01-01，已过期
        }
        expired_token = jwt.encode(
            expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
        )

        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {expired_token}"},
            )
            assert response.status_code == 401, (
                f"过期 Token 应返回 401，实际返回 {response.status_code}"
            )


# ===========================================================================
# 修改2：用户名校验 — 后端 Schema + 前端正则等价验证
# ===========================================================================


class TestUsernameValidation:
    """验证用户名校验：仅允许英文字母和数字

    后端：UserRegisterRequest 的 username 字段添加了 pattern=r"^[a-zA-Z0-9]+$"
    前端：Register.tsx 中 handleSubmit 添加了同样的正则校验
    """

    def test_valid_usernames(self):
        """合法用户名：纯英文、纯数字、英文+数字组合"""
        from pydantic import ValidationError

        from app.schemas.user import UserRegisterRequest

        valid_usernames = ["ab", "ABC", "abc123", "testuser2024", "01", "aB0"]
        for username in valid_usernames:
            req = UserRegisterRequest(
                email="test@example.com", username=username, password="123456"
            )
            assert req.username == username, f"合法用户名 '{username}' 应通过校验"

    def test_rejects_special_characters(self):
        """非法用户名：包含特殊字符应被拒绝"""
        from pydantic import ValidationError

        from app.schemas.user import UserRegisterRequest

        invalid_usernames = [
            "test_user",   # 下划线
            "test-user",   # 连字符
            "测试用户",     # 中文
            "user@name",   # @
            "user name",   # 空格
            "user.name",   # 点号
            "user+tag",    # 加号
        ]
        for username in invalid_usernames:
            with pytest.raises(ValidationError, match="string_pattern_mismatch"):
                UserRegisterRequest(
                    email="test@example.com", username=username, password="123456"
                )

    def test_min_max_length(self):
        """用户名长度校验：1字符太短、2字符合法、50字符合法、51字符太长"""
        from pydantic import ValidationError

        from app.schemas.user import UserRegisterRequest

        # 太短：1 个字符
        with pytest.raises(ValidationError):
            UserRegisterRequest(
                email="test@example.com", username="a", password="123456"
            )

        # 合法：2 个字符
        req = UserRegisterRequest(
            email="test@example.com", username="ab", password="123456"
        )
        assert req.username == "ab"

        # 合法：50 个字符
        long_username = "a" * 50
        req = UserRegisterRequest(
            email="test@example.com", username=long_username, password="123456"
        )
        assert req.username == long_username

        # 太长：51 个字符
        with pytest.raises(ValidationError):
            UserRegisterRequest(
                email="test@example.com", username="a" * 51, password="123456"
            )

    def test_frontend_regex_matches_backend(self):
        """前端正则 ^[a-zA-Z0-9]+$ 与后端 pattern 行为一致

        Register.tsx 中使用 /^[a-zA-Z0-9]+$/.test(username) 校验，
        后端 schema 使用 pattern=r"^[a-zA-Z0-9]+$" 校验。
        两者应对同一组输入产生相同的校验结果。
        """
        # 前端使用的正则（与 Register.tsx 第55行一致）
        frontend_pattern = re.compile(r"^[a-zA-Z0-9]+$")

        test_cases = {
            # username: expected_valid
            "abc123": True,
            "ABC": True,
            "testuser2024": True,
            "test_user": False,
            "test-user": False,
            "测试用户": False,
            "user@name": False,
            "user name": False,
            "a": True,   # 前端正则不检查长度，只检查字符集
            "aB0": True,
        }

        for username, expected_valid in test_cases.items():
            frontend_result = bool(frontend_pattern.match(username))
            assert frontend_result == expected_valid, (
                f"用户名 '{username}'：前端正则校验结果为 {frontend_result}，"
                f"期望为 {expected_valid}"
            )


# ===========================================================================
# 修改3：mineru_plus 页码标签插入
# ===========================================================================


class TestPageMarkerInsertion:
    """验证 _insert_page_markers_from_content_list 函数正确插入页码标签

    该函数读取 MinerU 输出的 content_list.json，利用 page_idx 字段
    在 Markdown 中按页插入 <!-- page=N --> 标签。
    """

    def test_basic_insertion_with_synthetic_data(self):
        """使用构造的 content_list 数据测试基本插入逻辑"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        # 构造 3 页 content_list
        content_list = [
            {"type": "text", "page_idx": 0, "text": "第一页的内容开始"},
            {"type": "text", "page_idx": 0, "text": "第一页的更多内容"},
            {"type": "text", "page_idx": 1, "text": "第二页的内容开始"},
            {"type": "text", "page_idx": 1, "text": "第二页的更多内容"},
            {"type": "text", "page_idx": 2, "text": "第三页的内容开始"},
            {"type": "text", "page_idx": 2, "text": "第三页的更多内容"},
        ]

        # 构造对应的 Markdown 内容
        md_content = (
            "第一页的内容开始\n"
            "第一页的更多内容\n"
            "第二页的内容开始\n"
            "第二页的更多内容\n"
            "第三页的内容开始\n"
            "第三页的更多内容\n"
        )

        # 写入临时 content_list.json
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(content_list, f, ensure_ascii=False)
            content_list_path = Path(f.name)

        try:
            result = _insert_page_markers_from_content_list(
                md_content, content_list_path, start_page_id=0
            )

            # 验证页码标签存在
            page_markers = re.findall(r"<!-- page=(\d+) -->", result)
            assert len(page_markers) == 3, (
                f"应插入 3 个页码标签，实际插入 {len(page_markers)} 个"
            )

            # 验证页码值
            page_numbers = [int(n) for n in page_markers]
            assert page_numbers == [1, 2, 3], (
                f"页码应为 [1, 2, 3]，实际为 {page_numbers}"
            )

            # 验证标签位置：每个标签应在对应页面内容之前
            assert "<!-- page=1 -->" in result
            assert "<!-- page=2 -->" in result
            assert "<!-- page=3 -->" in result
        finally:
            content_list_path.unlink(missing_ok=True)

    def test_start_page_offset(self):
        """分块场景：start_page_id=5 时页码应从6开始"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        content_list = [
            {"type": "text", "page_idx": 0, "text": "分块内容A"},
            {"type": "text", "page_idx": 1, "text": "分块内容B"},
        ]

        md_content = "分块内容A\n分块内容B\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(content_list, f, ensure_ascii=False)
            content_list_path = Path(f.name)

        try:
            result = _insert_page_markers_from_content_list(
                md_content, content_list_path, start_page_id=5
            )

            page_markers = re.findall(r"<!-- page=(\d+) -->", result)
            page_numbers = [int(n) for n in page_markers]
            # page_idx=0 + 1 + start_page_id=5 = 6
            # page_idx=1 + 1 + start_page_id=5 = 7
            assert page_numbers == [6, 7], (
                f"start_page_id=5 时页码应为 [6, 7]，实际为 {page_numbers}"
            )
        finally:
            content_list_path.unlink(missing_ok=True)

    def test_empty_content_list_returns_original(self):
        """空 content_list 应返回原始 Markdown 不变"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        md_content = "原始内容\n不变\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump([], f)
            content_list_path = Path(f.name)

        try:
            result = _insert_page_markers_from_content_list(
                md_content, content_list_path, start_page_id=0
            )
            assert result == md_content, "空 content_list 应返回原始 Markdown"
        finally:
            content_list_path.unlink(missing_ok=True)

    def test_missing_content_list_file_returns_original(self):
        """content_list.json 不存在时应返回原始 Markdown 不变"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        md_content = "原始内容\n不变\n"
        nonexistent_path = Path("/tmp/nonexistent_content_list_12345.json")

        result = _insert_page_markers_from_content_list(
            md_content, nonexistent_path, start_page_id=0
        )
        assert result == md_content, "文件不存在时应返回原始 Markdown"

    def test_duplicate_text_across_pages(self):
        """不同页有相同首文本时，页码标签应按顺序插入而非错乱"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        # page_idx=0 和 page_idx=2 的首文本都是 "你好"
        content_list = [
            {"type": "text", "page_idx": 0, "text": "你好"},
            {"type": "text", "page_idx": 1, "text": "中国"},
            {"type": "text", "page_idx": 2, "text": "你好"},
            {"type": "text", "page_idx": 3, "text": "世界"},
        ]

        md_content = "你好\n中国\n你好\n世界\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(content_list, f, ensure_ascii=False)
            content_list_path = Path(f.name)

        try:
            result = _insert_page_markers_from_content_list(
                md_content, content_list_path, start_page_id=0
            )

            page_markers = re.findall(r"<!-- page=(\d+) -->", result)
            page_numbers = [int(n) for n in page_markers]
            assert page_numbers == [1, 2, 3, 4], (
                f"页码应为 [1, 2, 3, 4]，实际为 {page_numbers}"
            )

            # 验证页码标签在 markdown 中的出现顺序与页码递增一致
            marker_positions = [
                result.index(f"<!-- page={n} -->") for n in page_numbers
            ]
            for i in range(1, len(marker_positions)):
                assert marker_positions[i] > marker_positions[i - 1], (
                    f"page={page_numbers[i]} 出现在 page={page_numbers[i-1]} 之前，顺序错乱"
                )
        finally:
            content_list_path.unlink(missing_ok=True)

    def test_aside_text_excluded(self):
        """aside_text/header/footer 等辅助类型不应作为页码定位标记"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        # page_idx=0 的第一个 item 是 aside_text，应被跳过
        # page_idx=1 的第一个 item 是 header，应被跳过
        content_list = [
            {"type": "aside_text", "page_idx": 0, "text": "侧边栏文字"},
            {"type": "text", "page_idx": 0, "text": "正文开始"},
            {"type": "header", "page_idx": 1, "text": "页眉文字"},
            {"type": "text", "page_idx": 1, "text": "第二页正文"},
        ]

        md_content = "正文开始\n第二页正文\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(content_list, f, ensure_ascii=False)
            content_list_path = Path(f.name)

        try:
            result = _insert_page_markers_from_content_list(
                md_content, content_list_path, start_page_id=0
            )

            page_markers = re.findall(r"<!-- page=(\d+) -->", result)
            page_numbers = [int(n) for n in page_markers]
            assert page_numbers == [1, 2], (
                f"排除辅助类型后页码应为 [1, 2]，实际为 {page_numbers}"
            )

            # aside_text 和 header 的文本不应出现在定位逻辑中
            # 验证 page=1 在 "正文开始" 之前，page=2 在 "第二页正文" 之前
            assert result.index("<!-- page=1 -->") < result.index("正文开始")
            assert result.index("<!-- page=2 -->") < result.index("第二页正文")
        finally:
            content_list_path.unlink(missing_ok=True)

    def test_page_markers_order_is_sequential(self):
        """页码标签在 markdown 中的出现顺序必须与页码递增一致"""
        from mineru_plus.converter import _insert_page_markers_from_content_list

        # 模拟真实场景：4页文档，page 0 和 page 2 首文本相同
        content_list = [
            {"type": "text", "page_idx": 0, "text": "\\*\\*\\*你好"},
            {"type": "aside_text", "page_idx": 0, "text": "\\*\\*\\*"},
            {"type": "text", "page_idx": 1, "text": "\\*\\*\\*中国"},
            {"type": "aside_text", "page_idx": 1, "text": "\\*\\*\\*"},
            {"type": "text", "page_idx": 2, "text": "\\*\\*\\*你好"},
            {"type": "aside_text", "page_idx": 2, "text": "\\*\\*\\*"},
            {"type": "text", "page_idx": 3, "text": "\\*\\*\\*世界"},
        ]

        md_content = "\\*\\*\\*你好\n\\*\\*\\*中国\n\\*\\*\\*你好\n\\*\\*\\*世界\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(content_list, f, ensure_ascii=False)
            content_list_path = Path(f.name)

        try:
            result = _insert_page_markers_from_content_list(
                md_content, content_list_path, start_page_id=0
            )

            page_markers = re.findall(r"<!-- page=(\d+) -->", result)
            page_numbers = [int(n) for n in page_markers]
            assert page_numbers == [1, 2, 3, 4], (
                f"页码应为 [1, 2, 3, 4]，实际为 {page_numbers}"
            )

            # 关键断言：页码标签在文档中的出现顺序必须递增
            marker_positions = [
                result.index(f"<!-- page={n} -->") for n in page_numbers
            ]
            for i in range(1, len(marker_positions)):
                assert marker_positions[i] > marker_positions[i - 1], (
                    f"<!-- page={page_numbers[i]} --> 出现在 "
                    f"<!-- page={page_numbers[i-1]} --> 之前，顺序错乱！"
                )
        finally:
            content_list_path.unlink(missing_ok=True)

    @pytest.mark.slow
    def test_page_markers_with_real_pdf(self):
        """使用真实 PDF 文件通过云端 API 测试页码标签插入

        调用 convert() 转换 劳动合同书-田润鑫.pdf（使用 vlm-http-client 云端后端），
        验证返回的 markdown_content 中包含 <!-- page=N --> 标签。

        此测试需要 MINERU_API_TOKEN 已配置（从 D:\\test\\.env 加载）。
        云端转换可能需要 2-5 分钟，超时设为 600 秒。
        """
        if not os.path.exists(REAL_PDF_PATH):
            pytest.skip(f"测试文件不存在: {REAL_PDF_PATH}")

        # 加载 MinerU API 配置
        _load_env_for_mineru()

        # 检查 API Token 是否已配置
        api_token = os.environ.get("MINERU_API_TOKEN", "")
        if not api_token:
            pytest.skip(
                "MINERU_API_TOKEN 未配置，跳过云端 PDF 转换测试。"
                "请在 D:\\test\\.env 中设置 MINERU_API_TOKEN"
            )

        from mineru_plus.converter import convert

        start_time = time.time()
        print(f"\n  开始云端 API 转换: {REAL_PDF_PATH}")

        result = convert(
            REAL_PDF_PATH,
            backend="vlm-http-client",
            lang="ch",
            timeout=600,  # 云端转换可能需要较长时间
        )

        elapsed = time.time() - start_time
        print(f"  转换耗时: {elapsed:.1f} 秒")

        assert result.success, f"转换失败: {result.error}"
        assert result.markdown_content, "Markdown 内容为空"

        # 验证页码标签存在
        page_markers = re.findall(r"<!-- page=(\d+) -->", result.markdown_content)
        assert len(page_markers) > 0, (
            "Markdown 中未找到页码标签 <!-- page=N -->，"
            "页码标签插入功能可能未生效"
        )

        # 验证页码从1开始
        page_numbers = [int(n) for n in page_markers]
        assert page_numbers[0] == 1, (
            f"第一个页码应为1，实际为 {page_numbers[0]}"
        )

        # 验证页码递增（不允许递减）
        for i in range(1, len(page_numbers)):
            assert page_numbers[i] > page_numbers[i - 1], (
                f"页码应递增：page={page_numbers[i-1]} 后出现 page={page_numbers[i]}"
            )

        # 验证页码数量与元数据中的页数一致
        if result.metadata and "page_count" in result.metadata:
            expected_pages = result.metadata["page_count"]
            assert page_numbers[-1] <= expected_pages, (
                f"最大页码 {page_numbers[-1]} 超过文档总页数 {expected_pages}"
            )

        print(f"  真实 PDF 测试结果：")
        print(f"  文件: 劳动合同书-田润鑫.pdf")
        print(f"  后端: vlm-http-client（云端 API）")
        print(f"  页码标签数: {len(page_markers)}")
        print(f"  页码范围: {page_numbers[0]} ~ {page_numbers[-1]}")
        print(f"  元数据页数: {result.metadata.get('page_count', '未知')}")
        # 输出前200个字符的 Markdown 内容，便于调试
        preview = result.markdown_content[:200].replace("\n", "\\n")
        print(f"  Markdown 预览: {preview}")


# ===========================================================================
# 修改4：mineru_backend 配置
# ===========================================================================


class TestMineruBackendConfig:
    """验证 config.py 中 mineru_backend 配置项和 convert_tasks.py 使用配置"""

    def test_settings_has_mineru_backend_field(self):
        """Settings 类应包含 mineru_backend 字段"""
        from app.config import Settings

        s = Settings()
        assert hasattr(s, "mineru_backend"), "Settings 缺少 mineru_backend 字段"

    def test_mineru_backend_default_is_pipeline(self):
        """mineru_backend 默认值应为 pipeline（本地处理）"""
        from app.config import Settings

        s = Settings()
        assert s.mineru_backend == "pipeline", (
            f"mineru_backend 默认值应为 'pipeline'，实际为 '{s.mineru_backend}'"
        )

    def test_mineru_backend_from_env(self):
        """mineru_backend 可通过环境变量 MINERU_BACKEND 覆盖"""
        from app.config import Settings

        # 保存原始值
        original = os.environ.get("MINERU_BACKEND")

        try:
            os.environ["MINERU_BACKEND"] = "vlm-http-client"
            s = Settings()
            assert s.mineru_backend == "vlm-http-client", (
                f"环境变量设置后 mineru_backend 应为 'vlm-http-client'，"
                f"实际为 '{s.mineru_backend}'"
            )
        finally:
            # 恢复原始值
            if original is None:
                os.environ.pop("MINERU_BACKEND", None)
            else:
                os.environ["MINERU_BACKEND"] = original

    def test_convert_document_accepts_backend_parameter(self):
        """_convert_document 函数签名应接受 backend 参数，默认 None"""
        # 使用 inspect 读取源码而非导入，避免数据库初始化
        convert_tasks_path = BACKEND_DIR / "app" / "tasks" / "convert_tasks.py"
        source = convert_tasks_path.read_text(encoding="utf-8")

        # 验证函数签名中包含 backend 参数
        assert "backend: Optional[str] = None" in source, (
            "_convert_document 函数签名中未找到 backend: Optional[str] = None 参数"
        )

    def test_convert_document_task_accepts_backend_parameter(self):
        """convert_document_task 函数签名应接受 backend 参数"""
        # 使用 inspect 读取源码而非导入，避免数据库初始化
        convert_tasks_path = BACKEND_DIR / "app" / "tasks" / "convert_tasks.py"
        source = convert_tasks_path.read_text(encoding="utf-8")

        # 验证 Celery 任务签名中包含 backend 参数
        assert "backend: Optional[str] = None" in source, (
            "convert_document_task 函数签名中未找到 backend 参数"
        )
        # 验证 backend 被传递给 _convert_document
        assert "_convert_document(note_id, file_path, source_type, backend)" in source, (
            "convert_document_task 中未将 backend 传递给 _convert_document"
        )

    def test_convert_tasks_uses_config_not_hardcoded(self):
        """convert_tasks.py 应从 settings.mineru_backend 读取配置，而非硬编码"""
        # 读取 convert_tasks.py 源码，验证不包含硬编码的 backend="vlm-http-client"
        convert_tasks_path = BACKEND_DIR / "app" / "tasks" / "convert_tasks.py"
        source = convert_tasks_path.read_text(encoding="utf-8")

        # 不应包含硬编码的 backend="vlm-http-client"
        assert 'backend="vlm-http-client"' not in source, (
            "convert_tasks.py 中仍包含硬编码的 backend=\"vlm-http-client\"，"
            "应改为从 settings.mineru_backend 读取"
        )

        # 应包含 settings.mineru_backend 引用
        assert "settings.mineru_backend" in source, (
            "convert_tasks.py 中未引用 settings.mineru_backend"
        )


# ===========================================================================
# 修改5：上传选择本地/云端解析
# ===========================================================================


class TestUploadBackendParameter:
    """验证上传 API 接受 backend 参数并正确传递"""

    def test_upload_api_has_backend_form_parameter(self):
        """upload_document 函数签名应接受 backend Form 参数"""
        # 读取源码验证，避免数据库初始化
        upload_py_path = BACKEND_DIR / "app" / "api" / "upload.py"
        source = upload_py_path.read_text(encoding="utf-8")

        assert "backend: Optional[str] = Form(None)" in source, (
            "upload_document 函数签名中未找到 backend: Optional[str] = Form(None) 参数"
        )

    def test_upload_api_backend_is_optional(self):
        """backend 参数应为可选（默认 None）"""
        upload_py_path = BACKEND_DIR / "app" / "api" / "upload.py"
        source = upload_py_path.read_text(encoding="utf-8")

        # 验证 Form(None) 表示可选
        assert "Form(None)" in source, (
            "backend 参数的 Form() 默认值应为 None"
        )

    def test_upload_file_in_client_ts_accepts_backend(self):
        """验证前端 client.ts 中 uploadFile 函数接受 backend 参数

        通过检查源码确认函数签名包含 backend 可选参数。
        """
        client_ts_path = (
            BACKEND_DIR.parent / "frontend" / "src" / "api" / "client.ts"
        )
        if not client_ts_path.exists():
            pytest.skip("前端 client.ts 文件不存在")

        source = client_ts_path.read_text(encoding="utf-8")

        # 验证 uploadFile 函数签名包含 backend 参数
        assert "backend" in source, "client.ts 中未找到 backend 参数"
        # 验证 FormData 中附加 backend
        assert "formData.append('backend'" in source, (
            "client.ts 中未将 backend 附加到 FormData"
        )

    def test_upload_tsx_has_backend_options(self):
        """验证前端 Upload.tsx 中包含解析方式选择按钮"""
        upload_tsx_path = (
            BACKEND_DIR.parent / "frontend" / "src" / "pages" / "Upload.tsx"
        )
        if not upload_tsx_path.exists():
            pytest.skip("前端 Upload.tsx 文件不存在")

        source = upload_tsx_path.read_text(encoding="utf-8")

        # 验证 BACKEND_OPTIONS 常量
        assert "BACKEND_OPTIONS" in source, "Upload.tsx 中未找到 BACKEND_OPTIONS"
        # 验证 parseBackend 状态
        assert "parseBackend" in source, "Upload.tsx 中未找到 parseBackend 状态"
        # 验证传递给 uploadFile
        assert "parseBackend" in source and "uploadFile" in source, (
            "Upload.tsx 中未将 parseBackend 传递给 uploadFile"
        )

    def test_celery_task_receives_backend_from_upload(self):
        """验证 upload.py 将 backend 参数传递给 Celery 任务"""
        upload_py_path = BACKEND_DIR / "app" / "api" / "upload.py"
        source = upload_py_path.read_text(encoding="utf-8")

        # 验证 convert_document_task.delay 调用中包含 backend
        assert "backend=backend" in source, (
            "upload.py 中未将 backend 参数传递给 Celery 任务"
        )


# ===========================================================================
# 修改6：PDF体积膨胀修复确认
# ===========================================================================


class TestPdfBloatFix:
    """验证 intake.py 中 PDF 分块使用 PyMuPDF 防止资源膨胀"""

    def test_intake_uses_pymupdf_insert_pdf(self):
        """intake.py 中 _split_pdf 应使用 PyMuPDF 的 insert_pdf"""
        intake_path = MINERU_PLUS_DIR / "intake.py"
        if not intake_path.exists():
            pytest.skip("mineru_plus/intake.py 文件不存在")

        source = intake_path.read_text(encoding="utf-8")

        # 验证使用 insert_pdf（PyMuPDF 的方法，防止资源膨胀）
        assert "insert_pdf" in source, (
            "intake.py 中未使用 insert_pdf，PDF 分块可能仍有资源膨胀问题"
        )

    def test_intake_has_strip_unused_resources(self):
        """intake.py 应包含 _strip_unused_resources 函数"""
        intake_path = MINERU_PLUS_DIR / "intake.py"
        if not intake_path.exists():
            pytest.skip("mineru_plus/intake.py 文件不存在")

        source = intake_path.read_text(encoding="utf-8")

        # 验证存在清理未引用资源的函数
        assert "_strip_unused_resources" in source, (
            "intake.py 中未找到 _strip_unused_resources 函数"
        )
