#!/usr/bin/env python3
"""기능별 테스트케이스 리포트 생성기.

pytest 실행 결과(junit xml)와 테스트 코드의 docstring을 결합해,
"기능 영역 → 테스트케이스 → 설명 → 결과"를 마크다운 또는 HTML 리포트로 만든다.

사용법:
    # 마크다운 리포트 생성
    .venv/bin/python -m pytest -q --junitxml=/tmp/junit_main.xml
    .venv/bin/python scripts/feature_test_report.py /tmp/junit_main.xml docs/test_report/feature-test-report.md

    # HTML 리포트 생성 (출력 경로가 .html 로 끝나면 자동으로 HTML 형식)
    .venv/bin/python scripts/feature_test_report.py /tmp/junit_main.xml docs/test_report/feature-test-report.html

출력 형식 선택:
    - 출력 경로가 `.html`로 끝나면 → 자체 포함 HTML 리포트 생성 (인라인 CSS, 외부 의존성 없음)
    - 그 외 → 마크다운 리포트 생성
    - 두 형식을 모두 생성하려면 스크립트를 두 번 호출하면 된다

테스트케이스 설명은 각 테스트 함수의 docstring 첫 줄에서 가져온다 —
docstring이 곧 TC 명세이므로, 새 테스트를 쓸 때 docstring을 충실히 달 것.
"""
import ast
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 테스트 모듈 → (기능 영역, 모듈 설명). 새 테스트 파일을 추가하면 여기에 등록한다.
FEATURE_MAP = {
    # ── 도메인 규칙 (단위) ──
    "tests/unit/test_billing_math.py": ("도메인 규칙 — 금액·기간 계산", "할인·기간 계산 기본"),
    "tests/unit/test_billing_math_edges.py": ("도메인 규칙 — 금액·기간 계산", "경계 케이스(수수료 불변식·월말 클램프·0원 할인)"),
    "tests/unit/test_transitions.py": ("도메인 규칙 — 구독 상태머신", "허용 전이·불변식"),
    "tests/unit/test_crypto.py": ("도메인 규칙 — 암호화", "AES-GCM 암복호화"),
    "tests/unit/test_security.py": ("도메인 규칙 — 서명·해시", "HMAC 서명·비밀번호 해시"),
    "tests/unit/test_client_ip.py": ("인증·보안 — 외부 API", "X-Forwarded-For 위조 방어"),
    "tests/unit/test_toss_client.py": ("토스 연동 — 클라이언트", "HTTP 클라이언트·타임아웃 분류"),
    "tests/unit/test_email_sender.py": ("운영 — 알림", "이메일 발송 구현"),
    "tests/unit/test_admin_helpers.py": ("어드민 — 공용 부품", "페이징·필터 헬퍼"),
    "tests/unit/test_export.py": ("어드민 — 공용 부품", "엑셀 수식 주입 방어"),
    "tests/unit/test_payment_error_labels.py": ("어드민 — 공용 부품", "토스 오류 한글 라벨"),
    "tests/unit/test_config_settings.py": ("도메인 규칙 — 설정·환경변수", "전역 Settings 기본값·오버라이드 검증"),
    # ── 통합 (실제 DB/Redis + FakeToss) ──
    "tests/integration/test_subscription_create.py": ("구독 — 생성", "일반·체험·첫구독 혜택·실패·타임아웃"),
    "tests/integration/test_subscription_manage.py": ("구독 — 관리", "취소·재개·카드변경"),
    "tests/integration/test_trial_and_manual.py": ("구독 — 체험·수동결제·연장", "체험 전환·수동결제·관리자 재결제·연장(EXTENDED)"),
    "tests/integration/test_renewals.py": ("구독 — 자동 갱신 배치", "갱신·재시도·정지·만료 상태머신 + 정산 수렴"),
    "tests/integration/test_scheduler.py": ("구독 — 자동 갱신 배치", "스케줄러 전역 락"),
    "tests/integration/test_one_off_payment.py": ("결제 — 단건(일반)", "생성·멱등·테넌트 격리·취소"),
    "tests/integration/test_webhooks.py": ("결제 — 토스 웹훅", "멱등·페이로드 불신·취소 동기화"),
    "tests/integration/test_settlement.py": ("정산·통계", "월별 정산 집계"),
    "tests/integration/test_dashboard.py": ("정산·통계", "대시보드 집계"),
    "tests/integration/test_plans_service.py": ("요금제", "CRUD·검증·주기 불변·보너스 일수"),
    "tests/integration/test_registry.py": ("서비스(테넌트) 관리", "등록·키 발급/회전·정책"),
    "tests/integration/test_accounts.py": ("어드민 — 계정·권한", "계정·담당 서비스 배정"),
    "tests/integration/test_app_settings.py": ("운영 — 전역설정", "재시도 정책·킬스위치 설정"),
    "tests/integration/test_killswitch.py": ("운영 — 전역설정", "킬스위치 게이트·캐시"),
    "tests/integration/test_api_endpoints.py": ("외부 API — 엔드포인트", "HTTP 응답·스키마"),
    "tests/integration/test_auth_service.py": ("어드민 — 로그인·세션", "로그인·잠금·세션"),
    "tests/integration/test_cards.py": ("카드 — 보관함(Card Vault)", "카드 등록/교체·삭제·동시성·차단 규칙"),
    "tests/integration/test_cards_api.py": ("카드 — 외부 API", "POST/GET/DELETE /api/v1/cards"),
    # ── e2e (HTTP 전체 경로) ──
    "tests/e2e/test_full_flow.py": ("시나리오 — 전체 흐름", "가입→갱신→취소 장기 시나리오"),
    "tests/e2e/test_admin_flows.py": ("어드민 — 화면", "로그인·대시보드·기본 흐름"),
    "tests/e2e/test_admin_operations.py": ("어드민 — 화면", "서비스·구독·결제 운영 동작"),
    "tests/e2e/test_admin_services_plans.py": ("어드민 — 화면", "서비스·요금제 화면"),
    "tests/e2e/test_service_plans.py": ("어드민 — 화면", "담당자 권한별 요금제"),
    "tests/e2e/test_list_export.py": ("어드민 — 화면", "목록·엑셀 다운로드"),
    "tests/e2e/test_dashboard_page.py": ("어드민 — 화면", "대시보드 렌더"),
    "tests/e2e/test_api_http.py": ("외부 API — 엔드포인트", "HTTP 레벨 검증"),
    "tests/e2e/test_security_phase2.py": ("인증·보안 — 어드민·운영", "로그인 rate limit·보안 헤더·세션 절대만료"),
    "tests/e2e/test_accounts_admin.py": ("어드민 — 계정·권한", "계정 관리 화면"),
    "tests/e2e/test_email_flash.py": ("어드민 — 화면", "메일 안내·플래시 메시지"),
    "tests/e2e/test_htmx_partials.py": ("어드민 — 화면", "htmx partial 갱신"),
    "tests/e2e/test_killswitch.py": ("운영 — 전역설정", "킬스위치 화면·차단 동작"),
    "tests/e2e/test_service_detail_page.py": ("어드민 — 화면", "서비스 상세(탭·키·정책·담당자)"),
    "tests/e2e/test_services_list.py": ("어드민 — 화면", "서비스 목록"),
    "tests/e2e/test_settlement_page.py": ("어드민 — 화면", "정산 화면"),
    "tests/e2e/test_account_loading_ux.py": ("어드민 — 화면", "계정 추가 폼 로딩 UX(data-loading 속성)"),
    # ── 보안 전용 ──
    "tests/security/test_hmac_auth.py": ("인증·보안 — 외부 API", "HMAC 3중 인증 6단계"),
    "tests/security/test_admin_security.py": ("인증·보안 — 어드민·운영", "권한 격리·잠금·CSRF"),
    # ── 기타 통합 ──
    "tests/integration/test_api_auth.py": ("인증·보안 — 외부 API", "인증 의존성 통합"),
    "tests/integration/test_audit.py": ("운영 — 감사 로그", "감사 기록·이름 해석"),
    "tests/integration/test_models.py": ("도메인 규칙 — DB 제약", "유니크·제약 동작"),
    "tests/integration/test_payment_cancel.py": ("결제 — 단건(일반)", "취소·수수료 정책"),
}

# 영역 표시 순서(미등록 모듈은 '기타'로)
AREA_ORDER = [
    "구독 — 생성", "구독 — 관리", "구독 — 체험·수동결제·연장", "구독 — 자동 갱신 배치",
    "결제 — 단건(일반)", "결제 — 토스 웹훅", "정산·통계", "요금제", "서비스(테넌트) 관리",
    "카드 — 보관함(Card Vault)", "카드 — 외부 API",
    "외부 API — 엔드포인트", "인증·보안 — 외부 API", "인증·보안 — 어드민·운영",
    "어드민 — 로그인·세션", "어드민 — 계정·권한", "어드민 — 화면", "어드민 — 공용 부품",
    "운영 — 전역설정", "운영 — 감사 로그", "운영 — 알림", "토스 연동 — 클라이언트",
    "도메인 규칙 — DB 제약",
    "도메인 규칙 — 구독 상태머신", "도메인 규칙 — 금액·기간 계산",
    "도메인 규칙 — 암호화", "도메인 규칙 — 서명·해시",
    "도메인 규칙 — 설정·환경변수",
    "시나리오 — 전체 흐름", "기타",
]


def collect_docstrings() -> dict[tuple[str, str], str]:
    """(모듈 상대경로, 테스트 함수명) → docstring 첫 줄."""
    out = {}
    for path in (ROOT / "tests").rglob("test_*.py"):
        rel = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                doc = ast.get_docstring(node) or ""
                out[(rel, node.name)] = doc.splitlines()[0].strip() if doc else ""
    return out


def parse_junit(xml_path: str):
    """junit xml → [(모듈 상대경로, 테스트명(원본), 테스트명(기본), 결과, 시간초)]."""
    rows = []
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        parts = case.get("classname", "").split(".")
        # 클래스 기반 테스트는 classname 끝이 클래스명 — 실제 존재하는 파일까지 잘라낸다
        module = "/".join(parts) + ".py"
        for cut in (len(parts), len(parts) - 1):
            cand = "/".join(parts[:cut]) + ".py"
            if (ROOT / cand).exists():
                module = cand
                break
        name = case.get("name", "").split("[")[0]   # 파라미터라이즈 id 제거
        if case.find("failure") is not None or case.find("error") is not None:
            status = "❌ 실패"
        elif case.find("skipped") is not None:
            status = "⏭️ 건너뜀"
        else:
            status = "✅ 통과"
        rows.append((module, case.get("name", ""), name, status, float(case.get("time", 0))))
    return rows


def build_by_area(rows, docs):
    """테스트 행을 기능 영역별로 집계한다."""
    by_area = defaultdict(lambda: defaultdict(list))
    for module, full_name, base_name, status, sec in rows:
        area, mod_desc = FEATURE_MAP.get(module, ("기타", ""))
        desc = docs.get((module, base_name), "") or base_name.replace("test_", "").replace("_", " ")
        by_area[area][(module, mod_desc)].append((full_name, desc, status, sec))
    return by_area


def write_markdown(out_path: Path, by_area, rows, now: str):
    """마크다운 리포트를 생성한다."""
    total = len(rows)
    passed = sum(1 for r in rows if r[3] == "✅ 통과")
    failed = sum(1 for r in rows if r[3] == "❌ 실패")
    skipped = total - passed - failed

    lines = [
        "# 전체 기능 테스트케이스 실행 리포트",
        "",
        f"- **실행 시각**: {now}",
        f"- **결과**: 총 {total}건 — ✅ 통과 {passed} · ❌ 실패 {failed} · ⏭️ 건너뜀 {skipped}",
        "- **재생성**: `.venv/bin/python -m pytest -q --junitxml=/tmp/junit_main.xml && "
        ".venv/bin/python scripts/feature_test_report.py`",
        "",
        "## 기능 영역별 요약",
        "",
        "| 기능 영역 | 테스트 수 | 통과 | 실패 |",
        "|---|---|---|---|",
    ]
    for area in AREA_ORDER:
        if area not in by_area:
            continue
        cases = [c for mod in by_area[area].values() for c in mod]
        p = sum(1 for c in cases if c[2] == "✅ 통과")
        f = sum(1 for c in cases if c[2] == "❌ 실패")
        lines.append(f"| {area} | {len(cases)} | {p} | {f} |")
    lines += ["", "---", ""]

    for area in AREA_ORDER:
        if area not in by_area:
            continue
        lines.append(f"## {area}")
        lines.append("")
        for (module, mod_desc), cases in sorted(by_area[area].items()):
            lines.append(f"### `{module}` — {mod_desc} ({len(cases)}건)")
            lines.append("")
            lines.append("| 테스트케이스 | 설명 | 결과 |")
            lines.append("|---|---|---|")
            for full_name, desc, status, _sec in cases:
                lines.append(f"| `{full_name}` | {desc} | {status} |")
            lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"생성(MD): {out_path}  (총 {total}건, 통과 {passed}, 실패 {failed})")


def _badge(status: str) -> str:
    """상태 값에 따라 HTML 배지 스팬을 반환한다."""
    if status == "✅ 통과":
        return '<span class="badge pass">통과</span>'
    elif status == "❌ 실패":
        return '<span class="badge fail">실패</span>'
    else:
        return '<span class="badge skip">건너뜀</span>'


def write_html(out_path: Path, by_area, rows, now: str):
    """자체 포함 HTML 리포트를 생성한다 (인라인 CSS, 외부 의존성 없음, 한국어 UI)."""
    total = len(rows)
    passed = sum(1 for r in rows if r[3] == "✅ 통과")
    failed = sum(1 for r in rows if r[3] == "❌ 실패")
    skipped = total - passed - failed

    # ── 인라인 CSS ──────────────────────────────────────────────
    css = """
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        background: #f5f7fa;
        color: #1a202c;
        font-size: 14px;
        line-height: 1.6;
    }
    .page-header {
        background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%);
        color: #fff;
        padding: 32px 40px 28px;
    }
    .page-header h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: 8px; }
    .page-header .meta { opacity: 0.85; font-size: 0.85rem; }
    .summary-bar {
        display: flex; gap: 16px; flex-wrap: wrap;
        padding: 20px 40px;
        background: #fff;
        border-bottom: 1px solid #e2e8f0;
        box-shadow: 0 1px 4px rgba(0,0,0,.06);
    }
    .stat-card {
        border-radius: 8px;
        padding: 12px 20px;
        min-width: 110px;
        text-align: center;
    }
    .stat-card .num { font-size: 1.8rem; font-weight: 800; line-height: 1; }
    .stat-card .lbl { font-size: 0.75rem; margin-top: 4px; opacity: 0.7; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
    .stat-total  { background: #ebf4ff; color: #1a365d; }
    .stat-pass   { background: #f0fff4; color: #22543d; }
    .stat-fail   { background: #fff5f5; color: #742a2a; }
    .stat-skip   { background: #f7fafc; color: #4a5568; }
    .content { padding: 28px 40px 60px; max-width: 1320px; }
    .area-section { margin-bottom: 40px; }
    .area-title {
        font-size: 1.05rem; font-weight: 700;
        color: #2d3748;
        border-left: 4px solid #3182ce;
        padding-left: 10px;
        margin-bottom: 14px;
    }
    .module-block { margin-bottom: 18px; }
    .module-title {
        font-size: 0.8rem; font-weight: 600;
        color: #4a5568;
        background: #edf2f7;
        border-radius: 4px;
        padding: 5px 10px;
        margin-bottom: 0;
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        background: #fff;
        border-radius: 0 0 8px 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,.07);
        overflow: hidden;
        font-size: 0.82rem;
    }
    thead th {
        background: #f7fafc;
        color: #718096;
        font-weight: 700;
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        padding: 8px 12px;
        text-align: left;
        border-bottom: 2px solid #e2e8f0;
    }
    tbody tr { border-bottom: 1px solid #edf2f7; }
    tbody tr:last-child { border-bottom: none; }
    tbody tr:hover { background: #f7fafc; }
    tbody tr.row-fail { background: #fff5f5; }
    tbody tr.row-fail:hover { background: #fed7d7; }
    td { padding: 7px 12px; vertical-align: top; }
    td.tc-name {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 0.78rem;
        color: #2d3748;
        max-width: 360px;
        word-break: break-all;
    }
    td.tc-desc { color: #4a5568; }
    td.tc-status { white-space: nowrap; }
    .badge {
        display: inline-block;
        border-radius: 4px;
        padding: 2px 9px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.03em;
    }
    .badge.pass { background: #c6f6d5; color: #22543d; }
    .badge.fail { background: #fed7d7; color: #742a2a; border: 1px solid #fc8181; }
    .badge.skip { background: #e2e8f0; color: #718096; }
    .toc {
        background: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 32px;
        font-size: 0.82rem;
    }
    .toc h2 { font-size: 0.85rem; font-weight: 700; color: #718096; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
    .toc ul { list-style: none; display: flex; flex-wrap: wrap; gap: 6px 0; }
    .toc li { width: 50%; }
    .toc a { color: #3182ce; text-decoration: none; }
    .toc a:hover { text-decoration: underline; }
    .alert-failed {
        background: #fff5f5;
        border: 2px solid #fc8181;
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 24px;
        font-size: 0.85rem;
        color: #742a2a;
        font-weight: 600;
    }
    """

    # ── HTML 빌드 ──────────────────────────────────────────────
    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="ko">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        "  <title>전체 기능 테스트케이스 실행 리포트</title>",
        f"  <style>{css}</style>",
        "</head>",
        "<body>",
        '<div class="page-header">',
        "  <h1>전체 기능 테스트케이스 실행 리포트</h1>",
        f'  <div class="meta">생성 시각: {now}</div>',
        "</div>",
        '<div class="summary-bar">',
        f'  <div class="stat-card stat-total"><div class="num">{total}</div><div class="lbl">전체</div></div>',
        f'  <div class="stat-card stat-pass"><div class="num">{passed}</div><div class="lbl">통과</div></div>',
        f'  <div class="stat-card stat-fail"><div class="num">{failed}</div><div class="lbl">실패</div></div>',
        f'  <div class="stat-card stat-skip"><div class="num">{skipped}</div><div class="lbl">건너뜀</div></div>',
        "</div>",
        '<div class="content">',
    ]

    # 실패 경고 배너
    if failed > 0:
        html_parts.append(
            f'  <div class="alert-failed">⚠️ 실패한 테스트 {failed}건이 있습니다. 아래 목록에서 확인하세요.</div>'
        )

    # TOC
    toc_areas = [a for a in AREA_ORDER if a in by_area]
    if toc_areas:
        html_parts.append('  <nav class="toc"><h2>기능 영역 목차</h2><ul>')
        for area in toc_areas:
            anchor = area.replace(" ", "-").replace("·", "").replace("—", "").replace("(", "").replace(")", "")
            cases_in_area = [c for mod in by_area[area].values() for c in mod]
            n = len(cases_in_area)
            p = sum(1 for c in cases_in_area if c[2] == "✅ 통과")
            f = sum(1 for c in cases_in_area if c[2] == "❌ 실패")
            fail_note = f' <span style="color:#e53e3e;font-weight:700;">실패 {f}건</span>' if f else ""
            html_parts.append(
                f'    <li><a href="#{anchor}">{area}</a> ({n}건, 통과 {p}){fail_note}</li>'
            )
        html_parts.append("  </ul></nav>")

    # 영역별 섹션
    for area in AREA_ORDER:
        if area not in by_area:
            continue
        anchor = area.replace(" ", "-").replace("·", "").replace("—", "").replace("(", "").replace(")", "")
        html_parts.append(f'  <section class="area-section" id="{anchor}">')
        html_parts.append(f'    <h2 class="area-title">{area}</h2>')

        for (module, mod_desc), cases in sorted(by_area[area].items()):
            html_parts.append(f'    <div class="module-block">')
            html_parts.append(f'      <div class="module-title">{module} — {mod_desc} ({len(cases)}건)</div>')
            html_parts.append("      <table>")
            html_parts.append("        <thead><tr>")
            html_parts.append("          <th>테스트케이스</th><th>설명</th><th>결과</th><th>시간(s)</th>")
            html_parts.append("        </tr></thead>")
            html_parts.append("        <tbody>")
            for full_name, desc, status, sec in cases:
                # 실패 행은 강조 클래스
                row_cls = ' class="row-fail"' if status == "❌ 실패" else ""
                badge = _badge(status)
                # HTML 이스케이프 (< > & " 처리)
                safe_name = full_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                safe_desc = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html_parts.append(
                    f'          <tr{row_cls}>'
                    f'<td class="tc-name">{safe_name}</td>'
                    f'<td class="tc-desc">{safe_desc}</td>'
                    f'<td class="tc-status">{badge}</td>'
                    f'<td>{sec:.2f}</td>'
                    f"</tr>"
                )
            html_parts.append("        </tbody>")
            html_parts.append("      </table>")
            html_parts.append("    </div>")

        html_parts.append("  </section>")

    html_parts += [
        "</div>",  # .content
        "</body>",
        "</html>",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(html_parts), encoding="utf-8")
    print(f"생성(HTML): {out_path}  (총 {total}건, 통과 {passed}, 실패 {failed})")


def main():
    """CLI 진입점. 출력 경로 확장자에 따라 MD 또는 HTML을 생성한다."""
    xml_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/junit_main.xml"
    out_path = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "docs/test_report/feature-test-report.md")

    docs = collect_docstrings()
    rows = parse_junit(xml_path)
    by_area = build_by_area(rows, docs)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 출력 형식 분기: .html → HTML, 그 외 → Markdown
    if out_path.suffix.lower() == ".html":
        write_html(out_path, by_area, rows, now)
    else:
        write_markdown(out_path, by_area, rows, now)


if __name__ == "__main__":
    main()
