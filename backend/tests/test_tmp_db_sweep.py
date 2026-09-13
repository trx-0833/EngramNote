"""`data/tmp_test/` 陈旧临时库清理的测试（附录 AX）

## 为什么这值得一条测试

清理逻辑写在 `conftest.pytest_configure` 里，它**不在**任何断言之下：
写错了不会让任何用例变红，只会安静地不生效（或者更糟：删掉不该删的东西）。
这与项目里反复出现的"工具写好了没人调用 / 注释承诺了行为"同源 ——
**看不见的失效**。因此把判定逻辑单独拎出来验一遍。

同时验证两条安全边界：

- **新鲜的临时库不动**（可能是另一个并发会话正在用的）；
- **只删 `test_*.db`**（同目录下别的文件、别的扩展名都不碰）。
"""

import os
import time

from tests import conftest


def _make(tmp_dir, name: str, age_seconds: float, size: int = 16) -> str:
    path = os.path.join(str(tmp_dir), name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


class TestSweepStaleTestDbs:
    def _run(self, tmp_dir):
        """把清理逻辑指向临时目录（绝不碰真实的 data/tmp_test）"""
        return conftest._sweep_stale_test_dbs(str(tmp_dir))

    def test_removes_only_stale_test_dbs(self, tmp_path):
        tmp_dir = tmp_path / "tmp_test"
        tmp_dir.mkdir()
        old = _make(tmp_dir, "test_aaaaaaaa.db", age_seconds=7 * 3600)
        fresh = _make(tmp_dir, "test_bbbbbbbb.db", age_seconds=60)
        # ★ 主库新鲜（本轮不会被清理）→ 它的侧车必须原样留着，
        #   即使侧车自己已经"陈旧"：里面可能有尚未 checkpoint 的数据
        _make(tmp_dir, "test_bbbbbbbb.db-wal", age_seconds=7 * 3600)
        orphan_wal = _make(tmp_dir, "test_cccccccc.db-wal", age_seconds=7 * 3600)
        orphan_shm = _make(tmp_dir, "test_cccccccc.db-shm", age_seconds=7 * 3600)
        unrelated = _make(tmp_dir, "session.db", age_seconds=7 * 3600)

        assert self._run(tmp_dir) == 3  # old.db + 两个孤儿侧车

        assert not os.path.exists(old), "陈旧的临时库应当被清理"
        assert os.path.exists(fresh), "新鲜临时库不得删除（可能是并发会话在用）"
        assert os.path.exists(tmp_dir / "test_bbbbbbbb.db-wal"), (
            "主库活下来的侧车不得删除（会丢未 checkpoint 的数据）"
        )
        assert not os.path.exists(orphan_wal), "孤儿 -wal 应当被清理"
        assert not os.path.exists(orphan_shm), "孤儿 -shm 应当被清理"
        assert os.path.exists(unrelated), "不匹配 test_ 前缀的文件不得删除"

    def test_sidecar_goes_with_its_own_stale_db(self, tmp_path):
        """主库**本轮会被清理**时，它的侧车一并清理（主库都没了，侧车没有意义）

        第一版实现依赖 `os.listdir` 的顺序：先删主库、再看侧车时它就成了"孤儿"，
        于是同一份文件会因遍历顺序不同而得到不同结果。现在先把要删的主库集合
        算出来，判定就不再依赖顺序 —— 这条用例锁定该行为。
        """
        tmp_dir = tmp_path / "tmp_test"
        tmp_dir.mkdir()
        db = _make(tmp_dir, "test_33333333.db", age_seconds=7 * 3600)
        wal = _make(tmp_dir, "test_33333333.db-wal", age_seconds=7 * 3600)
        shm = _make(tmp_dir, "test_33333333.db-shm", age_seconds=7 * 3600)

        assert self._run(tmp_dir) == 3

        assert not os.path.exists(db)
        assert not os.path.exists(wal)
        assert not os.path.exists(shm)

    def test_removes_orphan_sidecars(self, tmp_path):
        """★ 主库已被删除、只剩 `-wal`/`-shm` 的孤儿必须清掉

        本轮在真实 `data/tmp_test/` 里实测到的就是这一种：fixture 删掉了主库，
        侧车留了下来 —— 只看 `.db` 结尾的逻辑对它们完全无效，会一直堆积。
        """
        tmp_dir = tmp_path / "tmp_test"
        tmp_dir.mkdir()
        wal = _make(tmp_dir, "test_11111111.db-wal", age_seconds=7 * 3600)
        shm = _make(tmp_dir, "test_11111111.db-shm", age_seconds=7 * 3600)
        fresh_wal = _make(tmp_dir, "test_22222222.db-wal", age_seconds=60)

        assert self._run(tmp_dir) == 2

        assert not os.path.exists(wal), "孤儿 -wal 应当被清理"
        assert not os.path.exists(shm), "孤儿 -shm 应当被清理"
        assert os.path.exists(fresh_wal), "新鲜的孤儿侧车不在本次清理范围（阈值之内）"

    def test_is_quiet_when_nothing_to_clean(self, tmp_path, capsys):
        tmp_dir = tmp_path / "tmp_test"
        tmp_dir.mkdir()
        _make(tmp_dir, "test_dddddddd.db", age_seconds=60)

        assert self._run(tmp_dir) == 0
        assert "清理陈旧临时测试库" not in capsys.readouterr().out

    def test_missing_directory_is_not_an_error(self, tmp_path):
        """目录不存在（全新检出）时静默返回，不能让整个会话起不来"""
        assert self._run(tmp_path / "nope") == 0

    def test_reports_what_it_freed(self, tmp_path, capsys):
        tmp_dir = tmp_path / "tmp_test"
        tmp_dir.mkdir()
        _make(tmp_dir, "test_eeeeeeee.db", age_seconds=8 * 3600, size=2 * 1024 * 1024)
        _make(tmp_dir, "test_ffffffff.db", age_seconds=8 * 3600, size=1024 * 1024)

        assert self._run(tmp_dir) == 2

        out = capsys.readouterr().out
        assert "2 个" in out and "3.0 MB" in out

    def test_default_directory_is_the_real_one(self):
        """缺省目录必须就是 `test_db` fixture 用的那个（写错 = 永远扫不到东西）"""
        import os as _os

        expected = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(conftest.__file__))),
            "data", "tmp_test",
        )
        assert conftest._TMP_TEST_DIR == expected
