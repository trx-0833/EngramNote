import hashlib
import json
import logging
import uuid
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.assessment import AssessmentResult, AssessmentMode
from ..models.note import Note
from ..services.llm_service import LLMService
from ..services.note_service import get_clean_markdown_content, get_note_markdown_content

logger = logging.getLogger("engramnote.llm")


class AssessmentService:
    def __init__(self, llm_service: LLMService, db: AsyncSession):
        self._llm = llm_service
        self._db = db

    def _compute_link_signature(self, material_note_ids: List[str]) -> str:
        """计算链接指纹"""
        if not material_note_ids:
            return "empty_link_signature"
        sorted_ids = sorted(material_note_ids)
        combined = ",".join(sorted_ids)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    async def compare_assessment(
        self,
        user_id: str,
        material_note_ids: List[str],
        personal_note_ids: List[str],
    ) -> AssessmentResult:
        """笔记-资料比对评估"""
        # 1. Fetch notes content
        materials = await self._fetch_notes(material_note_ids, user_id)
        notes = await self._fetch_notes(personal_note_ids, user_id)

        material_content = await self._merge_note_contents(materials)
        note_content = await self._merge_note_contents(notes)

        # 2. Call LLM for comparison
        system_prompt = """你是一位专业的学习评估专家。你的任务是比对学生的学习笔记和原始学习资料，评估学生对知识的掌握程度。

请从以下三个维度进行评估，每个维度给出0-100的评分：

1. **内容覆盖度**：笔记是否涵盖了资料的核心知识点
2. **思考深度**：笔记是否有自己的理解、归纳、推理，而非单纯摘抄
3. **结构清晰度**：笔记是否有逻辑结构，而非零散记录

请以JSON格式返回评估结果，格式如下：
{
  "coverage_score": <0-100>,
  "depth_score": <0-100>,
  "clarity_score": <0-100>,
  "overall_score": <0-100>,
  "covered_points": ["已覆盖的知识点1", "已覆盖的知识点2"],
  "uncovered_points": ["未覆盖的知识点1", "未覆盖的知识点2"],
  "suggestions": "改进建议"
}"""

        user_prompt = f"""## 学习资料：
{material_content}

## 学生笔记：
{note_content}

请评估该学生对学习资料的掌握程度。"""

        response = await self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            scene="assess_compare",
            response_format={"type": "json_object"},
        )

        # 3. Parse response
        try:
            result_data = json.loads(response)
        except json.JSONDecodeError:
            result_data = {
                "coverage_score": 0,
                "depth_score": 0,
                "clarity_score": 0,
                "overall_score": 0,
                "covered_points": [],
                "uncovered_points": [],
                "suggestions": "评估结果解析失败",
            }

        # 4. Save result
        assessment = AssessmentResult(
            id=str(uuid.uuid4()),
            user_id=user_id,
            material_note_ids=material_note_ids,
            personal_note_ids=personal_note_ids,
            mode=AssessmentMode.compare,
            scores={
                "coverage_score": result_data.get("coverage_score", 0),
                "depth_score": result_data.get("depth_score", 0),
                "clarity_score": result_data.get("clarity_score", 0),
                "covered_points": result_data.get("covered_points", []),
                "uncovered_points": result_data.get("uncovered_points", []),
            },
            overall_score=result_data.get("overall_score", 0),
            suggestions=result_data.get("suggestions", ""),
        )
        self._db.add(assessment)
        await self._db.commit()
        await self._db.refresh(assessment)

        return assessment

    async def generate_quiz(
        self,
        user_id: str,
        material_note_ids: List[str],
        personal_note_id: Optional[str] = None,
    ) -> AssessmentResult:
        """生成开放性问题"""
        # 0. 缓存检查：当传入 personal_note_id 时，通过 link_signature 查找未过期的缓存
        if personal_note_id:
            link_signature = self._compute_link_signature(material_note_ids)
            cached = await self._db.execute(
                select(AssessmentResult).where(
                    AssessmentResult.user_id == user_id,
                    AssessmentResult.mode == AssessmentMode.quiz.value,
                    AssessmentResult.link_signature == link_signature,
                    AssessmentResult.is_stale == False,
                ).order_by(AssessmentResult.created_at.desc())
            )
            cached_quizzes = cached.scalars().all()
            if cached_quizzes:
                # 优先返回未作答的 quiz
                for quiz in cached_quizzes:
                    if not quiz.quiz_answers:
                        return quiz  # 返回未作答的缓存
                # 所有缓存都已作答，复用最新 quiz 的问题创建新 attempt
                latest = cached_quizzes[0]
                # 将旧已作答记录标记为 stale，避免缓存表膨胀和后续查询扫描过多
                for old in cached_quizzes:
                    if old.quiz_answers:
                        old.is_stale = True
                fresh_attempt = AssessmentResult(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    material_note_ids=latest.material_note_ids,
                    personal_note_ids=[personal_note_id],
                    mode=AssessmentMode.quiz,
                    scores={},
                    overall_score=0,
                    suggestions="",
                    quiz_questions=latest.quiz_questions,  # 复用已生成的问题
                    quiz_answers=[],  # 清空答案，新的一次作答
                    link_signature=link_signature,
                    is_stale=False,
                )
                self._db.add(fresh_attempt)
                await self._db.commit()
                await self._db.refresh(fresh_attempt)
                return fresh_attempt

        # 1. Fetch materials
        materials = await self._fetch_notes(material_note_ids, user_id)
        material_content = await self._merge_note_contents(materials)

        # 2. Generate questions via LLM
        system_prompt = """你是一位专业的教育评估专家。你的任务是根据提供的学习资料，生成3-5个开放性问题，用于评估学生对知识的掌握程度。

问题要求：
- 问题应覆盖资料的核心知识点
- 问题需要学生深入思考，而非简单记忆即可回答
- 问题应具有开放性，允许不同角度的回答
- 避免是/否类问题

请以JSON格式返回问题列表，格式如下：
{
  "questions": [
    {
      "index": 1,
      "question": "问题内容",
      "key_points": ["评分要点1", "评分要点2"]
    }
  ]
}"""

        user_prompt = f"""## 学习资料：
{material_content}

请根据以上学习资料生成3-5个开放性问题。"""

        response = await self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            scene="assess_quiz_generate",
            response_format={"type": "json_object"},
        )

        try:
            result_data = json.loads(response)
            questions = result_data.get("questions", [])
        except json.JSONDecodeError:
            questions = []

        # 3. 标记该笔记的旧 quiz 缓存为 stale（当 personal_note_id 传入时）
        if personal_note_id:
            old_quizzes = await self._db.execute(
                select(AssessmentResult).where(
                    AssessmentResult.user_id == user_id,
                    AssessmentResult.mode == AssessmentMode.quiz.value,
                    AssessmentResult.is_stale == False,
                )
            )
            for old_quiz in old_quizzes.scalars().all():
                # 通过 material_note_ids 匹配同一笔记的旧 quiz
                if old_quiz.material_note_ids and set(old_quiz.material_note_ids) == set(material_note_ids):
                    old_quiz.is_stale = True
            await self._db.commit()

        # 4. Save assessment with questions
        assessment = AssessmentResult(
            id=str(uuid.uuid4()),
            user_id=user_id,
            material_note_ids=material_note_ids,
            personal_note_ids=[personal_note_id] if personal_note_id else [],
            mode=AssessmentMode.quiz,
            scores={},
            overall_score=0,
            suggestions="",
            quiz_questions=questions,
            quiz_answers=[],
            link_signature=self._compute_link_signature(material_note_ids) if personal_note_id else None,
            is_stale=False,
        )
        self._db.add(assessment)
        await self._db.commit()
        await self._db.refresh(assessment)

        return assessment

    async def submit_answers(
        self,
        user_id: str,
        assessment_id: str,
        answers: List[Dict[str, Any]],
    ) -> AssessmentResult:
        """提交答案并评判"""
        # 1. Fetch assessment
        result = await self._db.execute(
            select(AssessmentResult).where(
                AssessmentResult.id == assessment_id,
                AssessmentResult.user_id == user_id,
            )
        )
        assessment = result.scalars().first()
        if not assessment:
            raise ValueError("评估记录不存在")

        if assessment.mode != AssessmentMode.quiz:
            raise ValueError("该评估不是开放性问题模式")

        # 2. Fetch material content
        materials = await self._fetch_notes(assessment.material_note_ids, user_id)
        material_content = await self._merge_note_contents(materials)

        # 3. Judge each answer
        judged_answers = []
        total_score = 0
        for answer in answers:
            q_idx = answer.get("question_index", 0)
            user_answer = answer.get("answer", "")

            # Get the question
            question_data = {}
            if q_idx < len(assessment.quiz_questions or []):
                question_data = assessment.quiz_questions[q_idx]

            question_text = question_data.get("question", "")
            key_points = question_data.get("key_points", [])

            system_prompt = """你是一位专业的教育评估专家。你的任务是评判学生对开放性问题的回答质量。

请从以下三个维度评分，每个维度0-100：
1. **准确性**：答案是否正确
2. **完整性**：是否覆盖了关键要点
3. **深度**：是否有深层理解而非表面记忆

请以JSON格式返回评判结果：
{
  "accuracy_score": <0-100>,
  "completeness_score": <0-100>,
  "depth_score": <0-100>,
  "question_score": <0-100, 综合评分>,
  "feedback": "具体反馈和建议",
  "covered_key_points": ["已覆盖的要点"],
  "missed_key_points": ["未覆盖的要点"]
}"""

            user_prompt = f"""## 原始资料：
{material_content[:3000]}

## 问题：{question_text}

## 评分要点：{', '.join(key_points)}

## 学生回答：{user_answer}

请评判该学生的回答。"""

            response = await self._llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                scene="assess_quiz_judge",
                response_format={"type": "json_object"},
            )

            try:
                judgment = json.loads(response)
            except json.JSONDecodeError:
                judgment = {
                    "accuracy_score": 0,
                    "completeness_score": 0,
                    "depth_score": 0,
                    "question_score": 0,
                    "feedback": "评判结果解析失败",
                    "covered_key_points": [],
                    "missed_key_points": [],
                }

            judged_answers.append({
                "question_index": q_idx,
                "answer": user_answer,
                "judgment": judgment,
            })
            total_score += judgment.get("question_score", 0)

        # 4. Update assessment
        overall_score = total_score / len(answers) if answers else 0
        assessment.quiz_answers = judged_answers
        assessment.overall_score = overall_score
        assessment.scores = {
            "total_questions": len(answers),
            "average_score": overall_score,
        }

        # Generate suggestions
        all_feedback = [a["judgment"].get("feedback", "") for a in judged_answers]
        assessment.suggestions = "\n".join(all_feedback)

        await self._db.commit()
        await self._db.refresh(assessment)

        return assessment

    async def get_history(self, user_id: str, note_id: str) -> List[AssessmentResult]:
        """获取评估历史"""
        result = await self._db.execute(
            select(AssessmentResult)
            .where(AssessmentResult.user_id == user_id)
            .order_by(AssessmentResult.created_at.desc())
        )
        # Filter to assessments that reference this note
        all_assessments = result.scalars().all()
        return [
            a for a in all_assessments
            if note_id in (a.material_note_ids or []) or note_id in (a.personal_note_ids or [])
        ]

    async def _fetch_notes(self, note_ids: List[str], user_id: str) -> List[Note]:
        """Fetch notes by IDs, verifying ownership（回收站笔记不可作为评估对象）"""
        result = await self._db.execute(
            select(Note).where(
                Note.id.in_(note_ids),
                Note.user_id == user_id,
                Note.trashed_at.is_(None),
            )
        )
        return result.scalars().all()

    async def _merge_note_contents(self, notes: List[Note]) -> str:
        """Merge multiple notes' markdown content"""
        parts = []
        for note in notes:
            title = note.title or "未命名笔记"
            # Try clean markdown first, fall back to original
            content = await get_clean_markdown_content(note)
            if not content:
                content = await get_note_markdown_content(note)
            if not content:
                content = ""
            parts.append(f"### {title}\n{content}")
        return "\n\n".join(parts)
