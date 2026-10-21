# -*- coding: utf-8 -*-
"""
数据盘点报告的结构守卫（D1）
==============================

## 为什么一份 markdown 报告需要测试

D1 的产物只有一份报告（`docs/data-inventory-<date>.md`），而这份报告会被
**当成删除清单**使用 —— 它的三档分类（🟢 现在就能删 / 🟠 要先确认 /
🔴 不确定 → 不动）如果错了，后果是**用户数据丢失**，不是"报告写得不漂亮"。

具体到本项目：`AGENTS.md` §6 记着一条历史事故 ——
**「清理副本却删掉真身」**。所以最需要钉住的一条判据是：

> **`storage/`（用户文件）与 `db/`（主库）绝不能出现在 🟢「现在就能删」档里。**

这类错误是**静默的** —— 报告读起来通顺、格式正确、有免责声明，
只是把真身归到了"可以删"。没有任何东西会报错，除了一条像下面这样的守卫。
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"


def _latest_inventory() -> pathlib.Path:
    """取最新的一份 `data-inventory-*.md`

    报告是按日期命名的（过程记录），所以不能写死文件名 ——
    写死会在下次更新报告时变成"测的是一份旧文件"，
    而那种失效**看起来像通过**。
    """
    candidates = sorted(DOCS.glob("data-inventory-*.md"))
    assert candidates, "docs/ 下没有任何 data-inventory-*.md —— D1 的产物不见了"
    return candidates[-1]


def _tier_section(text: str, marker: str) -> str:
    """截出某一档的小节正文（含 `marker` 的那个**标题行** → 下一个标题）

    ⚠️ 两个都踩过的坑：

    1. 不能只找 `text.index(marker)` —— `"现在就能删"` 在 §0 的正文里也出现过
       （"以及一份「现在就能删 / 要先确认 / 不确定 → 不动」的三档清单"），
       于是截出的是 §0 的一小段，里面**一行表格都没有**；
    2. 也不能写死 `"### " + marker` —— 三档的标题是
       `### 🟢 现在就能删（无引用、无功能影响）`，前缀有 emoji，
       而 `### 2.4` 这种又没有。

    所以按**行**找：标题行（`#` 开头）里含 marker 的那一行即起点。
    """
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith("#") and marker in line:
            start = i
            break
    assert start is not None, f"报告里找不到含 {marker!r} 的标题行"
    # 起点行之后，遇到下一个同级或更高级标题就停
    depth = len(lines[start]) - len(lines[start].lstrip("#"))
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("#"):
            if len(line) - len(line.lstrip("#")) <= depth:
                break
        out.append(line)
    return "".join(out)


@pytest.fixture(scope="module")
def report_text() -> str:
    return _latest_inventory().read_text(encoding="utf-8")


class TestReportExistsAndIsHonest:
    def test_report_is_marked_as_a_process_record(self, report_text):
        """报告必须**自己声明**是过程记录（不是活文档）

        仓库约定：文档带状态横幅（活文档 / 历史快照 / 过程记录）。
        这份报告的数字会随使用变化 —— 不声明的话，下一个人会把它当现状依据。
        """
        head = report_text[:600]
        assert "过程记录" in head, "报告开头没有声明它是过程记录"
        assert "只读" in head, "报告开头没有声明它是只读盘点"

    def test_disclaimer_says_no_bytes_were_touched(self, report_text):
        """必须写清"本报告没有删除/移动/修改任何一个字节"

        没有这句，一份名叫《数据盘点与删除决策报告》的文档被当成
        "已经清过了"或"可以照着执行"都是迟早的事。
        """
        assert "没有删除" in report_text
        assert "单独批" in report_text, "没有写清'任何删除动作都要另开一轮单独批'"

    def test_plan_decision_is_cited(self, report_text):
        """要引用已拍板的那一条（否则读者不知道这个约束从哪来）"""
        assert "§5 第 6 条" in report_text or "5 第 6 条" in report_text


class TestTierSafety:
    """★ 这一组是这份报告存在的全部理由"""

    def test_storage_is_never_in_the_safe_to_delete_tier(self, report_text):
        """★★ `storage/` 绝不能出现在 🟢「现在就能删」档

        `storage/` 是用户笔记的原始文件与清洗副本。删了 = 用户数据丢失，
        且数据库里仍有指向它们的路径（"笔记都在但每一篇都打不开"）。
        **备份策略也不覆盖它**（周快照只做库），所以删了就是真的没了。
        """
        safe = _tier_section(report_text, "现在就能删")
        assert "storage" not in safe.lower(), (
            "`storage/` 出现在了「现在就能删」档里 —— 这是最严重的一类错误"
        )

    def test_db_is_never_in_the_safe_to_delete_tier(self, report_text):
        """★★ **真库的完整路径** `data/db/engramnote.db` 绝不能出现在 🟢 档

        ⚠️ 这里必须按**路径**判，不能按文件名判 —— 第一版就是这么写的，
        当场红了，而**红得有价值**：

        `data/` 根目录下有一个 **0 字节的 `engramnote.db`**（6 月 27 日的空壳），
        它与真库 `data/db/engramnote.db` **同名**。那个空壳**确实**该删、
        也确实在 🟢 档里；而真库在 🔴 档。

        按 basename 判就分不开这两者 —— 而这正是本报告 §4 那条
        「看绝对路径，不看相对路径」的实例。守卫自己也得守这条规矩。
        """
        safe = _tier_section(report_text, "现在就能删")
        assert "data/db/engramnote.db" not in safe, (
            "真库（data/db/engramnote.db）出现在了「现在就能删」档里"
        )
        assert "-wal" not in safe and "-shm" not in safe, (
            "WAL/SHM 出现在了「现在就能删」档里 —— 它们看着像临时文件，实则含未落盘事务"
        )

    def test_the_stray_empty_db_is_distinguishable_in_the_report(self, report_text):
        """★ 那个 0 字节空壳必须**被明确指为不是真库**

        否则读者在 🟢 档里看到 `engramnote.db` 会以为自己找到了主库 ——
        而两个文件同名是这份报告里最容易误读的一处。
        """
        safe = _tier_section(report_text, "现在就能删")
        row = [ln for ln in safe.splitlines() if "engramnote.db" in ln]
        assert row, "🟢 档里应该有那个 0 字节的空壳文件"
        assert "根目录" in safe and "真库在" in safe, (
            "🟢 档里没有写清「这是根目录那个空壳、真库在 db/」"
        )

    def test_uxwalk_is_not_in_the_safe_tier(self, report_text):
        """★ `_uxwalk/` 是**副本**，但 A2/A3′ 重跑还没做 ⇒ 不能归进"现在就能删"

        这条正是 `AGENTS.md` §6 那个事故的镜像：副本确实不是真身，
        但它仍然有用途（重建要时间），不能因为"是副本"就立即删。
        """
        safe = _tier_section(report_text, "现在就能删")
        assert "_uxwalk" not in safe

    def test_safe_tier_only_contains_the_four_known_items(self, report_text):
        """🟢 档的内容必须**白名单化** —— 新增条目要显式改这张表

        没有这条，将来有人往"现在就能删"里加一样东西时，
        上面三条只能保证"不是 storage/db/_uxwalk"，其余一概不管。
        """
        safe = _tier_section(report_text, "现在就能删")
        allowed = ("_a1p0_probe", "engramnote.db", "tmp_test", "_understand_test_tmp")
        # 表格行里的第一个反引号内容就是目标路径
        targets = re.findall(r"^\|\s*`([^`]+)`", safe, re.MULTILINE)
        assert targets, f"🟢 档里没解析出任何目标 —— 报告格式变了？\n{safe[:400]}"
        unexpected = [t for t in targets if not any(a in t for a in allowed)]
        assert not unexpected, f"🟢 档里出现了未登记的目标：{unexpected}"


class TestEveryItemHasTheThreeFacts:
    """验收判据：每一项都要有「引用者 / 删除影响 / 恢复方式」"""

    def test_each_major_entry_is_covered(self, report_text):
        for entry in ("models/", "mineru/", "_uxwalk/", "storage/", "chroma/", "db/"):
            assert entry in report_text, f"盘点漏了 {entry}"

    def test_storage_section_states_it_is_not_recoverable_from_db_backups(self, report_text):
        """★ `storage/` 那一节必须说清"备份不覆盖它"

        这是最容易被想当然的一条：「反正有备份」。而备份是**库**的备份，
        用户文件不在其中 —— 不说清的话，读者会以为删了能恢复。
        """
        section = _tier_section(report_text, "### 2.4")
        assert "没有它就真的没了" in section or "不含" in section, (
            "storage 那一节没有说清「备份不覆盖用户文件」"
        )


class TestReproductionCommand:
    def test_report_tells_how_to_regenerate(self, report_text):
        """报告必须写清"怎么重跑"（否则数字只能手改，而手改会脱节）"""
        assert "d1_inventory.py" in report_text
        assert "不要手改" in report_text or "重跑" in report_text


class TestAdjacentDocFix:
    """D1 盘点时撞到的两处"声明 ≠ 现实"，本轮顺手修了文档"""

    def test_test_accounts_doc_uses_the_real_cleanup_path(self):
        """★ `e2e_cleanup.py` 的真实位置是 `backend/scripts/dev/`

        原文档写的是 `python e2e_cleanup.py` —— 照做会得到
        `can't open file`。这条守卫钉住修正后的路径。
        """
        doc = REPO / "backend" / "tests" / "测试账号信息.md"
        text = doc.read_text(encoding="utf-8")
        assert "scripts/dev/e2e_cleanup.py" in text, "文档里的清理命令路径又退回旧的了"
        assert (REPO / "backend" / "scripts" / "dev" / "e2e_cleanup.py").exists(), (
            "文档指向的脚本真的不存在 —— 那这条指引仍然是假的"
        )

    def test_doc_does_not_recommend_the_broken_local_script(self):
        """★ 不能把 `cleanup_test_data.py` 当可用入口（它 import 就崩）"""
        doc = REPO / "backend" / "tests" / "测试账号信息.md"
        text = doc.read_text(encoding="utf-8")
        if "cleanup_test_data" in text:
            assert "别把它当作可用的清理入口" in text, (
                "提到了那个脚本却没说明它坏了"
            )
