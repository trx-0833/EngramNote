"""
通知服务模块

本模块提供复习提醒通知能力，包括查询到期提醒数据、渲染邮件内容（HTML/纯文本）
以及通过 SMTP 发送复习提醒邮件。

主要职责：
- 查询用户当前提醒数据（到期数、1 小时内到期数、薄弱点数）
- 渲染中文邮件 HTML 和纯文本内容
- 通过 SMTP 发送复习提醒邮件（smtplib 同步调用，通过 asyncio.to_thread 异步包装）

设计决策：
- 所有 SMTP 调用包裹在 try/except 中，永不向上抛出异常，仅返回 bool
- smtplib 为同步阻塞库，使用 asyncio.to_thread 在独立线程中执行，避免阻塞事件循环
- 邮件内容使用中文，与项目中文定位保持一致
- SMTP 未配置或无到期复习时跳过发送，仅记录 info 日志
"""

import asyncio
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem

logger = logging.getLogger(__name__)

# 邮件中引用的应用入口链接（项目暂无独立配置项，使用默认开发地址）
APP_LINK = "http://localhost:3000"


class NotificationService:
    """
    通知服务

    提供复习提醒数据查询和邮件发送能力。

    使用方式：
        service = NotificationService()
        reminders = await service.get_reminders(user_id, db)
        ok = await service.send_review_email("user@example.com", user_id, db)
    """

    async def get_reminders(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """
        获取用户当前提醒数据

        统计维度：
        - due_count: 已到期或尚未安排复习的题目数（next_review_at <= now 或为 None）
        - due_in_1h_count: 未来 1 小时内到期的题目数（next_review_at BETWEEN now AND now+1h）
        - weak_point_count: 掌握度低于 60 的知识卡片数
        - last_reminded_at: 上次提醒时间（暂未持久化，固定返回 None）

        Args:
            user_id: 用户 ID
            db: 数据库会话

        Returns:
            Dict: 提醒数据
        """
        now = datetime.now(timezone.utc)
        now_plus_1h = now + timedelta(hours=1)

        # 到期题目数（next_review_at <= now 或为 None）
        due_count_result = await db.execute(
            select(func.count()).select_from(QuizItem).where(
                QuizItem.user_id == user_id,
                (QuizItem.next_review_at <= now) | (QuizItem.next_review_at.is_(None)),
            )
        )
        due_count = due_count_result.scalar() or 0

        # 1 小时内到期题目数（next_review_at BETWEEN now AND now+1h）
        due_in_1h_count_result = await db.execute(
            select(func.count()).select_from(QuizItem).where(
                QuizItem.user_id == user_id,
                QuizItem.next_review_at >= now,
                QuizItem.next_review_at <= now_plus_1h,
            )
        )
        due_in_1h_count = due_in_1h_count_result.scalar() or 0

        # 薄弱点数（掌握度 < 60 的知识卡片）
        weak_point_count_result = await db.execute(
            select(func.count()).select_from(KnowledgeCard).where(
                KnowledgeCard.user_id == user_id,
                KnowledgeCard.mastery_level < 60,
            )
        )
        weak_point_count = weak_point_count_result.scalar() or 0

        return {
            "due_count": due_count,
            "due_in_1h_count": due_in_1h_count,
            "weak_point_count": weak_point_count,
            "last_reminded_at": None,
        }

    async def send_review_email(
        self,
        user_email: str,
        user_id: str,
        db: AsyncSession,
    ) -> bool:
        """
        发送复习提醒邮件

        流程：
        1. 校验 SMTP 配置（smtp_host 和 smtp_user 非空），未配置则跳过
        2. 查询提醒数据，无到期复习则跳过
        3. 获取 top 3 薄弱知识点（按掌握度升序，仅取掌握度 < 60 的卡片）
        4. 渲染 HTML 和纯文本邮件内容
        5. 通过 asyncio.to_thread 在独立线程中同步发送 SMTP 邮件

        任何异常都不向上抛出，仅记录日志并返回 False。

        Args:
            user_email: 收件人邮箱
            user_id: 用户 ID
            db: 数据库会话

        Returns:
            bool: 发送成功返回 True，否则返回 False
        """
        try:
            settings = get_settings()

            # SMTP 未配置，跳过
            if not settings.smtp_host or not settings.smtp_user:
                logger.info("SMTP not configured, skipping email")
                return False

            # 查询提醒数据
            reminders = await self.get_reminders(user_id, db)
            due_count = reminders.get("due_count", 0)

            # 无到期复习，跳过
            if due_count == 0:
                logger.info("No due reviews, skipping email")
                return False

            # 获取 top 3 薄弱知识点
            weak_points = await self._get_top_weak_points(user_id, db, limit=3)

            subject = f"EngramNote 复习提醒 - 您有 {due_count} 道题目待复习"
            html_body = self.render_email_html(due_count, weak_points)
            text_body = self.render_email_text(due_count, weak_points)

            from_addr = settings.smtp_from or settings.smtp_user

            # 在独立线程中执行同步 SMTP 发送，避免阻塞事件循环
            await asyncio.to_thread(
                self._send_smtp_sync,
                settings.smtp_host,
                settings.smtp_port,
                settings.smtp_user,
                settings.smtp_password,
                settings.smtp_use_tls,
                from_addr,
                user_email,
                subject,
                html_body,
                text_body,
            )

            logger.info(
                f"复习提醒邮件发送成功: user={user_id[:8]}, email={user_email}, "
                f"due_count={due_count}, weak_points={len(weak_points)}"
            )
            return True
        except Exception as e:
            logger.error(
                f"复习提醒邮件发送失败: user={user_id[:8]}, email={user_email}, error={e}"
            )
            return False

    async def _get_top_weak_points(
        self,
        user_id: str,
        db: AsyncSession,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        获取掌握度最低的知识卡片（薄弱点）

        Args:
            user_id: 用户 ID
            db: 数据库会话
            limit: 返回数量上限

        Returns:
            List[Dict]: 薄弱知识点列表，每个包含 title 和 mastery_level
        """
        result = await db.execute(
            select(
                KnowledgeCard.title.label("title"),
                KnowledgeCard.mastery_level.label("mastery_level"),
            )
            .where(
                KnowledgeCard.user_id == user_id,
                KnowledgeCard.mastery_level < 60,
            )
            .order_by(KnowledgeCard.mastery_level.asc())
            .limit(limit)
        )
        rows = result.all()
        return [
            {
                "title": row.title,
                "mastery_level": round(float(row.mastery_level or 0), 1),
            }
            for row in rows
        ]

    @staticmethod
    def _send_smtp_sync(
        host: str,
        port: int,
        user: str,
        password: str,
        use_tls: bool,
        from_addr: str,
        to_addr: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        """
        同步发送 SMTP 邮件（通过 asyncio.to_thread 在独立线程中调用）

        Args:
            host: SMTP 服务器地址
            port: SMTP 服务器端口
            user: 登录用户名
            password: 登录密码
            use_tls: 是否启用 STARTTLS
            from_addr: 发件人地址
            to_addr: 收件人地址
            subject: 邮件主题
            html_body: HTML 正文
            text_body: 纯文本正文
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        # 先附加纯文本，再附加 HTML，邮件客户端按优先级展示后者
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(host, port) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())

    def render_email_html(self, due_count: int, weak_points: List[Dict[str, Any]]) -> str:
        """
        渲染复习提醒邮件 HTML 内容

        Args:
            due_count: 到期题目数
            weak_points: 薄弱知识点列表，每个含 title 和 mastery_level

        Returns:
            str: HTML 邮件内容（内联 CSS）
        """
        if weak_points:
            weak_points_rows = ""
            for wp in weak_points:
                title = wp.get("title", "")
                mastery = float(wp.get("mastery_level", 0))
                mastery_int = int(mastery)
                # 掌握度越低，颜色越红；接近 60 用橙色
                bar_color = "#e74c3c" if mastery < 40 else "#f39c12"
                weak_points_rows += (
                    "<tr>"
                    f'<td style="padding:10px 12px;border-bottom:1px solid #eee;color:#333;">{title}</td>'
                    '<td style="padding:10px 12px;border-bottom:1px solid #eee;width:160px;">'
                    '<div style="background:#f0f0f0;border-radius:4px;height:14px;overflow:hidden;">'
                    f'<div style="background:{bar_color};height:14px;width:{mastery_int}%;"></div>'
                    "</div>"
                    f'<span style="font-size:12px;color:#888;">掌握度 {mastery_int}%</span>'
                    "</td>"
                    "</tr>"
                )
        else:
            weak_points_rows = (
                '<tr><td colspan="2" style="padding:12px;color:#888;text-align:center;">'
                "暂无薄弱知识点，继续保持！</td></tr>"
            )

        return (
            '<!DOCTYPE html>\n'
            '<html lang="zh-CN">\n'
            '<head><meta charset="utf-8"><title>EngramNote 复习提醒</title></head>\n'
            '<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,'
            "BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;\">\n"
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
            'style="background:#f5f7fa;padding:24px 0;">\n'
            "  <tr><td align=\"center\">\n"
            '    <table role="presentation" cellpadding="0" cellspacing="0" width="600" '
            'style="background:#ffffff;border-radius:8px;overflow:hidden;'
            'box-shadow:0 2px 8px rgba(0,0,0,0.06);">\n'
            '      <tr><td style="background:#4a6cf7;padding:24px 32px;color:#ffffff;">'
            '<div style="font-size:22px;font-weight:bold;">EngramNote</div>'
            '<div style="font-size:13px;opacity:0.85;margin-top:4px;">智能笔记 · 间隔重复复习</div>'
            "</td></tr>\n"
            '      <tr><td style="padding:24px 32px 8px;">'
            '<div style="font-size:16px;color:#333;">您好，这是您的复习提醒：</div>'
            '<div style="margin-top:12px;padding:16px 20px;background:#eef2ff;border-radius:6px;">'
            f'<span style="font-size:28px;font-weight:bold;color:#4a6cf7;">{due_count}</span>'
            '<span style="font-size:14px;color:#555;margin-left:8px;">道题目待复习</span>'
            "</div>"
            '<div style="font-size:13px;color:#888;margin-top:10px;">'
            "请尽快登录 EngramNote 完成今日复习，巩固记忆。</div>"
            "</td></tr>\n"
            '      <tr><td style="padding:16px 32px 8px;">'
            '<div style="font-size:15px;font-weight:bold;color:#333;margin-bottom:8px;">薄弱知识点 Top 3</div>'
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
            'style="border-collapse:collapse;font-size:14px;">'
            f"{weak_points_rows}"
            "</table>"
            "</td></tr>\n"
            '      <tr><td style="padding:16px 32px 28px;">'
            f'<a href="{APP_LINK}" style="display:inline-block;background:#4a6cf7;color:#ffffff;'
            'text-decoration:none;padding:10px 24px;border-radius:5px;font-size:14px;">立即去复习</a>'
            "</td></tr>\n"
            '      <tr><td style="padding:16px 32px;background:#fafbfc;border-top:1px solid #eee;">'
            '<div style="font-size:12px;color:#999;line-height:1.6;">'
            "这是一封来自 EngramNote 的自动提醒邮件。<br>"
            f'<a href="{APP_LINK}" style="color:#4a6cf7;text-decoration:none;">{APP_LINK}</a>'
            "</div></td></tr>\n"
            "    </table>\n"
            "  </td></tr>\n"
            "</table>\n"
            "</body>\n"
            "</html>"
        )

    def render_email_text(self, due_count: int, weak_points: List[Dict[str, Any]]) -> str:
        """
        渲染复习提醒邮件纯文本内容

        Args:
            due_count: 到期题目数
            weak_points: 薄弱知识点列表，每个含 title 和 mastery_level

        Returns:
            str: 纯文本邮件内容
        """
        lines: List[str] = []
        lines.append("EngramNote 复习提醒")
        lines.append("智能笔记 · 间隔重复复习")
        lines.append("")
        lines.append(f"您好，您有 {due_count} 道题目待复习。")
        lines.append("请尽快登录 EngramNote 完成今日复习，巩固记忆。")
        lines.append("")
        lines.append("薄弱知识点 Top 3：")
        if weak_points:
            for i, wp in enumerate(weak_points, 1):
                title = wp.get("title", "")
                mastery = int(float(wp.get("mastery_level", 0)))
                lines.append(f"  {i}. {title}（掌握度 {mastery}%）")
        else:
            lines.append("  暂无薄弱知识点，继续保持！")
        lines.append("")
        lines.append(f"立即去复习：{APP_LINK}")
        lines.append("")
        lines.append("这是一封来自 EngramNote 的自动提醒邮件。")
        return "\n".join(lines)
