"""LLM 子包

services/llm_service.py 的拆分目标：本包聚合共享 httpx 客户端、JSON 容错
解析、速率限制与多轮对话会话等基础设施，供 LLMService 与外部复用。
"""