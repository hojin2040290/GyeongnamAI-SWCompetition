"""증거 자료 저장 상태 점검: python -m app.evidence_check [--fix]

DB에 기록된 증거 원본과 상담 사전 자료가 저장 폴더(data 아래)에 실제로 있는지, 올린 뒤 바뀌지 않았는지(SHA-256)를 확인한다.
--fix를 붙이면 data 폴더 밖에 저장된 파일을 data 폴더로 복사하고(원본 파일은 그대로 둠), 복사본의 SHA-256이 같을 때만 기록의 경로를 바꾼다.
"""
import hashlib
import shutil
import sys
from pathlib import Path

from sqlmodel import Session, select

from app import storage
from app.config import DATA_DIR, DB_PATH, REPORT_DIR, UPLOAD_DIR
from app.db import engine, init_db
from app.models import Evidence, Report


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _copy_in(src: Path, folder: Path) -> Path:
    """data 폴더로 복사한다. 같은 이름이 있으면 덮어쓰지 않고 이름을 바꾼다."""
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / src.name
    n = 1
    while dst.exists():
        dst = folder / f"{src.stem}_{n}{src.suffix}"
        n += 1
    shutil.copy2(src, dst)
    return dst


def check_evidence(s: Session, fix: bool) -> dict:
    out = {"ok": 0, "moved": 0, "outside": [], "missing": [], "changed": []}
    for ev in s.exec(select(Evidence)).all():
        label = f"증거 {ev.id} ({ev.filename})"
        try:
            path = storage.locate(ev.stored_path)
        except FileNotFoundError:
            out["missing"].append(label)
            continue
        if _sha(path) != ev.sha256:
            out["changed"].append(label)
            continue
        if not _inside(path, UPLOAD_DIR):
            if not fix:
                out["outside"].append(f"{label}: {path}")
                continue
            dst = _copy_in(path, UPLOAD_DIR / str(ev.user_id))
            if _sha(dst) != ev.sha256:
                out["changed"].append(f"{label} (복사본이 달라 옮기지 않음)")
                dst.unlink()
                continue
            ev.stored_path = str(dst)
            s.add(ev)
            out["moved"] += 1
        elif str(path) != ev.stored_path:
            ev.stored_path = str(path)  # 폴더를 옮겨 경로만 달라진 기록은 지금 경로로 맞춘다
            s.add(ev)
        out["ok"] += 1
    s.commit()
    return out


def check_reports(s: Session, fix: bool) -> dict:
    out = {"ok": 0, "moved": 0, "outside": [], "missing": [], "changed": []}
    for rep in s.exec(select(Report)).all():
        label = f"상담 사전 자료 {rep.id}"
        try:
            path = storage.locate(rep.path, REPORT_DIR)
        except FileNotFoundError:
            out["missing"].append(label)
            continue
        if not _inside(path, REPORT_DIR):
            if not fix:
                out["outside"].append(f"{label}: {path}")
                continue
            rep.path = str(_copy_in(path, REPORT_DIR))
            s.add(rep)
            out["moved"] += 1
        out["ok"] += 1
    s.commit()
    return out


def main(argv: list[str]) -> int:
    fix = "--fix" in argv
    init_db()
    print(f"데이터 폴더: {DATA_DIR}\n  기록(DB): {DB_PATH}\n  증거 원본: {UPLOAD_DIR}\n  상담 사전 자료: {REPORT_DIR}")
    for p, name in ((DB_PATH, "기록(DB)"), (UPLOAD_DIR, "증거 원본"), (REPORT_DIR, "상담 사전 자료")):
        if not _inside(p, DATA_DIR):
            print(f"  주의: {name} 위치가 data 폴더 밖이에요 (.env의 경로 설정을 확인해 주세요)")
    bad = 0
    with Session(engine) as s:
        for title, r in (("증거 원본", check_evidence(s, fix)), ("상담 사전 자료", check_reports(s, fix))):
            print(f"\n[{title}] 정상 {r['ok']}건" + (f", data 폴더로 복사 {r['moved']}건" if r["moved"] else ""))
            for key, msg in (("outside", "data 폴더 밖에 있음 (--fix로 복사)"), ("missing", "파일 없음"),
                             ("changed", "올린 뒤 내용이 바뀜 (SHA-256 다름)")):
                for x in r[key]:
                    print(f"  {msg}: {x}")
                bad += len(r[key])
    print("\n모두 정상이에요." if not bad else f"\n확인할 것 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
