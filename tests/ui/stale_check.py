"""part10용: AI 없이(LLM_FAKE=false) 계약서 점검을 저장해 'AI 응답 대기 중' 결과가 남은 DB를 만든다."""
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

init_db()
c = TestClient(app)
c.__enter__()
c.post("/api/auth/register", json={"email": "old@example.com", "password": "test1234", "birth_date": "2009-05-01"})
j = c.post("/api/jobs", json={"name": "가상분식", "wage": 9000, "start_date": "2026-08-01",
                             "schedule": {"월": {"start": "17:00", "end": "22:30", "brk": "없음"}}}).json()["id"]
print([i["status"] for i in c.post(f"/api/jobs/{j}/check").json()["items"]])
