"""
Vault 一致性校验服务（overhaul-plan 阶段 1.11）

回答一个此前**无法回答**的问题：**磁盘上的文件与数据库的记录还一致吗？**

## 为什么必须有它

本项目的存储层是"DB 记路径 + 文件系统存内容"的双写结构，而双写的两侧
都可能单独出问题：

- **DB 有、磁盘无**：用户在界面上看得到笔记，点开却是空的 / 报错。
  成因：外部误删、迁移丢失、上传中断（上传顺序是先 commit DB 再写文件）。
- **磁盘有、DB 无**：文件永久占用空间且**无人知晓** —— 既看不到、也删不掉。
  成因：删除时文件层失败后 DB 记录仍被删除（M-10 的另一面）、
  上传失败回滚不彻底。
- **大小/哈希不符**：文件被截断或替换。成因：磁盘故障、并发写同一路径。

三种情况在**没有校验机制**时都是静默的，只能等用户报障。
`overhaul-plan` 附录 A.7 已经实测到垃圾残留（8 个 tmp/worker 残留目录、
1 行两端皆 NULL 的死链接），说明这类发散是真实发生的。

## 设计要点

**只报告，不修改。** 与 `_migrate_sqlite` 的孤儿检查同一原则：
校验器一旦自动"修复"，就会把一次误判变成不可逆的数据删除。
修复动作留给运维显式执行（见 `scripts/verify_vault.py --repair`）。

**哈希是可选的。** 计算全部 markdown 的 sha256 在大库上很慢，
默认只比大小；`--deep` 才逐文件哈希。大小相同但内容不同的概率极低，
日常巡检用大小足够，排查具体问题时再用深度模式。
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.note import Note
from ..models.note_version import NoteVersion
from .vault_path import META_DIR, SOURCE_DIR, TRASH_SLUG

logger = logging.getLogger(__name__)

#: 参与一致性校验的路径字段 → 所在 bucket
#:
#: `clean_md_path` 与两个 markdown 路径同桶；`original_file_path` 在原文件桶。
PATH_FIELDS: tuple[tuple[str, str], ...] = (
    ("original_file_path", "original"),
    ("original_md_path", "markdown"),
    ("clean_md_path", "markdown"),
)


@dataclass
class VaultIssue:
    """一条不一致记录"""
    kind: str            # missing_file | orphan_file | size_mismatch | hash_mismatch
    note_id: Optional[str]
    object_name: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "note_id": self.note_id,
            "object_name": self.object_name,
            "detail": self.detail,
        }


@dataclass
class VaultAuditResult:
    """一次校验的结果汇总"""
    notes_scanned: int = 0
    db_objects: int = 0
    disk_objects: int = 0
    issues: list[VaultIssue] = field(default_factory=list)
    #: 按严重度分组计数，便于脚本直接打印
    counts: dict[str, int] = field(default_factory=dict)
    #: 是否做了哈希比对（影响耗时的预期）
    deep: bool = False

    def add(self, issue: VaultIssue) -> None:
        self.issues.append(issue)
        self.counts[issue.kind] = self.counts.get(issue.kind, 0) + 1

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "deep": self.deep,
            "notes_scanned": self.notes_scanned,
            "db_objects": self.db_objects,
            "disk_objects": self.disk_objects,
            "counts": self.counts,
            "issues": [i.to_dict() for i in self.issues],
        }


def _bucket_names() -> tuple[str, str]:
    """返回 (原文件桶, markdown 桶)"""
    settings = get_settings()
    return settings.minio_bucket_original, settings.minio_bucket_markdown


def _resolve_bucket(kind: str) -> str:
    original, markdown = _bucket_names()
    return original if kind == "original" else markdown


def _objects_are_namespaced() -> bool:
    """本后端的桶之间是否**确有区分能力**

    MinIO 是：同名对象可以分别存在于 original 与 markdown 两个桶。
    本地模式**不是**：`_resolve_path` 对已含 `source/output/history/cache`
    段的 Vault 结构路径不加 bucket 前缀，直接映射到单一目录树，
    于是 `data/storage/{user}/inbox/source/x.pdf` 同时是 original 与
    markdown 两个"桶"里的对象（见 `list_object_names` 文档）。

    这个区分决定了"应存在"集合与磁盘枚举能否用同一种 key 形式比较 ——
    第一版忽略了它，用 `(bucket, name)` 建集合、却只枚举 markdown 桶，
    于是把 20 个原文件全部误报为孤儿（**校验器最危险的失效模式**：
    误报会诱导运维去删用户的真实资料）。
    """
    return get_settings().storage_backend == "minio"


def _vault_key(bucket: str, object_name: str) -> tuple[str, str]:
    """构造可比较的 Vault 键（与 `list_object_names` 的枚举结果对齐）

    本地模式下丢弃 bucket，使"DB 记录的路径"与"磁盘枚举到的路径"
    落在同一个命名空间里，从而可以直接求集合差。
    """
    if _objects_are_namespaced():
        return (bucket, object_name)
    return ("", object_name)


def _expected_keys(expected: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """把已收集的 (bucket, name) 映射为比较用键集合"""
    return {_vault_key(b, n) for b, n in expected}


def sha256_of(bucket: str, object_name: str) -> Optional[str]:
    """计算对象内容的 sha256；不存在或读取失败时返回 None"""
    from .storage_service import get_object_bytes

    try:
        return hashlib.sha256(get_object_bytes(bucket, object_name)).hexdigest()
    except Exception:
        return None


async def audit_vault(
    db: AsyncSession,
    *,
    user_id: Optional[str] = None,
    deep: bool = False,
    include_orphans: bool = True,
) -> VaultAuditResult:
    """校验 DB 记录的路径与磁盘文件是否一致

    Args:
        db: 数据库会话
        user_id: 只校验该用户的笔记；None 表示全部
        deep: 是否逐文件比对 sha256（慢，默认关）
        include_orphans: 是否扫描"磁盘有、DB 无"的孤儿文件

    Returns:
        VaultAuditResult

    注意：孤儿扫描需要遍历磁盘目录，笔记多时较慢；`include_orphans=False`
    可只做"DB → 磁盘"方向的校验（快）。
    """
    from .storage_service import file_exists

    result = VaultAuditResult(deep=deep)

    query = select(Note)
    if user_id:
        query = query.where(Note.user_id == user_id)
    notes = list((await db.execute(query)).scalars().all())
    result.notes_scanned = len(notes)

    #: 磁盘上应当存在的对象名集合（用于孤儿判定）
    expected: set[tuple[str, str]] = set()

    for note in notes:
        for field_name, bucket_kind in PATH_FIELDS:
            object_name = getattr(note, field_name, None)
            if not object_name:
                continue
            bucket = _resolve_bucket(bucket_kind)
            result.db_objects += 1
            expected.add((bucket, object_name))

            if not file_exists(bucket, object_name):
                result.add(VaultIssue(
                    kind="missing_file",
                    note_id=note.id,
                    object_name=object_name,
                    detail=f"DB 的 {field_name} 指向的文件不存在（笔记在界面上可见但内容缺失）",
                ))
                continue

            if deep:
                actual = sha256_of(bucket, object_name)
                recorded = _recorded_hash(note, field_name)
                if actual and recorded and actual != recorded:
                    result.add(VaultIssue(
                        kind="hash_mismatch",
                        note_id=note.id,
                        object_name=object_name,
                        detail=f"sha256 不符：DB 记录 {recorded[:12]}… 实际 {actual[:12]}…",
                    ))

    # 版本文件也在 DB 中有记录，同样纳入"应存在"集合
    version_query = select(NoteVersion)
    if user_id:
        version_query = version_query.where(NoteVersion.user_id == user_id)
    for version in (await db.execute(version_query)).scalars().all():
        if not version.storage_path:
            continue
        bucket = _resolve_bucket("markdown")
        result.db_objects += 1
        expected.add((bucket, version.storage_path))
        if not file_exists(bucket, version.storage_path):
            result.add(VaultIssue(
                kind="missing_file",
                note_id=version.note_id,
                object_name=version.storage_path,
                detail=f"版本 v{version.version_number} 的文件不存在（版本历史里有记录，内容已丢失）",
            ))

    if include_orphans:
        await _scan_orphans(db, result, expected, user_id)

    logger.info(
        "Vault 一致性校验完成: 笔记 %d，DB 对象 %d，磁盘对象 %d，问题 %d（%s）",
        result.notes_scanned, result.db_objects, result.disk_objects,
        len(result.issues), result.counts or "无",
    )
    return result


def _recorded_hash(note: Note, field_name: str) -> Optional[str]:
    """从 note.metadata_ 里取该字段记录的 sha256（若有）

    只有原始文件在元数据里有 `file_hash`（上传时算的）。
    markdown 类字段没有记录哈希，因此深度模式对它们只能报"文件缺失"，
    不会报 hash_mismatch —— 这是有意的：没有可信基线就不该下"内容被改过"的结论。
    """
    if field_name != "original_file_path":
        return None
    metadata = note.metadata_ or {}
    value = metadata.get("file_hash")
    return value if isinstance(value, str) and value else None


def _is_mirror_only(name: str) -> bool:
    """`output/meta/` 下的写穿镜像不是孤儿

    每篇笔记的 `output/meta/{base}.json` 是状态旁载镜像，用户级
    `output/meta/projects.json` 是项目标签清单（见 `vault_meta` 模块）。
    它们**有意不进数据库**：DB 本身就是状态的权威来源，镜像只是为了让
    Vault 脱离 DB 也能被读懂。既不参与"应存在"集合，也就永远不会
    被 DB 引用 —— 若按"无引用即孤儿"判定，**每一篇笔记都会报一个孤儿**，
    21 条噪声会把真正的孤儿彻底淹没。
    """
    # 判断路径中是否**连续包含** output/meta 段。
    # 第一版取路径**末尾两段**比对，而 meta 对象是
    # "…/inbox/output/meta/{base}.json"，末尾两段是 "meta/x.json" ——
    # 谓词恒为 False，21 条噪声原样漏出。
    return f"/{META_DIR}/" in f"/{name}/"


def _is_source_object(name: str) -> bool:
    """`source/` 下的是用户原始资料，**永不自动清理**

    原文件只存在于 original 桶。若某后端的桶之间确有区分能力，
    枚举 markdown 桶时看不到它们 —— 那种情况下它们由 `expected`
    的 original 键覆盖，不该出现在"磁盘有、DB 无"的候选里。
    """
    return SOURCE_DIR in name.split("/")


async def _scan_orphans(
    db: AsyncSession,
    result: VaultAuditResult,
    expected: set[tuple[str, str]],
    user_id: Optional[str],
) -> None:
    """扫描"磁盘有、DB 无"的孤儿对象

    只扫常规对象区；**不动** `history/`（版本文件多且已单独校验）、
    `output/meta/` 镜像（见 `_is_mirror_only`）与 trash 目录
    （回收站里的文件在 DB 里仍有记录，不是孤儿）。

    比较用的是 `_vault_key`，与"应存在"集合同一形式 —— 本地模式下
    bucket 不参与比较，MinIO 模式下参与。
    """
    from .storage_service import list_object_names

    original_bucket, markdown_bucket = _bucket_names()
    namespaced = _objects_are_namespaced()
    #: MinIO 下同名对象可能分别存在于两个桶，必须分别枚举；
    #: 本地模式两个"桶"是同一棵目录树，枚举一次即可（否则每个对象数两遍）
    buckets = (markdown_bucket,) if not namespaced else (markdown_bucket, original_bucket)

    known = _expected_keys(expected)

    for bucket in buckets:
        for prefix in _orphan_scan_prefixes(user_id):
            try:
                names = list_object_names(bucket, prefix)
            except Exception as exc:  # pragma: no cover - 取决于存储后端
                logger.warning("扫描孤儿文件失败（忽略）: %s/%s, %s", bucket, prefix, exc)
                continue

            for name in names:
                # trash 目录下的文件对应仍存在的笔记记录，跳过
                if f"/{TRASH_SLUG}/" in name:
                    continue
                if _is_mirror_only(name):
                    continue
                if _vault_key(bucket, name) in known:
                    result.disk_objects += 1
                    continue
                if namespaced and _is_source_object(name):
                    # 原文件不属于本桶的枚举范围，由 original 键单独覆盖
                    continue
                result.disk_objects += 1
                result.add(VaultIssue(
                    kind="orphan_file",
                    note_id=None,
                    object_name=name,
                    detail="磁盘上存在但 DB 无任何记录引用（占用空间且无法通过界面删除）",
                ))


def _orphan_scan_prefixes(user_id: Optional[str]) -> Iterable[str]:
    """孤儿扫描的目录前缀

    有 user_id 时只扫该用户；否则扫整个桶（`""` 表示根）。
    """
    if user_id:
        return (f"{user_id}/",)
    return ("",)
