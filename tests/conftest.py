"""테스트는 임시 폴더의 DB와 파일만 쓴다 (data/app.db를 건드리지 않음)."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="albajikimi_test_")
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
os.environ["REPORT_DIR"] = os.path.join(_tmp, "reports")
os.environ["NAVER_CLIENT_ID"] = ""
os.environ["NAVER_CLIENT_SECRET"] = ""
os.environ["LLM_FAKE"] = "false"  # 테스트는 AI가 없을 때(대기)와 가짜 AI를 각각 따로 켜서 확인한다
os.environ["LLM_ENABLED"] = "false"
os.environ["LLM_FAKE_DELAY"] = "0"  # 테스트에서는 가짜 AI가 기다리지 않는다
os.environ["LAW_REFRESH_ON_START"] = "false"  # 테스트에서는 켤 때 법제처에 접속하지 않는다 (켤 때 확인은 test_flow에서)
os.environ["DEV_TOOLS"] = "true"  # 시연용 주소 테스트 (끈 경우는 test_flow에서 따로 확인)
