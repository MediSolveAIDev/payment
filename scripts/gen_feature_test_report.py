"""요청 017 — 기능별 상세 테스트 리포트(HTML) 생성기.

입력: pytest junit-xml(결제 서버) + Django -v2 출력(sample). 둘을 기능 영역으로
분류해 per-test 결과/소요시간/요약을 담은 단일 HTML을 docs/test_report/에 쓴다.
"""
import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

JUNIT = Path(sys.argv[1])          # /tmp/pytest_017.xml
SAMPLE_TXT = Path(sys.argv[2])     # /tmp/sample_017.txt
GEN_DATE = sys.argv[3]             # 'YYYY-MM-DD HH:MM' (호출측 주입 — 스크립트 내 시계 사용 안 함)
OUT = Path(__file__).resolve().parent.parent / "docs" / "test_report" / "017-feature-test-report.html"

# 기능 영역 분류 — (라벨, 키워드 리스트). 위에서부터 먼저 매칭(구독·결제·카드 우선).
AREAS = [
    ("카드(Card Vault)", ["card"]),
    ("결제(일반·취소·정산결제)", ["payment", "one_off", "oneoff", "billing", "reconcil"]),
    ("구독(생성·갱신·관리)", ["subscription", "renewal", "trial", "scheduler", "transition"]),
    ("서비스 알림(웹훅)", ["service_notification", "notify", "notification"]),
    ("웹훅 수신(토스)", ["webhook"]),
    ("정산", ["settlement"]),
    ("대시보드", ["dashboard"]),
    ("요금제", ["plan"]),
    ("서비스·레지스트리", ["registry", "service", "services"]),
    ("인증·계정·어드민", ["auth", "login", "account", "admin", "user", "audit"]),
    ("API·스키마·보안", ["api", "schema", "hmac", "security", "client_ip", "config"]),
    ("기타", []),
]


def classify(text: str) -> str:
    t = text.lower()
    for label, kws in AREAS:
        if not kws:
            return label
        if any(k in t for k in kws):
            return label
    return "기타"


def parse_junit(path: Path):
    """junit xml → [(area, suite, name, status, dur, msg)]."""
    rows = []
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")          # 예: tests.integration.test_payment_cancel
        name = tc.get("name", "")
        dur = float(tc.get("time", "0") or 0)
        status, msg = "passed", ""
        if tc.find("failure") is not None:
            status = "failed"; msg = (tc.find("failure").get("message") or "")[:300]
        elif tc.find("error") is not None:
            status = "error"; msg = (tc.find("error").get("message") or "")[:300]
        elif tc.find("skipped") is not None:
            status = "skipped"
        suite = cls.split(".")[-1] if cls else "?"
        rows.append((classify(cls + " " + name), suite, name, status, dur, msg))
    return rows


def _classify_sample(desc: str) -> str:
    """샘플 테스트는 Django가 docstring(한국어)을 출력하므로 한국어 키워드로 분류한다."""
    d = desc
    if "카드" in d:
        return "샘플 서비스 — 카드"
    if "구독" in d:
        return "샘플 서비스 — 구독"
    if "결제" in d or "단건" in d or "취소" in d or "환불" in d or "수수료" in d:
        return "샘플 서비스 — 결제"
    if "알림" in d or "notify" in d.lower() or "수신" in d:
        return "샘플 서비스 — 알림 수신"
    if "서명" in d or "hmac" in d.lower():
        return "샘플 서비스 — HMAC 서명"
    if "로그인" in d or "이메일" in d or "서비스" in d or "키" in d or "login" in d.lower():
        return "샘플 서비스 — 인증·서비스 흐름"
    return "샘플 서비스 — 기타"


def parse_sample(path: Path):
    """Django -v2 출력 → [(area, suite, name, status, dur, msg)]. dur 미측정(0).

    docstring 있는 테스트는 'test_x (path)' 대신 docstring 첫 줄이 출력되므로
    'desc ... ok' 전체를 잡아 한국어 키워드로 분류한다.
    """
    rows = []
    pat = re.compile(r"^(.*?) \.\.\. (ok|FAIL|ERROR|skipped)\b", re.M)
    txt = path.read_text(encoding="utf-8", errors="replace")
    for m in pat.finditer(txt):
        desc, res = m.group(1).strip(), m.group(2)
        status = {"ok": "passed", "FAIL": "failed",
                  "ERROR": "error", "skipped": "skipped"}[res]
        rows.append((_classify_sample(desc), "shop.tests", desc, status, 0.0, ""))
    return rows


def esc(s):
    return html.escape(str(s or ""))


pay_rows = parse_junit(JUNIT)
sample_rows = parse_sample(SAMPLE_TXT)
rows = pay_rows + sample_rows

# 영역별 그룹 + 요약
by_area = {}
for area, suite, name, status, dur, msg in rows:
    by_area.setdefault(area, []).append((suite, name, status, dur, msg))
# 결제 서버 영역(AREAS 순서) 먼저, 그다음 샘플 등 동적 영역
_known = {a for a, _ in AREAS}
area_order = ([a for a, _ in AREAS if a in by_area]
              + sorted(a for a in by_area if a not in _known))

total = len(rows)
passed = sum(1 for r in rows if r[3] == "passed")
failed = sum(1 for r in rows if r[3] in ("failed", "error"))
skipped = sum(1 for r in rows if r[3] == "skipped")

# 핵심(구독·결제·카드) 실패 집계
CORE = {"카드(Card Vault)", "결제(일반·취소·정산결제)", "구독(생성·갱신·관리)"}
core_total = sum(len(v) for a, v in by_area.items() if a in CORE)
core_fail = sum(1 for area, _, _, status, _, _ in rows
                if area in CORE and status in ("failed", "error"))

BADGE = {"passed": ("통과", "#0BA66A", "#EAF8F1"),
         "failed": ("실패", "#FF4E51", "#FFEFEF"),
         "error": ("에러", "#FF4E51", "#FFEFEF"),
         "skipped": ("스킵", "#9F9F9F", "#F3F3F3")}


def badge(status):
    label, fg, bg = BADGE.get(status, (status, "#333", "#eee"))
    return f'<span style="background:{bg};color:{fg};font-weight:600;font-size:12px;padding:2px 8px;border-radius:999px">{label}</span>'


verdict_ok = core_fail == 0 and failed == 0
verdict_bg = "#EAF8F1" if verdict_ok else "#FFEFEF"
verdict_fg = "#0BA66A" if verdict_ok else "#C2272A"
verdict_msg = ("구독·결제·카드 전 영역 버그 없음 — 전체 테스트 통과"
               if verdict_ok else f"실패 {failed}건 — 점검 필요")

cards = "".join(
    f'<div style="flex:1;min-width:120px;background:#fff;border:1px solid #E3E3E3;border-radius:12px;padding:16px;text-align:center">'
    f'<div style="font-size:26px;font-weight:700;color:{c}">{n}</div>'
    f'<div style="color:#6E6E6E;font-size:13px">{l}</div></div>'
    for n, l, c in [(total, "전체", "#111"), (passed, "통과", "#0BA66A"),
                    (failed, "실패", "#FF4E51"), (skipped, "스킵", "#9F9F9F")])

sections = []
for area in area_order:
    items = by_area[area]
    ap = sum(1 for it in items if it[2] == "passed")
    af = sum(1 for it in items if it[2] in ("failed", "error"))
    core_tag = ' · <b style="color:#476CFF">핵심</b>' if area in CORE else ""
    trs = "".join(
        f'<tr style="border-top:1px solid #F3F3F3">'
        f'<td style="padding:6px 10px;color:#6E6E6E;font-size:12px;font-family:ui-monospace,monospace">{esc(suite)}</td>'
        f'<td style="padding:6px 10px;font-family:ui-monospace,monospace;font-size:12px">{esc(name)}</td>'
        f'<td style="padding:6px 10px">{badge(status)}</td>'
        f'<td style="padding:6px 10px;text-align:right;color:#9F9F9F;font-size:12px">{dur*1000:.0f} ms</td>'
        f'{("<td style=padding:6px;color:#C2272A;font-size:11px>" + esc(msg) + "</td>") if msg else "<td></td>"}'
        f'</tr>'
        for suite, name, status, dur, msg in sorted(items, key=lambda x: (x[0], x[1])))
    sections.append(
        f'<h2 style="margin:26px 0 8px;font-size:17px">{esc(area)} '
        f'<span style="font-size:13px;color:#6E6E6E;font-weight:400">— {ap}/{len(items)} 통과'
        f'{(", <span style=color:#FF4E51>실패 " + str(af) + "</span>") if af else ""}{core_tag}</span></h2>'
        f'<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #E3E3E3;border-radius:10px;overflow:hidden;font-size:13px">'
        f'<thead><tr style="background:#FBFBFB;color:#6E6E6E;font-size:12px">'
        f'<th style="text-align:left;padding:8px 10px">스위트</th><th style="text-align:left;padding:8px 10px">테스트</th>'
        f'<th style="text-align:left;padding:8px 10px">결과</th><th style="text-align:right;padding:8px 10px">소요</th><th></th>'
        f'</tr></thead><tbody>{trs}</tbody></table>')

doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>전체 기능 테스트 리포트 (요청 017)</title>
<style>body{{font-family:'Pretendard',-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;
background:#FBFBFB;color:#111;margin:0;padding:32px;line-height:1.6}}
.wrap{{max-width:1080px;margin:0 auto}}h1{{font-size:24px;margin:0 0 4px}}code{{font-family:ui-monospace,monospace}}</style>
</head><body><div class="wrap">
<h1>전체 기능 테스트 리포트</h1>
<p style="color:#6E6E6E;margin:0 0 16px">구독/결제 서버 + 샘플 서비스 — 요청 017 · 생성 {esc(GEN_DATE)}</p>
<div style="background:{verdict_bg};color:{verdict_fg};border-radius:12px;padding:16px 18px;font-weight:600;margin-bottom:16px">
✅ {esc(verdict_msg)} · 핵심(구독·결제·카드) {core_total}건 중 실패 {core_fail}건</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px">{cards}</div>
<p style="color:#6E6E6E;font-size:13px">실행 환경: 결제 서버 pytest(외부 PostgreSQL <code>payment_test</code> + Redis) {len(pay_rows)}건 · 샘플 서비스 Django test(SQLite) {len(sample_rows)}건 · 총 {total}건.</p>
{''.join(sections)}
<hr style="margin:28px 0;border:none;border-top:1px solid #E3E3E3">
<p style="color:#9F9F9F;font-size:12px">자동 생성: <code>scripts/gen_feature_test_report.py</code> (junit-xml + Django -v2 파싱). 매 실행 시 갱신.</p>
</div></body></html>"""

OUT.write_text(doc, encoding="utf-8")
print(f"wrote {OUT}  ({total} tests, {passed} passed, {failed} failed, {skipped} skipped)")
print(f"core(구독·결제·카드): {core_total} tests, {core_fail} failed")
