"""AI 모델(vLLM) 연결 테스트. 실제 모델 대신 응답만 흉내 내고, 사진 저장, 에이전트 흐름, 검증 장치는 실제로 돈다."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.llm import client
from app.main import app
from tests.fake_agent import FakeAgent, said, smart_policy

SAMPLE = Path(__file__).resolve().parent.parent / "테스트자료"

CONTRACT = {"임금": "시급 9,288원 (수습기간 중 최저임금의 90%)", "근로시간": "매주 월, 수, 금, 토 17시 00분 ~ 22시 30분",
            "휴게시간": "", "휴일": "매주 일요일", "연차휴가": "관계 법령에 따름",
            "근무장소": "경상남도 창원시 의창구 도계동 312-7", "업무내용": "편의점 계산, 상품 진열"}
PAYSLIP = {"month": "2026-09", "net_pay": "557,280원", "base_pay": "557,280", "weekly_holiday_pay": "0", "deduction": "0"}
# 채용공고: 모델이 칸에 맞지 않는 값(쓸 수 없는 글자, 없는 요일, 틀린 시각)을 섞어 내도 코드가 거른다
POSTING = {"name": "가상분식 시험점★", "industry": "음식점, 카페", "work_desc": "주문 받기, 서빙", "wage": "10,320원",
           "probation": "없음",
           "shifts": [{"days": ["월", "수", "금"], "start": "17:00", "end": "22:00", "brk": "30분"},
                      {"days": ["토요일"], "start": "10:00", "end": "16:00", "brk": ""},
                      {"days": ["토"], "start": "18:00", "end": "20:00", "brk": "없음"},
                      {"days": ["X"], "start": "25:00", "end": "9"}, "틀린 모양"]}


AGENT = FakeAgent(smart_policy)


def fake_post(payload: dict) -> dict:
    """vLLM의 /chat/completions 응답 흉내: 사진은 읽은 값을, 나머지는 에이전트의 도구 호출을 돌려준다."""
    content = payload["messages"][-1]["content"]
    if isinstance(content, list):  # 사진
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        text = content[0]["text"]
        answer = "```json\n" + json.dumps(PAYSLIP if "급여명세서" in text else POSTING if "채용공고" in text else CONTRACT,
                                          ensure_ascii=False) + "\n```"
        return {"choices": [{"message": {"role": "assistant", "content": answer}}]}
    return AGENT(payload)


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


def _notice(c) -> int:
    r = c.post("/api/evidence", files={"file": ("공고.png", (SAMPLE / "알바5개" / "2_채용공고.png").read_bytes(), "image/png")},
               data={"kind": "notice"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_posting_photo_without_model(c):
    r = c.post(f"/api/seek/read?evidence_id={_notice(c)}").json()
    assert r["ai"] is False and "연결되지 않았어요" in r["reason"] and r["found"] == 0


def test_posting_photo_read_into_seek_fields(c, ai):
    """지원 전 확인: 공고 사진을 AI가 읽고, 코드가 칸에 맞게 거른 값만 돌려준다 (업종 선택지, 시급 범위, 요일과 시각, 쓸 수 있는 글자)."""
    r = c.post(f"/api/seek/read?evidence_id={_notice(c)}").json()
    f = r["fields"]
    assert r["ai"] is True and r["found"] == 6 and r["total"] == 6
    assert f["name"] == "가상분식 시험점" and f["industry"] == "음식점, 카페" and f["work_desc"] == "주문 받기, 서빙"
    assert f["wage"] == 10320 and f["probation"] == "no"
    slot = {"start": "17:00", "end": "22:00", "brk": "30분"}
    assert f["schedule"] == {"월": slot, "수": slot, "금": slot,
                             "토": [{"start": "10:00", "end": "16:00", "brk": "모름"}, {"start": "18:00", "end": "20:00", "brk": "없음"}]}
    assert any(t["step"] == "도구 read_posting_image" for t in r["trace"])
    # 읽은 값을 그대로 지원 전 확인에 넣어도 서버 검사를 통과한다
    seek = c.post("/api/seek/check", json={k: f[k] for k in ("name", "industry", "work_desc", "wage", "probation", "schedule")})
    assert seek.status_code == 200, seek.text


def test_posting_read_only_own_notice(c, ai):
    """다른 사람의 공고 사진, 일하는 곳에 올린 자료(계약서)는 지원 전 확인으로 읽지 않는다."""
    contract = c.post(f"/api/jobs/{job_id(c)}/contract", files={"file": ("c.png", (SAMPLE / "02_근로계약서.png").read_bytes(), "image/png")},
                      data={"read": "false"}).json()["evidence"]["id"]
    assert c.post(f"/api/seek/read?evidence_id={contract}").status_code == 404
    other = TestClient(app)
    other.post("/api/auth/register", json={"email": "ai_other@example.com", "password": "test1234", "birth_date": "2009-05-20"})
    assert other.post(f"/api/seek/read?evidence_id={_notice(c)}").status_code == 404


def test_posting_odd_values_are_dropped(monkeypatch):
    """모델이 선택지에 없는 업종, 범위를 넘는 시급, 모르는 수습 표현을 내면 비워 둔다 (추측해 채우지 않음)."""
    from app import ocr
    monkeypatch.setattr(client, "read_image_json", lambda *a: {"name": "", "industry": "식당", "wage": "1,000,000,000",
                                                                "probation": "협의", "shifts": None})
    out = ocr.read_posting(b"", "image/png")
    assert out["fields"] == {"name": "", "industry": "", "work_desc": "", "wage": None, "probation": "", "schedule": {}}
    assert out["found"] == 0
    monkeypatch.setattr(client, "read_image_json", lambda *a: ["목록"])
    with pytest.raises(client.LLMError):
        ocr.read_posting(b"", "image/png")


def test_ai_judgment_is_cross_checked(c, ai):
    """AI가 모두 정상이라고 해도, 코드 계산으로 위반 의심인 항목(야간근로, 최저임금)은 확인 필요로 되돌린다."""
    r = c.post(f"/api/jobs/{job_id(c)}/check").json()
    assert r["ai_agent"] is True
    steps = [t["step"] for t in r["trace"]]
    assert steps.index("도구 check_rules") < steps.index("도구 get_article") < steps.index("AI 끝냄")
    items = r["items"]
    night = [i for i in items if i["law"] == "근로기준법 제70조"][0]
    assert night["status"] == "warn" and night["rule_status"] in ("bad", "warn")
    assert any(i["status"] == "ok" and i.get("ai_reason") == said("판단 이유") for i in items)
    assert "pending" not in {i["status"] for i in items}


def test_model_error_keeps_pending(c, monkeypatch):
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake-vl")

    def boom(payload):
        raise client.LLMError("AI 모델에 연결하지 못했어요 (ConnectError)")
    monkeypatch.setattr(client, "_post", boom)
    items = c.post(f"/api/jobs/{job_id(c)}/check").json()["items"]
    assert {i["status"] for i in items} <= {"pending", "warn"}
    assert all("ConnectError" in i["ai_error"] for i in items if i["status"] == "pending")


def test_guard_without_model_waits(c):
    jid = job_id(c)
    g = c.post(f"/api/jobs/{jid}/guard", json={"reported": True}).json()
    assert g["message_source"] == "waiting" and "제104조" in g["message"]
    assert any("AI 응답 대기 중" in t["detail"] for t in g["trace"])
    p = c.post(f"/api/jobs/{jid}/guard/posts", json={"url": "https://example.com/wait", "title": "신고한 알바 이야기"}).json()
    assert p["classify"]["judged"] == 0 and "AI 응답 대기 중" in p["classify"]["reason"]
    post = [x for x in c.get(f"/api/jobs/{jid}/guard").json()["posts"] if x["url"].endswith("/wait")][0]
    assert post["status"] == "pending" and post["ai_reason"] == ""
    rep = c.post(f"/api/jobs/{jid}/report").json()
    assert "AI 요약은 AI 응답 대기 중" in rep["trace"][-1]["detail"] and "완료" not in rep["trace"][-1]["detail"]
    assert "AI 문구는 AI 응답 대기 중" in g["trace"][-1]["detail"]
    html = c.get(rep["url"]).text
    assert "AI 요약" in html and "AI 응답 대기 중" in html
    c.post(f"/api/jobs/{jid}/guard", json={"reported": False})  # 다른 테스트의 매일 점검 수에 끼지 않도록


def test_guard_with_model(c, ai):
    jid = job_id(c)
    g = c.post(f"/api/jobs/{jid}/guard", json={"reported": True}).json()
    assert g["message_source"] == "ai" and g["message"] == said("보복 금지 안내 문구")
    c.put(f"/api/jobs/{jid}/guard/message", json={"message": "직접 고침"})
    assert c.get(f"/api/jobs/{jid}/guard").json()["message_source"] == "custom"
    g = c.put(f"/api/jobs/{jid}/guard/message", json={"message": ""}).json()
    assert g["message_source"] == "ai" and g["message"] == said("보복 금지 안내 문구")  # 되돌리면 AI 문구로
    p = c.post(f"/api/jobs/{jid}/guard/posts", json={"url": "https://example.com/ok", "title": "맛집 후기"}).json()
    assert p["classify"]["judged"] >= 1  # 전에 대기 중이던 게시물도 함께 판별
    posts = {x["url"]: x for x in c.get(f"/api/jobs/{jid}/guard").json()["posts"]}
    assert posts["https://example.com/wait"]["status"] == "suspect"
    assert posts["https://example.com/ok"]["status"] == "ok" and posts["https://example.com/ok"]["ai_reason"] == said("게시물 판별 근거")
    notes = c.get("/api/notifications").json()
    assert any(said("보복 의심 게시물 알림 제목") == n["title"] for n in notes)
    c.post(f"/api/jobs/{jid}/guard", json={"reported": False})


def test_report_summary_by_model(c, ai):
    r = c.post(f"/api/jobs/{job_id(c)}/report").json()
    assert r["summary_ai"] is True
    html = c.get(r["url"]).text
    assert said("상담 자료 요약") in html and said("물어볼 점") in html and "근로기준법 제70조" in html


def test_model_error_falls_back_to_waiting(c, monkeypatch):
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake-vl")

    def boom(payload):
        raise client.LLMError("AI 모델에 연결하지 못했어요 (ConnectError)")
    monkeypatch.setattr(client, "_post", boom)
    r = c.post(f"/api/jobs/{job_id(c)}/report").json()
    assert r["summary_ai"] is False
    assert "AI 응답 대기 중" in c.get(r["url"]).text


def test_parse_json_variants():
    assert client.parse_json('설명\n```json\n{"a": 1}\n```') == {"a": 1}
    assert client.parse_json('[{"i": 0}]') == [{"i": 0}]
    with pytest.raises(client.LLMError):
        client.parse_json("모르겠어요")


def test_llm_extra_body_is_added_to_requests(monkeypatch):
    """LLM_EXTRA_BODY(모델마다 필요한 요청 옵션, 예: 생각 모드 끄기)는 모든 AI 요청에 더해진다. 잘못된 JSON은 무시한다."""
    from app.llm import client
    sent = []
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "m")
    monkeypatch.setattr(client.httpx, "post", lambda url, json, headers, timeout: sent.append(json) or type(
        "R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"choices": [{"message": {"content": "{}"}}]}})())
    monkeypatch.setattr(client, "LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    client.chat([{"role": "user", "content": "안녕"}])
    assert sent[-1]["chat_template_kwargs"] == {"enable_thinking": False} and sent[-1]["model"] == "m"
    monkeypatch.setattr(client, "LLM_EXTRA_BODY", "{잘못된")
    client.chat([{"role": "user", "content": "안녕"}])
    assert "chat_template_kwargs" not in sent[-1]


def test_llm_max_tokens_limits_every_request(monkeypatch):
    """AI 답 길이 제한(LLM_MAX_TOKENS): 모델이 끝없이 써도 요청이 몇 분씩 걸리지 않게 모든 요청에 붙는다 (0이면 붙이지 않음)."""
    from app.llm import client
    sent = []
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "m")
    monkeypatch.setattr(client.httpx, "post", lambda url, json, headers, timeout: sent.append(json) or type(
        "R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"choices": [{"message": {"content": "{}"}}]}})())
    monkeypatch.setattr(client, "LLM_MAX_TOKENS", 4096)
    client.chat([{"role": "user", "content": "안녕"}])
    assert sent[-1]["max_tokens"] == 4096
    monkeypatch.setattr(client, "LLM_MAX_TOKENS", 0)
    client.chat([{"role": "user", "content": "안녕"}])
    assert "max_tokens" not in sent[-1]


def test_llm_sampling_overrides_request(monkeypatch):
    """LLM_SAMPLING(모델 공식 문서의 권장 생성 설정): 앱이 정한 temperature 0 위에 덮어쓴다. null은 그 값을 빼서
    vLLM이 모델의 generation_config.json 기본값을 쓰게 한다. 생성 설정이 아닌 칸(model, messages)은 바꾸지 않는다."""
    from app.llm import client
    sent = []
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "m")
    monkeypatch.setattr(client.httpx, "post", lambda url, json, headers, timeout: sent.append(json) or type(
        "R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"choices": [{"message": {"content": "{}"}}]}})())
    monkeypatch.setattr(client, "LLM_SAMPLING", '{"temperature": 0.6, "top_p": 0.95, "presence_penalty": 1.5, "model": "x"}')
    client.chat([{"role": "user", "content": "안녕"}])
    assert sent[-1]["temperature"] == 0.6 and sent[-1]["top_p"] == 0.95 and sent[-1]["presence_penalty"] == 1.5
    assert sent[-1]["model"] == "m"
    monkeypatch.setattr(client, "LLM_SAMPLING", '{"temperature": null}')
    client.chat([{"role": "user", "content": "안녕"}])
    assert "temperature" not in sent[-1]
    monkeypatch.setattr(client, "LLM_SAMPLING", "")
    client.chat([{"role": "user", "content": "안녕"}])
    assert sent[-1]["temperature"] == 0


def test_parse_json_ignores_reasoning_text():
    """생각 파서가 붙지 않은 모델은 생각 글이 답에 섞여 온다. 생각 글 속 괄호를 JSON으로 읽지 않는다."""
    assert client.parse_json('<think>예: {"임금": "모름"} 일까?</think>\n{"임금": "10320"}') == {"임금": "10320"}
    # Qwen 계열: 대화 틀이 <think>를 열어 두면 답에는 닫는 표시만 온다
    assert client.parse_json('{"x": 1} 이렇게 쓰면 되겠다</think>{"임금": "9288"}') == {"임금": "9288"}
    assert client.parse_json('[THINK]{"a": 0}[/THINK]{"a": 1}') == {"a": 1}
    assert client.parse_json('<|channel>thought\n{"a": 0}<channel|>{"a": 2}') == {"a": 2}
    # GLM: 답을 상자 표시로 감싼다
    assert client.parse_json('<|begin_of_box|>{"a": 3}<|end_of_box|>') == {"a": 3}


def test_chat_removes_reasoning_from_content(monkeypatch):
    """생각 글이 답(content)에 섞여 와도 앱이 쓰는 글에는 남지 않는다."""
    from app.llm import client
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "m")
    monkeypatch.setattr(client.httpx, "post", lambda url, json, headers, timeout: type(
        "R", (), {"raise_for_status": lambda self: None,
                  "json": lambda self: {"choices": [{"message": {"content": "<think>고민</think>안녕하세요"}}]}})())
    assert client.chat([{"role": "user", "content": "안녕"}])["content"] == "안녕하세요"
