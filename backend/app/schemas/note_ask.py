from pydantic import BaseModel


class NoteAskRequest(BaseModel):
    question: str
    selected_text: str
    context_before: str = ""
    context_after: str = ""
    view_mode: str = "original"  # original / clean（仅供参考语义，后端不据此读取全文）