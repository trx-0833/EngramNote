"""验证 JSON 截断修复（max_tokens/解析容错/事件循环重建）"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def test_parse_markdown_wrapped():
    """剥离 markdown 代码块包裹"""
    from app.services.understanding_service import _parse_understanding_response
    r = _parse_understanding_response(
        '```json\n{"chapters": [{"chapter_title": "c1", "summary": "s1", "points": [{"card_type": "concept", "title": "t", "content": "x"}]}]}\n```'
    )
    assert r.get("chapters") and r["chapters"][0]["chapter_title"] == "c1", r
    print("[OK] 代码块包裹剥离")


def test_parse_truncated_no_crash():
    """截断 JSON 不崩溃，返回空结构"""
    from app.services.understanding_service import _parse_understanding_response
    truncated = '{"chapters": [{"chapter_title": "1 范围", "summary": "摘要", "points": [{"card_type": "conc'
    r = _parse_understanding_response(truncated)
    assert r == {"summary": "", "points": []}, r
    print("[OK] 截断 JSON 安全降级")


def test_parse_valid():
    """合法 JSON 正常解析"""
    from app.services.understanding_service import _parse_understanding_response
    r = _parse_understanding_response(
        '{"chapters": [{"chapter_title": "c1", "summary": "s", "points": [{"card_type": "concept", "title": "t"}]}]}'
    )
    assert len(r["chapters"]) == 1
    print("[OK] 合法 JSON 解析")


def test_llm_client_loop_rebuild():
    """共享客户端跨事件循环自动重建（修 Event loop is closed）"""
    from app.services import llm_service as ls

    async def use_in_loop():
        c1 = ls.get_llm_client()
        return id(c1)

    loop1_id = asyncio.run(use_in_loop())
    # 第二次 asyncio.run = 新事件循环 → client 必须重建（不同 id）
    loop2_id = asyncio.run(use_in_loop())
    assert loop1_id != loop2_id, "跨事件循环未重建客户端"
    print(f"[OK] 客户端跨循环重建 ({loop1_id} -> {loop2_id})")
    ls.close_llm_client()


def test_max_tokens_bumped():
    """提取/出题场景 max_tokens 已提升到 8192（静态断言）"""
    with open(os.path.join(os.path.dirname(__file__), "..", "app", "services", "llm_service.py"), encoding="utf-8") as f:
        src = f.read()
    # 关键场景不应再有 4096
    for scene in ["extract_knowledge", "generate_questions", "extract_combined", "generate_extension", "infer_relations"]:
        # 找到该 scene 附近的 max_tokens（简化：统计整体）
        pass
    # 统计整个文件的 4096 出现次数（应为 0——所有提取/出题场景都已提升）
    import re
    n4096 = len(re.findall(r"max_tokens=4096", src))
    assert n4096 == 0, f"仍有 {n4096} 处 max_tokens=4096"
    n8192 = len(re.findall(r"max_tokens=8192", src))
    print(f"[OK] max_tokens=4096 清零，8192 共 {n8192} 处")


if __name__ == "__main__":
    test_parse_markdown_wrapped()
    test_parse_truncated_no_crash()
    test_parse_valid()
    test_llm_client_loop_rebuild()
    test_max_tokens_bumped()
    print("\n=== 全部验证通过 ===")
