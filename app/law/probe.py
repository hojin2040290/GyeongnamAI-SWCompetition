"""법제처 OPEN API 연결 확인. 신청한 API마다 목록을 부르고, 첫 항목으로 본문까지 불러 응답 구조를 출력한다.

사용법: .env에 LAW_OC를 넣고
    python -m app.law.probe
화면에 나온 내용은 law_probe_result.txt에도 저장된다 (OC 값은 가림, 이 파일은 GitHub에 올리지 않음).
target 이름 중 일부는 활용가이드로 확인 전이라, 응답이 오류면 가이드의 target 값으로 고친다.
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import httpx

from app.config import BASE_DIR, LAW_OC, _parse_line

BASE = "https://www.law.go.kr/DRF"
RESULT_FILE = BASE_DIR / "law_probe_result.txt"

# (이름, target, 검색어). 목록을 부른 뒤 첫 항목의 일련번호로 같은 target의 본문을 부른다.
CHECKS = [
    ("현행법령(시행일)", "eflaw", "근로기준법"),
    ("현행법령(공포일)", "law", "근로기준법"),
    ("행정규칙 (최저임금 고시)", "admrul", "최저임금"),
    ("법령 별표·서식", "licbyl", "근로기준법 시행령"),
    ("판례", "prec", "주휴수당"),
    ("법령해석례", "expc", "근로기준법"),
    ("고용노동부 법령해석", "moelCgmExpc", "주휴수당"),
    ("노동위원회 결정문", "nlrc", "부당해고"),
]
NO_BODY = {"licbyl"}  # 본문 XML이 없는 목록

out_lines: list[str] = []


def say(text: str = "") -> None:
    text = text.replace(LAW_OC, "***") if LAW_OC else text
    print(text)
    out_lines.append(text)


def parse(text: str):
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return None


def summarize(text: str) -> str:
    """XML이면 최상위 태그, 자주 나온 태그, 첫 항목의 값들을 보여 준다."""
    root = parse(text)
    if root is None:
        return "XML 아님: " + " ".join(text.split())[:300]
    tags = Counter(el.tag for el in root.iter())
    lines = [f"최상위 <{root.tag}>, 자주 나온 태그: {', '.join(f'{t}({n})' for t, n in tags.most_common(12))}"]
    if len(root) and all(len(el) == 0 for el in root):  # 오류 안내처럼 값만 있는 짧은 응답은 글자를 그대로 보여 준다
        lines += [f"    <{el.tag}> {' '.join((el.text or '').split())[:200]}" for el in root]
    first = next((el for el in root if len(el)), None)
    if first is not None:
        attrs = f" 속성 {first.attrib}" if first.attrib else ""
        lines.append(f"첫 <{first.tag}>{attrs} 안의 값:")
        for child in list(first)[:20]:
            val = " ".join("".join(child.itertext()).split())[:80]
            lines.append(f"    <{child.tag}> {val}")
    return "\n".join(lines)


def outline(text: str, limit: int = 70) -> str:
    """본문 구조: 태그 경로마다 나온 횟수와 첫 값 (조문, 판결 요지 등이 어느 태그에 있는지 확인용)."""
    root = parse(text)
    if root is None:
        return "XML 아님: " + " ".join(text.split())[:300]
    counts: Counter = Counter()
    sample: dict[str, str] = {}

    def walk(el, path):
        p = f"{path}/{el.tag}"
        counts[p] += 1
        val = " ".join((el.text or "").split())
        if val and p not in sample:
            sample[p] = val[:70]
        for ch in el:
            walk(ch, p)
    walk(root, "")
    lines = []
    for p, n in list(counts.items())[:limit]:
        lines.append(f"    {p} x{n}" + (f" = {sample[p]}" if p in sample else ""))
    if len(counts) > limit:
        lines.append(f"    ... 태그 경로 {len(counts) - limit}개 더 있음")
    return "\n".join(lines)


def find_law_id(text: str, name: str) -> str | None:
    root = parse(text)
    if root is None:
        return None
    for law in root.iter("law"):
        if (law.findtext("법령명한글") or "").strip() == name:
            return (law.findtext("법령ID") or "").strip() or None
    return None


def first_item_ids(text: str) -> dict[str, str]:
    """목록 첫 항목에서 번호로 보이는 값들 (…일련번호, …ID, id 속성)."""
    root = parse(text)
    first = next((el for el in root if len(el)), None) if root is not None else None
    if first is None:
        return {}
    ids = {k: v for k, v in first.attrib.items()}
    for ch in first:
        if ch.tag.endswith(("일련번호", "ID", "id")) and (ch.text or "").strip():
            ids[ch.tag] = ch.text.strip()
    return ids


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


def call(c: httpx.Client, path: str, params: dict) -> httpx.Response | None:
    say(f"  [{path} {' '.join(f'{k}={v}' for k, v in params.items())}]")
    try:
        r = c.get(f"{BASE}/{path}", params={"OC": LAW_OC, "type": "XML", **params})
    except httpx.HTTPError as exc:
        say(f"  연결 실패: {type(exc).__name__}")
        return None
    say(f"  응답 {r.status_code}, {r.headers.get('content-type', '')}, {len(r.content)}바이트")
    return r


def check(c: httpx.Client, name: str, target: str, query: str) -> None:
    say(f"\n=== {name} 목록")
    r = call(c, "lawSearch.do", {"target": target, "query": query})
    if r is None:
        return
    say(summarize(r.text))
    if target in NO_BODY:
        return
    if target in ("eflaw", "law"):
        law_id = find_law_id(r.text, "근로기준법")
        say(f"  근로기준법 법령 ID: {law_id}")
        ids = {"ID": law_id} if law_id else {}
    else:
        ids = first_item_ids(r.text)
    if not ids:
        say(f"=== {name} 본문: 건너뜀 (목록에서 번호를 찾지 못함)")
        return
    # 번호 이름이 API마다 달라 ID, MST 순서로 시도한다
    value = ids.get("ID") or next((v for k, v in ids.items() if k.endswith("일련번호")), next(iter(ids.values())))
    for key in ("ID", "MST"):
        say(f"\n=== {name} 본문 ({key}={value})")
        b = call(c, "lawService.do", {"target": target, key: value})
        if b is None:
            return
        say(outline(b.text))
        if parse(b.text) is not None and len(parse(b.text)) > 2:
            break
    if target == "eflaw" and ids:
        say(f"\n=== {name} 본문 중 제70조만 (JO=007000)")
        j = call(c, "lawService.do", {"target": target, "ID": value, "JO": "007000"})
        if j is not None:
            say(outline(j.text, limit=30))


def main() -> int:
    if not LAW_OC:
        print(missing_key_message())
        return 1
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        for name, target, query in CHECKS:
            check(c, name, target, query)
    RESULT_FILE.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"\n결과를 {RESULT_FILE.name} 파일에도 저장했어요. 이 파일 내용을 그대로 보내 주세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
