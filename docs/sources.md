# 출처와 AI 활용 기록 (별지2 작성용)

## 법 기준값 출처 (data/law_params.json)
- 고용노동부 공식 블로그, 알바 구하기 전 꼭 알아야 할 노동법 10문10답, 2025.12.4. https://blog.naver.com/molab_suda/224098273869
- 국가법령정보센터 근로기준법 제104조 제2항, 제40조
- 최저임금법 제5조 제2항 (수습 감액 요건)
- 근로기준법 제36조 (금품 청산 14일), 상시 5인 미만 가산수당 미적용 (근로기준법 시행령 별표1)

## 외부 API
| 이름 | 용도 | 출처 | 비고 |
|---|---|---|---|
| 법제처 국가법령정보 공동활용 OPEN API | 현행법령(시행일) 조문과 별표, 최저임금 고시(행정규칙), 판례, 법제처 법령해석례, 고용노동부 법령해석, 노동위원회 결정문 | https://open.law.go.kr | 이용 신청 필요 (등록한 IP에서만 호출) |
| 네이버 검색 API | 공개 게시물 검색 | https://developers.naver.com | 이용 신청 필요 |
| vLLM OpenAI 호환 API (/v1/chat/completions), vLLM 0.30.0 | 조항 판단, 도구 호출(tool calling), 계약서와 급여명세서 사진 읽기 | https://docs.vllm.ai | Apache 2.0 |
| AI 모델: Qwen3.8-27B (`Qwen/Qwen3.8-27B`, 55.6GB) | 위 모든 AI 작업. 후보 9개 비교(bench/)로 선정. 실행: `deploy/start_vllm.sh` | https://huggingface.co/Qwen/Qwen3.8-27B, https://github.com/QwenLM/Qwen3.8 | 모델 카드의 라이선스 (확인 후 기록) |

## 라이브러리
| 이름 | 용도 | 라이선스 |
|---|---|---|
| FastAPI | 웹 서버와 API | MIT |
| Uvicorn | 웹 서버 실행 | BSD-3-Clause |
| SQLModel | SQLite 테이블과 조회 | MIT |
| itsdangerous | 로그인 쿠키 서명 | BSD-3-Clause |
| python-multipart | 파일 올리기 | Apache-2.0 |
| Jinja2 | 화면 틀 | BSD-3-Clause |
| APScheduler (4 미만) | 매일 자동 점검, AI 대기 작업 재시도 | MIT |
| httpx | 법제처, 네이버, vLLM 호출 | BSD-3-Clause |
| pytest | 테스트 | MIT |
| Playwright (선택) | 게시물 화면 캡처 보존, 화면 회귀 시험(tests/ui, Node.js 판), 아이폰·갤럭시 크기 화면 캡처(tests/ui/device_shots.js) | Apache-2.0 |

설치한 버전은 `pip freeze`로 확인해 제출 전에 이 표에 옮긴다.

## 도구
| 이름 | 용도 | 출처 | 라이선스 |
|---|---|---|---|
| cloudflared (Cloudflare Tunnel) | 시연 때 내 컴퓨터의 서버를 https 주소로 외부에 열기 | https://github.com/cloudflare/cloudflared | Apache-2.0 |

## 글꼴
IBM Plex Sans KR (Google Fonts, SIL Open Font License). 화면 캡처 도구(tests/ui/fetch_fonts.sh)도 같은 글꼴을 받아 쓴다 (저장소에는 올리지 않음)

## 테스트 자료
`테스트자료/`의 계약서, 명세서, 입금 내역, 메시지 캡처, 채용공고 그림은 가상 인물과 가게로 직접 만든 것이다 (알바 사례 사진은 `tests/ui/make_case_images.js`로 시험 데이터의 계산 값과 같게 그림. `예시/채용공고_가상카페.png`는 가상카페 사례 조건으로 같은 방식의 HTML을 캡처해 만듦, 스크립트는 저장소에 없음). 실존 인물이나 가게가 아니다.

## AI 활용
| 도구 | 사용한 곳 | 직접 수정한 부분 |
|---|---|---|
| Claude (claude.ai, Claude Code) | 초기 코드 구조, 화면 초안, 테스트 작성, 에이전트 반복 구조(계획, 검증 결과로 다시 판단, 질문, 후속 일정, 메모와 조언), 입력 검사와 프롬프트 인젝션 대비, 가짜 AI(시험용), 증거 저장 위치 점검, 실행 가이드 작성, 홈 화면 미니멀 디자인 시안 3종(Claude 디자인 캔버스)과 적용, 처음 쓰는 사람 체험 안내, 데모 모드, 기기 크기 캡처 도구 | 개발하면서 기록 (직접 정한 요구사항과 고친 부분을 적는다) |

## 제출 전에 채울 것
- [ ] 쓴 AI 모델 이름, 크기, 라이선스 (vLLM에 띄운 모델)
- [ ] `pip freeze` 결과의 라이브러리 버전
- [ ] AI 활용 표의 '직접 수정한 부분' (요구사항을 정하고 결과를 확인해 고친 내용: 예: 월급날 말일, 쉬는 시간 직접 입력, 이메일 검사 누락 지적, 로그인 상태의 단계 건너뜀 발견)
- [ ] law_params.json의 기준값 출처를 원문과 다시 대조 (verified=false 항목)

## 모델 벤치마크 (bench/, 앱 모델 선정에 씀)
| 이름 | 용도 | 출처 | 라이선스 |
|---|---|---|---|
| huggingface_hub | 벤치마크 후보 모델 확인과 받기 (GPU 서버의 vLLM 가상환경에 이미 있음) | https://github.com/huggingface/huggingface_hub | Apache 2.0 |
| 후보 모델 9개 (`bench/models.json`) | 같은 시험으로 속도, 도구 호출, 판단, 사진 읽기, 한국어 비교 | Hugging Face 각 모델 페이지 | 모델마다 다름 (벤치마크 결과의 사전 확인에 라이선스를 남김. EXAONE은 비상업용) |
| vLLM Recipes (모델별 실행 방법) | 후보 모델의 실행 옵션, 도구·생각 파서, 생각 끄기 방법, 최소 버전 (`bench/models.json`의 docs) | https://github.com/vllm-project/recipes | Apache 2.0 |
| vLLM 도구 호출 문서와 대화 틀 (v0.30.0) | Llama 4, Gemma 4 도구 호출 파서와 대화 틀 파일 (실행할 때 설치된 vLLM 버전의 파일을 받음) | https://github.com/vllm-project/vllm/blob/v0.30.0/docs/features/tool_calling.md | Apache 2.0 |
| 모델 공식 문서 (EXAONE 4.5, Qwen3-VL, Qwen3.8, GLM) | 권장 생성 설정(temperature 등), 생각 끄기, 실행 옵션 | https://github.com/LG-AI-EXAONE/EXAONE-4.5, https://github.com/QwenLM/Qwen3-VL, https://github.com/QwenLM/Qwen3.8, https://github.com/zai-org/GLM-4.5 | 문서마다 다름 (참고만, 코드 복사 없음) |
