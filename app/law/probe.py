"""법제처 OPEN API 연결 확인. 신청한 API마다 한 번씩 불러 응답 모양만 출력한다.

사용법: .env에 LAW_OC를 넣고
    python -m app.law.probe
결과를 그대로 복사해 개발에 쓴다. OC 값은 출력에서 가린다.
target 이름 중 일부는 활용가이드로 확인 전이라, 응답이 오류면 가이드의 target 값으로 고친다.
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import httpx

from app.config import BASE_DIR, LAW_OC, _parse_line

BASE = "https://www.law.go.kr/DRF"

# (이름, 주소, 요청 변수). {law_id}는 앞의 목록 조회에서 찾은 법령 ID로 채운다.
CHECKS = [
    ("현행법령(시행일) 목록", "lawSearch.do", {"target": "eflaw", "query": "근로기준법"}),
    ("현행법령(공포일) 목록", "lawSearch.do", {"target": "law", "query": "근로기준법"}),
    ("현행법령(시행일) 본문", "lawService.do", {"target": "eflaw", "ID": "{law_id}"}),
    ("현행법령 본문 조항 (제70조)", "lawService.do", {"target": "eflaw", "ID": "{law_id}", "JO": "007000"}),
    ("행정규칙 목록 (최저임금 고시)", "lawSearch.do", {"target": "admrul", "query": "최저임금"}),
    ("법령 별표·서식 목록", "lawSearch.do", {"target": "licbyl", "query": "근로기준법"}),
    ("판례 목록", "lawSearch.do", {"target": "prec", "query": "주휴수당"}),
    ("법령해석례 목록", "lawSearch.do", {"target": "expc", "query": "근로기준법"}),
    ("고용노동부 법령해석 목록", "lawSearch.do", {"target": "moelCgmExpc", "query": "주휴수당"}),
    ("노동위원회 결정문 목록", "lawSearch.do", {"target": "nlrc", "query": "부당해고"}),
]


def summarize(text: str) -> str:
    """XML이면 최상위 태그, 자주 나온 태그, 첫 항목의 값들을 보여 준다."""
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return "XML 아님: " + " ".join(text.split())[:300]
    tags = Counter(el.tag for el in root.iter())
    lines = [f"최상위 <{root.tag}>, 자주 나온 태그: {', '.join(f'{t}({n})' for t, n in tags.most_common(12))}"]
    if len(root) and all(len(el) == 0 for el in root):  # 오류 안내처럼 값만 있는 짧은 응답은 글자를 그대로 보여 준다
        lines += [f"    <{el.tag}> {' '.join((el.text or '').split())[:200]}" for el in root]
    first = next((el for el in root if len(el)), None)
    if first is not None:
        lines.append(f"첫 <{first.tag}> 안의 값:")
        for child in list(first)[:14]:
            val = " ".join("".join(child.itertext()).split())[:80]
            lines.append(f"    <{child.tag}> {val}")
    return "\n".join(lines)


def find_law_id(text: str, name: str) -> str | None:
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return None
    for law in root.iter("law"):
        if (law.findtext("법령명한글") or "").strip() == name:
            return (law.findtext("법령ID") or "").strip() or None
    return None


def missing_key_message() -> str:
    """키를 못 찾았을 때 흔한 실수를 짚어 준다 (키 값은 출력하지 않음)."""
    env, example = BASE_DIR / ".env", BASE_DIR / ".env.example"
    if not env.exists():
        msg = f".env 파일이 없어요 ({env}). "
        lines = example.read_text(encoding="utf-8-sig").splitlines() if example.exists() else []
        if any((kv := _parse_line(line)) and kv[0] == "LAW_OC" and kv[1] for line in lines):
            msg += ("키가 .env.example에 들어 있어요. .env.example은 GitHub에 올라가는 파일이라 위험해요.\n"
                    "  cp .env.example .env   (키가 든 내용을 .env로 복사)\n"
                    "  git checkout -- .env.example   (.env.example은 원래대로)")
        else:
            msg += "cp .env.example .env 로 만든 뒤 LAW_OC=인증키 를 넣어 주세요."
        return msg
    return ".env에서 LAW_OC 값을 찾지 못했어요. LAW_OC=인증키 형태의 줄이 있는지, 파일을 저장했는지 확인해 주세요."


def main() -> int:
    if not LAW_OC:
        print(missing_key_message())
        return 1
    law_id = None
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        for name, path, params in CHECKS:
            p = {k: (v.format(law_id=law_id) if isinstance(v, str) else v) for k, v in params.items()}
            if "{law_id}" in str(params) and not law_id:
                print(f"\n=== {name}: 건너뜀 (법령 ID를 찾지 못함)")
                continue
            print(f"\n=== {name}  [{path} {' '.join(f'{k}={v}' for k, v in p.items())}]")
            try:
                r = c.get(f"{BASE}/{path}", params={"OC": LAW_OC, "type": "XML", **p})
            except httpx.HTTPError as exc:
                print(f"연결 실패: {type(exc).__name__}")
                continue
            print(f"응답 {r.status_code}, {r.headers.get('content-type', '')}, {len(r.content)}바이트")
            print(summarize(r.text).replace(LAW_OC, "***"))
            if path == "lawSearch.do" and params["target"] in ("eflaw", "law") and not law_id:
                law_id = find_law_id(r.text, "근로기준법")
                print(f"근로기준법 법령 ID: {law_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
