# 모델 벤치마크 (GPU 서버에서 실행)

후보 모델 9개(`models.json`)를 GPU 서버에서 하나씩 받고, 켜고, 같은 시험을 돌리고, 끈 뒤 비교표(`report.md`)를 만든다.
시험은 앱 코드를 그대로 써서 실제 앱과 같은 요청을 보낸다 (`suite.py`).

## 재는 것
| 분류 | 지표 |
|---|---|
| 준비 | 모델 크기, 받은 시간, 켜는 시간, GPU 메모리 최대 |
| 자동 점검 | 켠 뒤 시험 전에: 생각 모드가 실제로 꺼졌는지, 도구 호출을 파서가 읽는지(auto, 지정), 사진을 JSON으로 답하는지 |
| 속도 | 첫 글자까지 시간(TTFT), 초당 생성 토큰, 요청 시간(중간값, 95%, 최대), 100초 넘은 요청 수 |
| 토큰 | 입력, 출력 토큰 합계, 흐름 하나당 토큰 |
| 도구 호출 | 도구 호출 없이 글만 온 수, 글 속 호출(파서가 못 읽음), 입력 형식 오류, 지정한 도구 무시 |
| 에이전트 | 시험 데이터(알바 5개)로 실제 흐름 13개: 끝까지 마침, 반복 10회 초과, 평균 반복, 흐름 시간, 검증 장치 돌려보냄, 도구 오류, 질문, 조문 질문 |
| 판단 | 코드 규칙이 분명한 항목에서 AI 판단이 같음 / 확인 필요로 조심 / 반대 |
| 사진 | 계약서 4장 x 7항목, 명세서 3장 x 5항목 정답과 맞은 수, 답을 JSON으로 읽지 못한 장 수와 그중 길이 제한에 걸린 수, 실패한 답 원문 앞뒤 |
| 한국어 | AI가 쓴 글의 한글 비율, 한자(중국어 섞임) 글자 수, 글 예시 |

## 실행 (GPU 서버 터미널)
1. 준비 (한 번만, 몇 분). `LAW_OC`에 법제처 키를 넣으면 법 기준표를 만들어 실제 앱과 같은 조건으로 시험한다.
   ```bash
   cd /home/work/llm_alba
   curl -sO https://raw.githubusercontent.com/hojin2040290/GyeongnamAI-SWCompetition/main/bench/setup.sh
   LAW_OC=법제처키 bash setup.sh
   ```
   Gemma, Llama처럼 라이선스 동의가 필요한 모델은 Hugging Face에서 동의한 뒤 `export HF_TOKEN=hf_...`를 해 둔다.
2. 확인만 (받지 않고, GPU를 쓰지 않음, 1~2분): 모델 이름이 있는지, 크기, 도구 파서를 본다.
   ```bash
   cd /home/work/llm_alba/app
   /home/work/llm_alba/venv/bin/python bench/run.py --preflight
   ```
   마지막 줄의 `report.md`를 열어 '건너뜀'인 모델과 이유를 본다.
3. 전부 실행 (몇 시간, 터미널을 닫아도 계속):
   ```bash
   cd /home/work/llm_alba/app
   nohup /home/work/llm_alba/venv/bin/python bench/run.py --yes > bench/nohup.log 2>&1 &
   tail -f bench/nohup.log
   ```
   - 시작할 때 앱이 쓰는 8000번 vLLM을 끄고(vllm.pid의 번호로만), 끝나거나 중간에 멈춰도 `start_vllm.sh`로 다시 켠다. 벤치마크 동안 웹 앱의 AI는 멈춘다.
   - 일부만: `--models hcx_seed_32b_think,exaone45_33b` (key는 `models.json`)
   - 중간에 멈추기: `kill <nohup 실행 때 나온 번호>` (끝 정리로 8000번을 다시 켠다)
4. 결과: `bench/results/<연월일_시분초>/report.md` (모델별 JSON, vLLM 로그, 진행 로그도 같은 폴더). 사전 확인은 `<시각>_preflight` 폴더에 따로 남는다.
5. 중간에 멈췄으면 이어서 하기 (완료된 모델은 건너뜀, 결과는 단계마다 저장돼 있음):
   ```bash
   cd /home/work/llm_alba/app
   nohup /home/work/llm_alba/venv/bin/python bench/run.py --yes --resume bench/results/<폴더 이름> > bench/nohup.log 2>&1 &
   ```

## 모델마다 다른 점에 대한 대비
`models.json`에 모델마다 공식 문서(vLLM 레시피, 모델 회사 GitHub)에서 확인한 값과 그 주소(`docs`)를 적었다. 실행할 때는 다음 순서로 확인하고 고친다.
1. 버전: GPU 서버의 vLLM 가상환경에 있는 vllm, transformers, mistral_common이 공식 문서의 최소 버전(`requires`)보다 낮으면 설치 명령을 남기고 건너뛴다. 그래도 돌리려면 `--ignore-versions`.
2. 문서: 모델의 README, generation_config.json, 대화 틀을 결과 폴더 `docs/<key>/`에 남기고, README의 vllm serve 예시(파서), 권장 생성 설정 줄, 대화 틀의 생각 끄기 이름을 비교표에 적는다.
3. 대화 틀: Gemma 4, Llama 4처럼 vLLM이 주는 도구 호출용 대화 틀이 필요한 모델은 설치된 vLLM 버전의 GitHub 파일을 받아 `--chat-template`로 쓴다.
4. 켜기: 도구 파서 후보(`tool_parsers`)를 차례로 쓴다. 켜지지 않거나 자동 점검에서 도구 호출을 못 읽으면 다음 후보로 다시 켠다.
5. 생각 모드: 끄는 옵션(`extra_body`)을 보냈는데도 생각 글이 오면 대화 틀에서 찾은 다른 끄기 옵션(enable_thinking, thinking, skip_reasoning, reasoning_effort 등)을 차례로 시도하고, 꺼진 옵션으로 시험한다. 끝내 못 끄면 비교표에 '못 끔'으로 남긴다.
6. 생성 설정: 공식 문서의 권장값(`sampling`, 예: EXAONE은 temperature 0.6, presence_penalty 1.5)을 모든 요청에 쓴다. 문서에서 확인하지 못한 모델은 모델 기본값(generation_config.json)을 쓴다. 앱에서 그 모델을 쓰려면 `.env`의 `LLM_SAMPLING`에 같은 값을 넣는다.
7. 답 모양: 생각 글(`<think>`, `[THINK]`, Gemma thought 채널)이나 GLM 답 상자 표시가 답에 섞여 와도 앱이 지우고 JSON을 읽는다.
- GPU 없이 이 대처를 확인하는 가짜 서버 옵션: `--fail-parser`(켜지지 않음), `--working-parser`(파서 불일치), `--think-unless`(생각 글 섞임), `--broken-vision`(사진 답 반복), `--record`(보낸 요청 기록). 예:
  ```bash
  python3 bench/run.py --yes --serve-cmd "python3 bench/fake_server.py --port {port} --tool-parser {parser} --fail-parser bad --working-parser good --think-unless skip_reasoning" --fake-parsers bad,hermes2,good --suite-python python3
  ```

## 알아 둘 점
- 모델 이름과 vLLM 옵션은 공식 문서(`models.json`의 docs)에서 확인한 값이다. 사전 확인에서 이름이 없거나 GPU보다 큰 모델은 받지 않고 건너뛴다.
  도구 파서와 생각 파서는 공식 문서 값을 쓰고, `auto`인 모델(공식 문서를 Hugging Face에서만 볼 수 있는 모델)은 README의 `vllm serve` 예시에서 찾는다.
- AI 답 하나는 최대 4096토큰까지만 받는다 (앱과 같은 `LLM_MAX_TOKENS`). 모델이 같은 말을 반복하거나 끝맺지 못해도 요청이 몇 분씩 걸리지 않게 하려는 것이고, 제한에 걸린 요청 수는 비교표의 '길이 제한 걸림'에 남는다.
- 사진 읽기에 실패하면 그 답 원문 앞뒤가 모델별 JSON과 비교표 '모델별 자세히'에 남는다. 한 모델의 사진 시험만 다시 하려면
  (예: EXAONE, 끝나면 8000번을 다시 켬):
  ```bash
  cd /home/work/llm_alba/app
  nohup /home/work/llm_alba/venv/bin/python bench/run.py --yes --models exaone45_33b --skip speed,flows > bench/nohup_vision.log 2>&1 &
  ```
  결과는 새 폴더에 따로 생기고, 전체 실행 폴더는 바뀌지 않는다. 전체 실행이 끝난 뒤에 한다 (GPU를 함께 쓰면 둘 다 멈춘다).
- 생각(thinking) 모드가 있는 모델은 끄고 시험한다. 앱에서 그 모델을 쓰려면 비교표 '모델별 자세히'의 '실제로 쓴 요청 옵션'을 `.env`의 `LLM_EXTRA_BODY`에, 생성 설정을 `LLM_SAMPLING`에 넣는다.
- 받은 모델은 `/home/work/llm_alba/hf`(저장 폴더)에 남는다. 다 받으면 수백 GB라 끝난 뒤 쓰지 않을 모델은 지워도 된다.
- 지금 vLLM 버전이 지원하지 않는 모델은 '실패'와 vLLM 로그 끝부분이 비교표에 남는다.
- GPU 없이 흐름만 확인 (개발자용): `python bench/run.py --serve-cmd "python3 bench/fake_server.py --port {port}" --suite-python python3`. 사진 읽기 실패 모양을 보려면 `fake_server.py`에 `--broken-vision`을 더한다.
