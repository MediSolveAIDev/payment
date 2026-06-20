#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 테스트케이스 실행 & HTML 리포트 생성기.

각 QA 케이스(qa_cases.CASES)를 저장소의 실제 pytest 테스트에 매핑해 실행하고,
결과를 사람이 보기 좋은 단일 HTML 리포트로 만든다.

사용법 (저장소 루트에서 실행):
  # 사전: docker compose up -d  +  uv sync  (DB/Redis 필요)
  uv run python docs/manual/qa/run_qa.py            # pytest 실행 → 리포트
  python  docs/manual/qa/run_qa.py --from-xml r.xml # 기존 junit 결과로 리포트만
  python  docs/manual/qa/run_qa.py --demo           # 모의 결과로 미리보기

출력: docs/manual/qa/qa-report.html

표준 라이브러리만 사용한다(추가 설치 불필요). pytest 실행만 프로젝트 환경(py3.13)이 필요하며,
리포트 생성/파싱은 어떤 파이썬에서도 동작한다.
"""
import argparse
import datetime
import html as _html
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_cases import MODULES, CASES  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]               # docs/manual/qa → 저장소 루트
DEFAULT_XML = HERE / ".qa-results.xml"
OUT_HTML = HERE / "qa-report.html"

STATUS_LABEL = {
    "PASS": "합격", "FAIL": "불합격", "SKIP": "건너뜀",
    "N/A": "미발견", "PARTIAL": "부분", "MANUAL": "수동",
}
STATUS_COLOR = {
    "PASS": "#1FA971", "FAIL": "#FF4E51", "SKIP": "#9F9F9F",
    "N/A": "#AC47FF", "PARTIAL": "#FF8064", "MANUAL": "#476CFF",
}


# ── pytest 실행 ──────────────────────────────────────────────────────────
def run_pytest(xml_path: Path) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header",
           f"--junitxml={xml_path}"]
    print("▶ 실행:", " ".join(cmd), "  (cwd=%s)" % ROOT)
    try:
        return subprocess.run(cmd, cwd=str(ROOT)).returncode
    except FileNotFoundError:
        print("✗ pytest를 찾을 수 없습니다. 'uv run python ...' 로 실행하거나 의존성을 설치하세요.")
        return 127


# ── junit 결과 파싱 ──────────────────────────────────────────────────────
def parse_junit(xml_path: Path) -> dict:
    """junit xml → {함수명(파라미터 제외): 'PASS'|'FAIL'|'SKIP'}"""
    res: dict[str, str] = {}
    root = ET.parse(str(xml_path)).getroot()
    for tc in root.iter("testcase"):
        name = tc.get("name") or ""
        base = name.split("[")[0]              # 파라미터화 제거
        status = "PASS"
        for child in tc:
            if child.tag in ("failure", "error"):
                status = "FAIL"
            elif child.tag == "skipped":
                status = "SKIP"
        prev = res.get(base)
        # 같은 함수의 여러 파라미터: 하나라도 FAIL이면 FAIL 우선
        if prev == "FAIL" or status == "FAIL":
            res[base] = "FAIL"
        elif prev is None:
            res[base] = status
        elif prev == "SKIP" and status == "PASS":
            res[base] = "PASS"
    return res


# ── 케이스별 결과 집계 ───────────────────────────────────────────────────
def aggregate(results: dict) -> list:
    rows = []
    for cid, prio, title, tests in CASES:
        if not tests:
            rows.append(dict(id=cid, prio=prio, title=title, status="MANUAL", tests=[]))
            continue
        per = [(t, results.get(t, "N/A")) for t in tests]
        sset = {s for _, s in per}
        if "FAIL" in sset:
            st = "FAIL"
        elif sset == {"N/A"}:
            st = "N/A"
        elif "N/A" in sset and ("PASS" in sset or "SKIP" in sset):
            st = "PARTIAL"
        elif sset == {"SKIP"}:
            st = "SKIP"
        else:
            st = "PASS"
        rows.append(dict(id=cid, prio=prio, title=title, status=st, tests=per))
    return rows


def summarize(rows: list) -> dict:
    counts = {k: 0 for k in STATUS_LABEL}
    prio = {"P1": {"total": 0, "pass": 0}, "P2": {"total": 0, "pass": 0}, "P3": {"total": 0, "pass": 0}}
    for r in rows:
        counts[r["status"]] += 1
        p = prio[r["prio"]]
        p["total"] += 1
        if r["status"] == "PASS":
            p["pass"] += 1
    executed = counts["PASS"] + counts["FAIL"]          # 자동 실행되어 판정된 건
    rate = (counts["PASS"] / executed * 100) if executed else 0.0
    return dict(counts=counts, prio=prio, executed=executed, rate=rate, total=len(rows))


# ── HTML 렌더 ────────────────────────────────────────────────────────────
def _esc(s) -> str:
    return _html.escape(str(s))


def _badge(status: str) -> str:
    return (f'<span class="st" style="background:{STATUS_COLOR[status]}">'
            f'{STATUS_LABEL[status]}</span>')


def render(rows: list, meta: dict) -> str:
    s = summarize(rows)
    c = s["counts"]
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 모듈별 그룹
    by_mod: dict[str, list] = {k: [] for k in MODULES}
    for r in rows:
        by_mod[r["id"][0]].append(r)

    def card(label, val, color):
        return (f'<div class="sumcard"><div class="v" style="color:{color}">{val}</div>'
                f'<div class="l">{label}</div></div>')

    mod_html = []
    for letter, title in MODULES.items():
        items = by_mod[letter]
        mc = {k: 0 for k in STATUS_LABEL}
        for r in items:
            mc[r["status"]] += 1
        chip = []
        for st in ("PASS", "FAIL", "PARTIAL", "N/A", "SKIP", "MANUAL"):
            if mc[st]:
                chip.append(f'<span class="chip" style="background:{STATUS_COLOR[st]}">'
                            f'{STATUS_LABEL[st]} {mc[st]}</span>')
        rows_html = []
        for r in items:
            tlist = ", ".join(
                f'<span title="{_esc(st)}" style="color:{STATUS_COLOR.get(st,"#6E6E6E")}">{_esc(t)}</span>'
                for t, st in r["tests"]) if r["tests"] else '<span class="muted">수동 확인 항목</span>'
            rows_html.append(
                f'<tr class="r-{r["status"]}">'
                f'<td class="id">{_esc(r["id"])}</td>'
                f'<td><span class="p p{r["prio"][1]}">{r["prio"]}</span></td>'
                f'<td>{_esc(r["title"])}</td>'
                f'<td>{_badge(r["status"])}</td>'
                f'<td class="tests">{tlist}</td></tr>')
        open_attr = " open" if mc["FAIL"] or mc["N/A"] else ""
        mod_html.append(
            f'<details class="mod"{open_attr}><summary>'
            f'<span class="ml">{letter}</span> {_esc(title)} '
            f'<span class="mcount">{len(items)}건</span> {"".join(chip)}'
            f'</summary>'
            f'<div class="table-wrap"><table>'
            f'<thead><tr><th>ID</th><th>P</th><th>케이스</th><th>결과</th><th>매핑된 자동화 테스트</th></tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div></details>')

    p1 = s["prio"]["P1"]
    p1rate = (p1["pass"] / p1["total"] * 100) if p1["total"] else 0

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QA 테스트 결과 리포트 — 구독/결제 서버</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<style>
  :root {{ --primary:#476CFF; --border:#E3E3E3; --g100:#FBFBFB; --g700:#6E6E6E; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:'Pretendard',-apple-system,sans-serif; margin:0; background:var(--g100); color:#222; line-height:1.6; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:32px 24px 80px; }}
  h1 {{ font-size:26px; margin:0 0 4px; }}
  .sub {{ color:var(--g700); font-size:14px; margin-bottom:24px; }}
  .meta {{ font-size:13px; color:var(--g700); margin:6px 0 0; }}
  .meta code {{ background:#eef; padding:1px 6px; border-radius:5px; color:#2c3e8c; }}
  .banner {{ border-radius:14px; padding:18px 22px; color:#fff; margin:18px 0 22px; font-weight:600; font-size:18px; }}
  .summary {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:18px 0; }}
  .sumcard {{ background:#fff; border:1px solid var(--border); border-radius:12px; padding:16px; text-align:center; }}
  .sumcard .v {{ font-size:26px; font-weight:800; }}
  .sumcard .l {{ font-size:12.5px; color:var(--g700); margin-top:2px; }}
  .prio {{ display:flex; gap:12px; flex-wrap:wrap; margin:8px 0 24px; }}
  .priocard {{ flex:1; min-width:200px; background:#fff; border:1px solid var(--border); border-radius:12px; padding:14px 16px; }}
  .bar {{ height:9px; border-radius:99px; background:#eee; overflow:hidden; margin-top:8px; }}
  .bar > i {{ display:block; height:100%; background:var(--primary); }}
  details.mod {{ background:#fff; border:1px solid var(--border); border-radius:12px; margin:12px 0; overflow:hidden; }}
  details.mod > summary {{ cursor:pointer; padding:14px 18px; font-weight:700; font-size:15px; list-style:none; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  details.mod > summary::-webkit-details-marker {{ display:none; }}
  .ml {{ width:26px; height:26px; border-radius:7px; background:var(--primary); color:#fff; display:inline-flex; align-items:center; justify-content:center; font-weight:800; }}
  .mcount {{ font-weight:500; color:var(--g700); font-size:13px; }}
  .chip {{ color:#fff; font-size:11px; font-weight:700; border-radius:99px; padding:2px 9px; }}
  .table-wrap {{ overflow-x:auto; border-top:1px solid var(--border); }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th,td {{ padding:9px 12px; text-align:left; border-bottom:1px solid #f0f0f0; vertical-align:top; }}
  th {{ background:#fafafe; color:var(--g700); position:sticky; top:0; }}
  td.id {{ font-family:ui-monospace,Menlo,monospace; font-weight:700; color:var(--primary); white-space:nowrap; }}
  td.tests {{ font-family:ui-monospace,Menlo,monospace; font-size:11.5px; color:#888; }}
  tr.r-FAIL {{ background:#FFF5F5; }}
  tr.r-N\\/A {{ background:#FBF5FF; }}
  .st {{ color:#fff; font-size:12px; font-weight:700; border-radius:6px; padding:2px 10px; white-space:nowrap; }}
  .p {{ color:#fff; font-size:11px; font-weight:700; border-radius:5px; padding:1px 7px; }}
  .p1 {{ background:#FF4E51; }} .p2 {{ background:#FF8064; }} .p3 {{ background:#9F9F9F; }}
  .muted {{ color:#aaa; }}
  .note {{ background:#F0F4FF; border:1px solid #DDE6FF; border-radius:10px; padding:12px 16px; font-size:13px; color:#2c3e8c; margin:22px 0; }}
  .legend {{ font-size:12.5px; color:var(--g700); margin:10px 0; }}
  .legend .st {{ font-size:11px; }}
</style></head>
<body><div class="wrap">
  <h1>QA 테스트 결과 리포트</h1>
  <div class="sub">구독/결제 서버 · 매뉴얼 10장 테스트케이스 자동 실행 결과</div>
  <div class="meta">생성 시각 <b>{gen}</b> · 실행 모드 <code>{_esc(meta.get('mode',''))}</code> · 결과 소스 <code>{_esc(meta.get('source',''))}</code></div>

  <div class="banner" style="background:{'#1FA971' if c['FAIL']==0 else '#FF4E51'}">
    {'✅ 불합격 0건 — 전체 통과' if c['FAIL']==0 else f"❌ 불합격 {c['FAIL']}건 — 확인 필요"}
    &nbsp;·&nbsp; 자동 실행 합격률 {s['rate']:.1f}%  (P1 {p1rate:.0f}%)
  </div>

  <div class="summary">
    {card('전체 케이스', s['total'], '#222')}
    {card('합격', c['PASS'], STATUS_COLOR['PASS'])}
    {card('불합격', c['FAIL'], STATUS_COLOR['FAIL'])}
    {card('부분/미발견', c['PARTIAL']+c['N/A'], STATUS_COLOR['PARTIAL'])}
    {card('건너뜀', c['SKIP'], STATUS_COLOR['SKIP'])}
    {card('수동', c['MANUAL'], STATUS_COLOR['MANUAL'])}
  </div>

  <div class="prio">
    {''.join(f'''<div class="priocard"><b>{p} 우선순위</b> — {d['pass']}/{d['total']} 합격
      <div class="bar"><i style="width:{(d['pass']/d['total']*100) if d['total'] else 0:.0f}%"></i></div></div>'''
      for p, d in s['prio'].items())}
  </div>

  <div class="legend">
    범례:
    {_badge('PASS')} 매핑된 테스트 모두 통과 ·
    {_badge('FAIL')} 하나라도 실패 ·
    {_badge('PARTIAL')} 일부만 결과 존재 ·
    {_badge('N/A')} 매핑 테스트가 결과에 없음(매핑 점검) ·
    {_badge('SKIP')} 건너뜀 ·
    {_badge('MANUAL')} 자동화 외 수동 확인
  </div>

  {''.join(mod_html)}

  <div class="note">
    이 리포트는 매뉴얼 <b>10장 QA 테스트케이스</b>의 각 항목을 저장소의 <b>자동화 테스트(pytest)</b>에 매핑해 실행한 결과입니다.
    <code>N/A(미발견)</code>가 보이면 매핑된 테스트명이 바뀐 것이니 <code>docs/manual/qa/qa_cases.py</code>를 갱신하세요.
    재실행: <code>uv run python docs/manual/qa/run_qa.py</code>
  </div>
</div></body></html>
"""


# ── 데모(모의) 결과 ──────────────────────────────────────────────────────
def demo_results() -> dict:
    rnd = random.Random(7)
    res = {}
    for _, _, _, tests in CASES:
        for t in tests:
            x = rnd.random()
            res[t] = "FAIL" if x < 0.04 else ("SKIP" if x < 0.07 else "PASS")
    return res


def main():
    ap = argparse.ArgumentParser(description="QA 테스트케이스 실행 & HTML 리포트")
    ap.add_argument("--from-xml", help="기존 junit xml로 리포트만 생성")
    ap.add_argument("--demo", action="store_true", help="모의 결과로 미리보기 리포트")
    ap.add_argument("--no-run", action="store_true", help="pytest 실행 없이 기존 결과 사용")
    ap.add_argument("--out", default=str(OUT_HTML), help="리포트 출력 경로")
    a = ap.parse_args()

    if a.demo:
        results, mode, source = demo_results(), "DEMO(모의 데이터)", "샘플"
    else:
        xml_path = Path(a.from_xml) if a.from_xml else DEFAULT_XML
        if not a.from_xml and not a.no_run:
            run_pytest(xml_path)
        if not xml_path.exists():
            print(f"✗ 결과 파일이 없습니다: {xml_path}\n  먼저 pytest를 실행하거나 --from-xml 로 지정하세요.")
            sys.exit(1)
        results, mode, source = parse_junit(xml_path), "pytest", xml_path.name

    rows = aggregate(results)
    out = Path(a.out)
    out.write_text(render(rows, dict(mode=mode, source=source)), encoding="utf-8")
    s = summarize(rows)
    print(f"✓ 리포트 생성: {out}")
    print(f"  전체 {s['total']} · 합격 {s['counts']['PASS']} · 불합격 {s['counts']['FAIL']} "
          f"· 미발견 {s['counts']['N/A']} · 수동 {s['counts']['MANUAL']} · 합격률 {s['rate']:.1f}%")
    # 불합격이 있으면 CI 친화적으로 비0 종료
    sys.exit(1 if s["counts"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
