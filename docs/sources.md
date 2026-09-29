# 출처와 AI 활용 기록 (별지2 작성용)

## 법 기준값 출처 (data/law_params.json)
- 고용노동부 공식 블로그, 알바 구하기 전 꼭 알아야 할 노동법 10문10답, 2025.12.4. https://blog.naver.com/molab_suda/224098273869
- 국가법령정보센터 근로기준법 제104조 제2항, 제40조
- 최저임금법 제5조 제2항 (수습 감액 요건)
- 근로기준법 제36조 (금품 청산 14일), 상시 5인 미만 가산수당 미적용 (근로기준법 시행령 별표1)

## 외부 API
| 이름 | 용도 | 출처 |
|---|---|---|
| 법제처 국가법령정보 공동활용 OPEN API | 조문 원문 | https://open.law.go.kr |
| 네이버 검색 API | 공개 게시물 검색 | https://developers.naver.com |
| vLLM OpenAI 호환 API (/v1/chat/completions) | 조항 판단, 계약서와 급여명세서 사진 읽기 (비전 모델 하나가 글자 인식과 항목 정리를 함께 함) | https://docs.vllm.ai | 모델 이름과 라이선스는 선정 후 기록 |

## 라이브러리
fastapi, uvicorn, sqlmodel, itsdangerous, python-multipart, jinja2, apscheduler, httpx, pytest, (선택) playwright
설치 후 `pip freeze`로 버전과 라이선스를 이 표에 옮긴다.

## 글꼴
IBM Plex Sans KR (Google Fonts, SIL Open Font License)

## AI 활용
| 도구 | 사용한 곳 | 직접 수정한 부분 |
|---|---|---|
| Claude (claude.ai, Claude Code) | 초기 코드 구조, 화면 초안, 테스트 작성 | 개발하면서 기록 |
