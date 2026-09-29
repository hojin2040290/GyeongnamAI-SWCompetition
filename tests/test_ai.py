"""AI 모델(vLLM) 연결 테스트. 실제 모델 대신 응답만 흉내 내고, 사진 저장, 에이전트 흐름, 검증 장치는 실제로 돈다."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.llm import client
from app.main import app

SAMPLE = Path(__file__).resolve().parent.parent / "테스트자료"

CONTRACT = {"임금": "시급 9,288원 (수습기간 중 최저임금의 90%)", "근로시간": "매주 월, 수, 금, 토 17시 00분 ~ 22시 30분",
            "휴게시간": "", "휴일": "매주 일요일", "연차휴가": "관계 법령에 따름",
            "근무장소": "경상남도 창원시 의창구 도계동 312-7", "업무내용": "편의점 계산, 상품 진열"}
PAYSLIP = {"month": "2026-09", "net_pay": "557,280원", "base_pay": "557,280", "weekly_holiday_pay": "0", "deduction": "0"}


def fake_post(payload: dict) -> dict:
    """vLLM의 /chat/completions 응답 흉내."""
    msgs = payload["messages"]
    content = msgs[-1]["content"]
    if isinstance(content, list):  # 사진
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        text = content[0]["text"]
        answer = "```json\n" + json.dumps(PAYSLIP if "급여명세서" in text else CONTRACT, ensure_ascii=False) + "\n```"
    else:  # 조항 판단: 모든 항목을 정상이라고 답해 검증 장치가 되돌리는지 본다
        n = len(content.splitlines())
        answer = json.dumps([{"i": i, "status": "ok", "reason": "테스트"} for i in range(n)])
    return {"choices": [{"message": {"role": "assistant", "content": answer}}]}


@pytest.fixture()
def ai(monkeypatch):
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake-vl")
    monkeypatch.setattr(client, "_post", fake_post)


@pytest.fixture(scope="module")
def c():
    init_db()
    with TestClient(app) as cl:
        cl.post("/api/auth/register", json={"email": "ai@example.com", "password": "test1234", "birth_date": "2009-05-20"})
        cl.post("/api/jobs", json={"name": "가상편의점 AI점", "wage": 9288, "size": "lt5", "probation": "yes",
                                   "probation_months": 3, "start_date": "2026-09-07", "end_date": "2027-03-06",
                                   "contract_written": True, "copy_received": True,
                                   "schedule": {d: {"start": "17:00", "end": "22:30", "brk": "30분"} for d in "월수금토"}})
        yield cl


def job_id(c) -> int:
    return [j for j in c.get("/api/jobs").json() if j["name"] == "가상편의점 AI점"][0]["id"]


def test_contract_photo_without_model(c):
    r = c.post(f"/api/jobs/{job_id(c)}/contract",
               files={"file": ("계약서.png", (SAMPLE / "02_근로계약서.png").read_bytes(), "image/png")}).json()
    assert r["ai"] is False and "연결되지 않았어요" in r["reason"] and r["found"] == 0


def test_contract_photo_read_by_vision_model(c, ai):
    r = c.post(f"/api/jobs/{job_id(c)}/contract",
               files={"file": ("계약서.png", (SAMPLE / "02_근로계약서.png").read_bytes(), "image/png")}).json()
    assert r["ai"] is True and r["found"] == 6 and r["total"] == 7
    assert r["fields"]["휴게시간"] == "" and "9,288" in r["fields"]["임금"]
    assert any(t["step"] == "도구 read_contract_image" for t in r["trace"])


def test_pdf_is_not_sent_to_model(c, ai):
    r = c.post(f"/api/jobs/{job_id(c)}/contract", files={"file": ("계약서.pdf", b"%PDF-1.4", "application/pdf")}).json()
    assert r["ai"] is False and "사진 파일" in r["reason"]


def test_payslip_photo_fills_amount_then_saved(c, ai):
    jid = job_id(c)
    r = c.post(f"/api/jobs/{jid}/payslip/read",
               files={"file": ("명세서.png", (SAMPLE / "06_급여명세서.png").read_bytes(), "image/png")}).json()
    assert r["ai"] and r["month"] == "2026-09" and r["net_pay"] == 557280 and r["weekly_holiday_pay"] == 0
    c.post(f"/api/jobs/{jid}/payslip", data={"month": r["month"], "amount": str(r["net_pay"]),
                                              "evidence_id": str(r["evidence_id"])})
    assert c.get(f"/api/jobs/{jid}/payslips").json()[0]["evidence_id"] == r["evidence_id"]


def test_ai_judgment_is_cross_checked(c, ai):
    """AI가 모두 정상이라고 해도, 코드 계산으로 위반 의심인 항목(야간근로, 최저임금)은 확인 필요로 되돌린다."""
    items = c.post(f"/api/jobs/{job_id(c)}/check").json()["items"]
    night = [i for i in items if i["law"] == "근로기준법 제70조"][0]
    assert night["status"] == "warn" and night["rule_status"] in ("bad", "warn")
    assert any(i["status"] == "ok" and i.get("ai_reason") == "테스트" for i in items)
    assert "pending" not in {i["status"] for i in items}


def test_model_error_keeps_pending(c, monkeypatch):
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake-vl")

    def boom(payload):
        raise client.LLMError("AI 모델에 연결하지 못했어요 (ConnectError)")
    monkeypatch.setattr(client, "_post", boom)
    items = c.post(f"/api/jobs/{job_id(c)}/check").json()["items"]
    assert {i["status"] for i in items} <= {"pending", "warn"} and items[0].get("ai_error")


def test_parse_json_variants():
    assert client.parse_json('설명\n```json\n{"a": 1}\n```') == {"a": 1}
    assert client.parse_json('[{"i": 0}]') == [{"i": 0}]
    with pytest.raises(client.LLMError):
        client.parse_json("모르겠어요")
