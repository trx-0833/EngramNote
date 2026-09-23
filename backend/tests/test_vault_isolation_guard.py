"""存储安全网守卫：测试期间**任何**落盘都不得写进真实 Vault

## 这个文件防的是什么

`storage_service._get_storage_root()` 的默认值是真实的
`backend/data/storage/`。本仓库历史上出现过两种"测试写真实存储"的形态：

1. **跑了就留垃圾**：一轮测试在真实存储根里留下 26 个随机 user_id 目录
   （`test_purge_file_consistency.py` 的旧 docstring 自述）；
2. **清理反而更危险**：靠"删掉运行期间新增的顶层目录"来收拾，
   一旦用户在测试运行期间往存储根放新目录，就会被当成测试垃圾删掉 ——
   而真实用户目录不可再生（`test_vault_audit.py:70-75` 已判定此路不通）。

正确做法是**重定向**：`conftest._redirect_vault_to_tmp` 把整个会话的
`VAULT_DIR` 指向临时目录。本文件锁住这条不变式，防止将来有人把它删掉。
"""

from pathlib import Path

from app.config import get_settings
from app.services import storage_service
from app.services.storage_service import _get_storage_root


def _real_vault() -> Path:
    return (Path(__file__).resolve().parent.parent / "data" / "storage").resolve()


class TestVaultIsRedirectedDuringTests:
    def test_storage_root_is_not_the_real_vault(self):
        """`_get_storage_root()` 必须落在临时目录，而不是 `backend/data/storage`"""
        root = _get_storage_root()
        assert Path(root).resolve() != _real_vault(), (
            f"测试的存储根指向了真实 Vault：{root}。"
            "这会真的写入用户数据 —— 见 conftest._redirect_vault_to_tmp"
        )

    def test_settings_point_at_the_same_redirect(self):
        """模块级冻结引用与当前 settings 必须指向**同一个 Vault**

        ⚠️ 这里断言的是**路径值**，不是对象身份（`is`）。
        原因：`test_vault_audit.py` / `test_purge_file_consistency.py` 的文件级
        fixture 退出时会 `get_settings.cache_clear()` 再重绑，产生的是**新实例**；
        实例身份因此不可能跨文件恒定，但"指向哪里"才是真正要守的不变式。
        """
        settings = get_settings()
        assert Path(storage_service.settings.get_vault_dir()).resolve() == Path(
            settings.get_vault_dir()
        ).resolve(), (
            "storage_service.settings 与 get_settings() 指向了不同的 Vault —— "
            "模块级冻结引用没被重绑，_get_storage_root() 会读旧实例（可能指向真实 Vault）"
        )
        assert Path(_get_storage_root()).resolve() != _real_vault()

    def test_writing_through_storage_service_lands_in_tmp(self):
        """一次真实写入必须落在重定向后的根里（行为证据，不只是路径断言）"""
        probe = f"guard-probe-{Path(__file__).stem}/hello.txt"
        storage_service.upload_bytes("original-files", probe, b"probe")
        try:
            hits = list(Path(_get_storage_root()).rglob("hello.txt"))
            assert hits, f"写入没有落在重定向的存储根里：{_get_storage_root()}"
            assert any("guard-probe" in str(p) for p in hits)
            for p in hits:
                assert _real_vault() not in p.resolve().parents, (
                    f"写入落进了真实 Vault：{p}"
                )
        finally:
            try:
                storage_service.delete_file("original-files", probe)
            except Exception:  # noqa: BLE001 - 清理失败不影响断言结论
                pass
