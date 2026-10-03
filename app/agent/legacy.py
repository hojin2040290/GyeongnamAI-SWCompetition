"""예전 코드가 저장한 AI 글 정리 (AI가 연결돼 있을 때 '다시 맡기기' 작업이 함께 한다).

예전에는 AI가 판단 이유나 조언에 서버 안에서만 쓰는 이름(warn, quit_check, give_advice 등)을 써도 그대로 저장했다.
다시 점검하기 전까지 화면에 그대로 보이므로, 그런 이름이 있는 저장된 AI 글만 골라 LLM이 고쳐 쓰게 한다 (rewrite.for_user).
고친 글에는 내부 이름이 없으므로 다음에는 건너뛴다.
"""
import json

from sqlmodel import Session, select

from app.agent.rewrite import for_user
from app.agent.safety import internal_names
from app.models import AgentQuestion, CaseNote, CheckRun, GuardPost, Notification

AI_KEYS = {"ai_reason"}  # 점검 결과 JSON 안의 AI 글 칸


def _walk(v):
    if isinstance(v, dict):
        return {k: for_user(x) if k in AI_KEYS and isinstance(x, str) and internal_names(x) else _walk(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_walk(x) for x in v]
    return v


def _fix(row, field: str) -> bool:
    old = getattr(row, field) or ""
    new = for_user(old) if internal_names(old) else old
    if new != old:
        setattr(row, field, new)
    return new != old


def rename_internal(s: Session) -> int:
    """바꾼 칸 수."""
    n = 0
    for row in s.exec(select(CheckRun)).all():
        try:
            data = json.loads(row.results_json)
        except ValueError:
            continue
        fixed = _walk(data)
        if fixed != data:
            row.results_json = json.dumps(fixed, ensure_ascii=False)
            n += 1
    for model, fields in ((GuardPost, ["ai_reason"]), (CaseNote, ["text"]), (AgentQuestion, ["question", "why"]),
                          (Notification, ["body"])):
        for row in s.exec(select(model)).all():
            n += sum(_fix(row, f) for f in fields)
    s.commit()
    return n
