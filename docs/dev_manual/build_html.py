#!/usr/bin/env python3
"""dev_manual 마크다운 문서들을 '다중 페이지' 개발자 매뉴얼 사이트로 변환한다.

목적: 초급 개발자가 이 사이트만으로 프로젝트 운영·기능추가·유지보수를 할 수 있게,
검증된 마크다운(기능 16 + 어드민 11 + 온보딩)을 문서별 HTML 페이지로 변환한다.
좌측 공통 네비 · 우측 'On this page' 목차 · 요약/주의/상호참조 콜아웃 · file:line 배지 ·
이전/다음 이동 · 검색 · 읽기 진행바를 제공한다.

실행: uv run --with markdown python docs/dev_manual/build_html.py
출력: docs/dev_manual/index.html, <slug>.html ..., assets/manual.css, assets/manual.js
      (manual.html 은 index.html 로 리다이렉트하는 호환 스텁)
"""
import html
import os
import re

import markdown  # uv run --with markdown 로 제공

HERE = os.path.dirname(os.path.abspath(__file__))

# 사이드바 표시 순서·그룹. (relpath, group) — relpath는 HERE 기준.
DOCS = [
    ("README.md", "기반 (먼저 읽기)"),
    ("01-getting-started.md", "기반 (먼저 읽기)"),
    ("02-database.md", "기반 (먼저 읽기)"),
    ("03-auth-and-security.md", "기반 (먼저 읽기)"),
    ("04-subscription-create.md", "기능별 상세"),
    ("05-subscription-renewal.md", "기능별 상세"),
    ("06-subscription-manage.md", "기능별 상세"),
    ("07-one-off-payment.md", "기능별 상세"),
    ("08-plans.md", "기능별 상세"),
    ("09-services-registry.md", "기능별 상세"),
    ("10-settlement.md", "기능별 상세"),
    ("11-dashboard.md", "기능별 상세"),
    ("12-webhooks.md", "기능별 상세"),
    ("13-admin-accounts.md", "기능별 상세"),
    ("14-global-settings.md", "기능별 상세"),
    ("15-external-api-and-sample.md", "기능별 상세"),
    ("16-card-vault.md", "기능별 상세"),
    ("17-service-notifications.md", "기능별 상세"),
    ("admin/README.md", "어드민 화면별 매뉴얼"),
    ("admin/01-login-and-access.md", "어드민 화면별 매뉴얼"),
    ("admin/02-dashboard.md", "어드민 화면별 매뉴얼"),
    ("admin/03-services.md", "어드민 화면별 매뉴얼"),
    ("admin/04-plans.md", "어드민 화면별 매뉴얼"),
    ("admin/05-subscriptions.md", "어드민 화면별 매뉴얼"),
    ("admin/06-payments.md", "어드민 화면별 매뉴얼"),
    ("admin/07-settlement.md", "어드민 화면별 매뉴얼"),
    ("admin/08-users.md", "어드민 화면별 매뉴얼"),
    ("admin/09-audit.md", "어드민 화면별 매뉴얼"),
    ("admin/10-settings.md", "어드민 화면별 매뉴얼"),
    ("admin/11-onboarding-checklist.md", "운영 워크플로우"),
]

GROUP_ICON = {
    "기반 (먼저 읽기)": "🧱",
    "기능별 상세": "⚙️",
    "어드민 화면별 매뉴얼": "🖥️",
    "운영 워크플로우": "📋",
}
GROUP_DESC = {
    "기반 (먼저 읽기)": "시스템 개요·실행·DB·인증. 처음이면 여기부터.",
    "기능별 상세": "요청 → 파일/코드 → DB → 응답을 기능 단위로 추적.",
    "어드민 화면별 매뉴얼": "htmx 어드민 화면별 사용·구현 위치.",
    "운영 워크플로우": "신규 서비스 온보딩 등 절차 체크리스트.",
}


def slug(relpath: str) -> str:
    """문서 relpath → 페이지 파일명 베이스. 예: admin/03-services.md → admin--03-services"""
    return relpath[:-3].replace("/", "--")


SLUG = {rel: slug(rel) for rel, _ in DOCS}
GROUP_ORDER = list(dict.fromkeys(g for _, g in DOCS))
TITLES: dict = {}


def title_of(relpath: str, text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return relpath


def short_no(title: str) -> str:
    """제목 앞 번호(예: '04. 구독 생성' → '04')를 뽑아 칩으로 쓴다."""
    m = re.match(r"\s*([0-9]+)[.\s]", title)
    return m.group(1) if m else "·"


def plain(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[`*_>#|]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def summary_text(text: str) -> str:
    """문서 카드/헤더에 쓸 한 줄 요약. '## n. 한 줄 요약' 다음 줄을 우선 사용."""
    m = re.search(r"#+\s*[0-9.\s]*한\s*줄\s*요약\s*\n+([^\n]+)", text)
    if m:
        return plain(m.group(1))
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ">", "-", "|", "```")):
            continue
        return plain(s)
    return ""


# ── 본문 후처리 (체계적 UI) ──────────────────────────────────────────────────
def rewrite_md_links(htm: str, doc_dir: str) -> str:
    """.md 하이퍼링크를 우리 문서면 <slug>.html(+anchor)로, 아니면 원본 유지."""
    def repl(m: re.Match) -> str:
        href = m.group(1)
        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
            anchor = "#" + anchor
        if not href.endswith(".md"):
            return m.group(0)
        target = os.path.normpath(os.path.join(doc_dir, href)).replace(os.sep, "/")
        if target in SLUG:
            return f'href="{SLUG[target]}.html{anchor}"'
        return m.group(0)

    return re.sub(r'href="([^"]+)"', repl, htm)


LOC_RE = re.compile(r"^[\w./-]+\.[A-Za-z0-9_]+:\d+(?:-\d+)?$")


def badge_locs(htm: str) -> str:
    """인라인 코드 중 'path/file.py:123' 형태를 위치 배지로 강조."""
    return re.sub(
        r"<code>([^<]+)</code>",
        lambda m: f'<code class="loc">{m.group(1)}</code>' if LOC_RE.match(m.group(1)) else m.group(0),
        htm,
    )


CALLOUT_RULES = [
    (("쉽게 말하면", "비유하면", "비유로", "한마디로"), "easy", "🍎"),
    (("주의", "경고", "중요", "위험"), "warn", "⚠️"),
    (("상호참조", "관련 문서", "관련 테스트", "다음 문서"), "ref", "🔗"),
    (("대상 독자", "대상", "권한"), "who", "👤"),
    (("파일 위치", "파일"), "file", "📄"),
    (("참고", "팁", "TIP", "예시", "예)"), "note", "💡"),
]


def style_blockquotes(htm: str) -> str:
    """블록인용을 첫 단어 키워드에 따라 콜아웃으로 분류."""
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        text = re.sub(r"<[^>]+>", "", inner).lstrip()
        for keys, cls, ico in CALLOUT_RULES:
            if any(text.startswith(k) for k in keys):
                return f'<blockquote class="cal {cls}"><span class="cal-i">{ico}</span><div class="cal-b">{inner}</div></blockquote>'
        return f'<blockquote class="cal note"><span class="cal-i">💬</span><div class="cal-b">{inner}</div></blockquote>'

    return re.sub(r"<blockquote>(.*?)</blockquote>", repl, htm, flags=re.S)


def lift_summary(htm: str):
    """'한 줄 요약' 섹션(h2 + 첫 문단)을 본문에서 떼어 헤더 강조 카드로 올린다."""
    pat = re.compile(r'<h2[^>]*>\s*[0-9.\s]*한\s*줄\s*요약\s*</h2>\s*(<p>.*?</p>)', re.S)
    m = pat.search(htm)
    if not m:
        return htm, ""
    body = htm[: m.start()] + htm[m.end():]
    return body, m.group(1)


def strip_first_h1(htm: str) -> str:
    return re.sub(r"<h1[^>]*>.*?</h1>", "", htm, count=1, flags=re.S)


def build_toc(htm: str) -> str:
    """본문 h2/h3(id 보유)로 우측 'On this page' 목차 생성."""
    items = []
    for m in re.finditer(r'<(h2|h3)\s+id="([^"]+)">(.*?)</\1>', htm, re.S):
        lvl, hid = m.group(1), m.group(2)
        txt = html.escape(re.sub(r"<[^>]+>", "", m.group(3)).strip())
        items.append(f'<a class="toc-{lvl}" href="#{hid}">{txt}</a>')
    if not items:
        return ""
    return '<div class="toc-h">이 페이지 내용</div>\n' + "\n".join(items)


# ── 공통 조각 ────────────────────────────────────────────────────────────────
def sidebar_html(active: str) -> str:
    out = ['<a class="brand" href="index.html"><span class="brand-ico">💳</span>'
           '<span class="brand-t"><b>구독·결제 서버</b><small>개발자 매뉴얼</small></span></a>',
           '<div class="search"><input id="q" type="text" placeholder="문서 검색…" autocomplete="off"></div>',
           '<nav class="nav">']
    for group in GROUP_ORDER:
        out.append(f'<div class="nav-group"><span>{GROUP_ICON.get(group, "•")}</span>{html.escape(group)}</div>')
        for rel, g in DOCS:
            if g != group:
                continue
            sg = SLUG[rel]
            cls = "nav-link active" if sg == active else "nav-link"
            t = TITLES[rel]
            out.append(f'<a class="{cls}" href="{sg}.html"><span class="n">{short_no(t)}</span>'
                       f'<span class="tt">{html.escape(t)}</span></a>')
    out.append("</nav>")
    return "\n".join(out)


def pager_html(idx: int) -> str:
    if idx > 0:
        r = DOCS[idx - 1][0]
        prev_l = f'<a class="pg prev" href="{SLUG[r]}.html"><small>← 이전</small><b>{html.escape(TITLES[r])}</b></a>'
    else:
        prev_l = '<a class="pg prev" href="index.html"><small>← 이전</small><b>매뉴얼 홈</b></a>'
    next_l = ""
    if idx < len(DOCS) - 1:
        r = DOCS[idx + 1][0]
        next_l = f'<a class="pg next" href="{SLUG[r]}.html"><small>다음 →</small><b>{html.escape(TITLES[r])}</b></a>'
    return f'<div class="pager">{prev_l}{next_l}</div>'


PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — 개발자 매뉴얼</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<link rel="stylesheet" href="assets/manual.css">
</head>
<body>
<div class="progress" id="progress"></div>
<button class="menu-btn" id="menuBtn" aria-label="메뉴">☰</button>
<aside class="sidebar" id="sidebar">
{sidebar}
</aside>
<div class="scrim" id="scrim"></div>
<main class="main">
  <article class="content">
    <nav class="crumb"><a href="index.html">개발자 매뉴얼</a> <span>/</span> <em>{group}</em></nav>
    <header class="doc-head">
      <div class="dh-eyebrow"><span class="dh-chip">{group_icon} {group}</span>
        <span class="dh-src">docs/dev_manual/{src}</span></div>
      <h1>{title}</h1>
      {summary}
    </header>
    <div class="doc">
{content}
    </div>
    {pager}
  </article>
  <aside class="toc" id="toc">
{toc}
  </aside>
</main>
<button class="top" id="topBtn" title="맨 위로" aria-label="맨 위로">↑</button>
<script src="assets/manual.js"></script>
</body>
</html>
"""


INDEX = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>구독·결제 서버 — 개발자 매뉴얼</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<link rel="stylesheet" href="assets/manual.css">
</head>
<body>
<div class="progress" id="progress"></div>
<button class="menu-btn" id="menuBtn" aria-label="메뉴">☰</button>
<aside class="sidebar" id="sidebar">
{sidebar}
</aside>
<div class="scrim" id="scrim"></div>
<main class="main main-index">
  <article class="content">
    <header class="hero">
      <span class="hero-tag">DEVELOPER MANUAL</span>
      <h1>구독·결제 서버 — 개발자 매뉴얼</h1>
      <p>사내 서비스 공통 <b>구독·결제 API 서버</b> (FastAPI · PostgreSQL · Redis · htmx 어드민 · TossPayments).
         이 매뉴얼 하나로 <b>운영 · 기능 추가 · 유지보수</b>가 가능하도록, 기능별로
         “요청 → 거치는 파일/코드(file:line) → DB 테이블 → 응답”을 추적해 정리했습니다.</p>
      <div class="hero-cta">
        <a class="cta primary" href="01-getting-started.html">▶ 시작하기 (구조·실행·테스트)</a>
        <a class="cta" href="README.html">읽는 순서 안내</a>
        <a class="cta" href="admin--11-onboarding-checklist.html">신규 서비스 온보딩</a>
      </div>
      <p class="hero-note">처음이라면 <b>기반(README·01·02·03)</b>을 먼저 읽고 담당 기능 문서로 이동하세요.
        내용이 코드와 다르면 <b>코드가 정답</b>입니다.</p>
    </header>
{cards}
  </article>
</main>
<button class="top" id="topBtn" title="맨 위로" aria-label="맨 위로">↑</button>
<script src="assets/manual.js"></script>
</body>
</html>
"""


def build_cards(summaries: dict) -> str:
    out = []
    for group in GROUP_ORDER:
        out.append(f'<section class="cardsec"><div class="cardsec-h"><span class="cs-ico">{GROUP_ICON.get(group,"•")}</span>'
                   f'<div><h2>{html.escape(group)}</h2><p>{html.escape(GROUP_DESC.get(group,""))}</p></div></div>'
                   '<div class="cards">')
        for rel, g in DOCS:
            if g != group:
                continue
            t = TITLES[rel]
            desc = summaries.get(rel, "")
            out.append(
                f'<a class="card" href="{SLUG[rel]}.html"><span class="card-no">{short_no(t)}</span>'
                f'<span class="card-b"><b>{html.escape(t)}</b>'
                f'<span class="card-d">{html.escape(desc[:110])}</span></span></a>'
            )
        out.append("</div></section>")
    return "\n".join(out)


def build():
    md = markdown.Markdown(extensions=["extra", "toc", "sane_lists", "attr_list"])
    summaries = {}
    raw = {}
    for rel, _ in DOCS:
        with open(os.path.join(HERE, rel), encoding="utf-8") as f:
            raw[rel] = f.read()
        TITLES[rel] = title_of(rel, raw[rel])
        summaries[rel] = summary_text(raw[rel])

    for idx, (rel, group) in enumerate(DOCS):
        md.reset()
        body = md.convert(raw[rel])
        body = rewrite_md_links(body, os.path.dirname(rel))
        body = strip_first_h1(body)
        body, summary_html = lift_summary(body)
        body = style_blockquotes(body)
        body = badge_locs(body)
        toc = build_toc(body)
        summary_block = (f'<div class="summary"><span class="summary-k">⭐ 한 줄 요약</span>{summary_html}</div>'
                         if summary_html else "")
        pagehtml = PAGE.format(
            title=html.escape(TITLES[rel]),
            sidebar=sidebar_html(SLUG[rel]),
            group=html.escape(group),
            group_icon=GROUP_ICON.get(group, "•"),
            src=html.escape(rel),
            summary=summary_block,
            content=body,
            toc=toc or '<div class="toc-h">이 페이지 내용</div><p class="toc-empty">소제목 없음</p>',
            pager=pager_html(idx),
        )
        with open(os.path.join(HERE, SLUG[rel] + ".html"), "w", encoding="utf-8") as f:
            f.write(pagehtml)

    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX.format(sidebar=sidebar_html(""), cards=build_cards(summaries)))

    with open(os.path.join(HERE, "manual.html"), "w", encoding="utf-8") as f:
        f.write('<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
                '<meta http-equiv="refresh" content="0; url=index.html">'
                '<title>개발자 매뉴얼</title></head>'
                '<body>개발자 매뉴얼이 새 구조로 이동했습니다. '
                '<a href="index.html">index.html 로 이동</a></body></html>')

    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
    with open(os.path.join(HERE, "assets", "manual.css"), "w", encoding="utf-8") as f:
        f.write(CSS)
    with open(os.path.join(HERE, "assets", "manual.js"), "w", encoding="utf-8") as f:
        f.write(JS)
    return len(DOCS)


CSS = r""":root{
  --primary:#476CFF;--primary-50:#F5F8FF;--primary-100:#EDF2FF;--primary-200:#DCE6FF;--primary-700:#2A45C0;
  --g50:#FCFCFD;--g100:#F7F8FA;--g200:#EEF0F4;--g300:#E2E5EC;--g600:#9AA1AD;--g700:#6B7280;
  --red:#E5484D;--amber:#9A6700;--amber-bg:#FFF7E6;--amber-bd:#FCE5A8;--green:#117A55;
  --text:#161A22;--muted:#525A66;--muted-strong:#3A4150;
  --code-bg:#EDF1FB;--code-text:#2A45C0;--line:#E6E9F0;--surface:#fff;
  --sidebar-w:300px;--toc-w:236px;
  --shadow-sm:0 1px 2px rgba(20,26,44,.05);--shadow:0 6px 22px rgba(31,45,90,.10);--shadow-lg:0 14px 40px rgba(31,45,90,.16);
  --radius:14px;--mono:ui-monospace,SFMono-Regular,'JetBrains Mono',Menlo,Consolas,monospace;
  --font:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font-family:var(--font);color:var(--text);background:var(--g100);line-height:1.68;font-size:15.5px;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}
::selection{background:var(--primary-200);color:var(--primary-700)}
h1,h2,h3,h4{letter-spacing:-.01em}
.progress{position:fixed;top:0;left:0;height:3px;width:0;z-index:60;background:linear-gradient(90deg,var(--primary),#7C93FF);transition:width .08s linear}
/* 사이드바 */
.sidebar{position:fixed;top:0;left:0;width:var(--sidebar-w);height:100vh;overflow-y:auto;background:var(--surface);
  border-right:1px solid var(--line);z-index:40}
.sidebar::-webkit-scrollbar{width:8px}.sidebar::-webkit-scrollbar-thumb{background:var(--g300);border-radius:8px;border:2px solid var(--surface)}
.brand{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:11px;padding:18px 18px 15px;
  background:var(--surface);border-bottom:1px solid var(--line)}
.brand:hover{text-decoration:none}
.brand-ico{width:38px;height:38px;flex:none;display:flex;align-items:center;justify-content:center;font-size:18px;
  background:linear-gradient(135deg,var(--primary),#6E86FF);border-radius:11px;box-shadow:var(--shadow-sm)}
.brand-t b{display:block;font-size:14.5px;color:var(--text);line-height:1.25}.brand-t small{font-size:11.5px;color:var(--muted)}
.search{position:sticky;top:70px;z-index:2;padding:11px 14px;background:var(--surface);border-bottom:1px solid var(--line)}
.search input{width:100%;height:38px;padding:0 12px 0 33px;border:1px solid var(--g300);border-radius:10px;font:inherit;font-size:13px;
  outline:none;background:var(--g50) url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="%239AA1AD" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>') no-repeat 11px center;transition:border-color .15s,box-shadow .15s}
.search input:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-100);background-color:#fff}
.nav{padding:8px 0 30px}
.nav-group{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:800;color:var(--g600);text-transform:uppercase;
  letter-spacing:.05em;padding:16px 18px 5px}
.nav-link{display:flex;align-items:center;gap:9px;margin:1px 10px;padding:7px 10px;border-radius:9px;color:var(--muted-strong);
  font-size:13.2px;line-height:1.35;transition:background .12s,color .12s}
.nav-link:hover{background:var(--g100);color:var(--text);text-decoration:none}
.nav-link .n{flex:none;width:24px;height:22px;display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700;
  color:var(--g700);background:var(--g100);border:1px solid var(--g200);border-radius:6px}
.nav-link .tt{min-width:0}
.nav-link.active{background:var(--primary-100);color:var(--primary-700);font-weight:700}
.nav-link.active .n{background:var(--primary);color:#fff;border-color:var(--primary)}
.nav-link.hidden{display:none}
/* 본문 레이아웃 */
.main{margin-left:var(--sidebar-w);display:grid;grid-template-columns:minmax(0,1fr) var(--toc-w);gap:34px;
  max-width:1180px;padding:0 40px 120px}
.main-index{display:block;max-width:1080px}
.content{min-width:0;padding-top:8px}
.crumb{font-size:12.5px;color:var(--muted);padding:22px 0 14px}.crumb a{color:var(--muted)}.crumb em{color:var(--muted-strong);font-style:normal;font-weight:600}
.doc-head{padding:0 0 18px;border-bottom:1px solid var(--line);margin-bottom:20px}
.dh-eyebrow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.dh-chip{font-size:12px;font-weight:700;color:var(--primary-700);background:var(--primary-100);border-radius:999px;padding:4px 11px}
.dh-src{font-size:11px;color:var(--muted);font-family:var(--mono);background:var(--g100);border:1px solid var(--g200);border-radius:6px;padding:3px 8px}
.doc-head h1{font-size:27px;line-height:1.22;margin:4px 0 0}
.summary{display:flex;gap:10px;align-items:flex-start;margin-top:16px;padding:13px 16px;background:linear-gradient(135deg,var(--primary-50),#fff);
  border:1px solid var(--primary-200);border-left:4px solid var(--primary);border-radius:10px}
.summary-k{flex:none;font-size:12.5px;font-weight:800;color:var(--primary-700);padding-top:2px}
.summary p{margin:0;color:var(--muted-strong)}
/* 본문 */
.doc{font-size:15.5px}
.doc h2{font-size:20px;margin:34px 0 12px;padding-top:20px;border-top:1px solid var(--g200);scroll-margin-top:16px}
.doc>h2:first-child{border-top:none;padding-top:4px;margin-top:6px}
.doc h3{font-size:16.5px;margin:24px 0 8px;color:var(--muted-strong);scroll-margin-top:16px}
.doc h4{font-size:14.5px;margin:18px 0 6px;color:var(--muted)}
.doc p{margin:11px 0}
.doc ul,.doc ol{margin:11px 0;padding-left:22px}.doc li{margin:5px 0}.doc li::marker{color:var(--primary)}
.doc strong{color:var(--muted-strong);font-weight:700}
.doc a{font-weight:500}
.doc code{background:var(--code-bg);color:var(--code-text);padding:2px 6px;border-radius:5px;font-size:12.5px;font-family:var(--mono)}
.doc code.loc{background:#0E1426;color:#A9C2FF;border:1px solid #243056;padding:2px 7px 2px 20px;
  background-image:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="%237FA0FF" stroke-width="2.4"><path d="M9 18l6-6-6-6"/></svg>');background-repeat:no-repeat;background-position:6px center;font-size:11.8px;white-space:nowrap}
.doc pre{position:relative;background:#1b1f30;color:#e6e9f0;padding:18px;border-radius:12px;overflow-x:auto;font-size:13px;line-height:1.62;
  border:1px solid #2b3045;box-shadow:var(--shadow);margin:14px 0}
.doc pre::before{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:linear-gradient(90deg,var(--primary),#7C93FF);border-radius:12px 12px 0 0}
.doc pre code{background:none;color:inherit;border:none;padding:0;font-size:13px;white-space:pre}
.doc pre::-webkit-scrollbar{height:9px}.doc pre::-webkit-scrollbar-thumb{background:#3a4060;border-radius:8px}
.doc table{border-collapse:separate;border-spacing:0;width:100%;margin:16px 0;font-size:13.5px;display:block;overflow-x:auto;
  border:1px solid var(--g300);border-radius:10px}
.doc th,.doc td{border-bottom:1px solid var(--g200);border-right:1px solid var(--g200);padding:9px 12px;text-align:left;vertical-align:top}
.doc th:last-child,.doc td:last-child{border-right:none}.doc tbody tr:last-child td{border-bottom:none}
.doc th{background:#EEF2FB;color:var(--muted-strong);font-weight:700;white-space:nowrap}
.doc tbody tr:nth-child(even) td{background:var(--g50)}.doc tbody tr:hover td{background:var(--primary-50)}
.doc hr{border:none;border-top:1px solid var(--g200);margin:26px 0}
.doc img{max-width:100%;border-radius:10px;border:1px solid var(--line)}
/* 콜아웃 */
.cal{display:flex;gap:11px;margin:15px 0;padding:13px 15px;border-radius:11px;border:1px solid var(--line);background:var(--g50)}
.cal .cal-i{flex:none;font-size:15px;line-height:1.5}
.cal .cal-b{min-width:0}.cal .cal-b>:first-child{margin-top:0}.cal .cal-b>:last-child{margin-bottom:0}
.cal.easy{background:#F0FBF4;border-color:#BBE9CB}.cal.easy strong{color:var(--green)}
.cal.warn{background:var(--amber-bg);border-color:var(--amber-bd)}.cal.warn strong{color:var(--amber)}
.cal.ref{background:var(--primary-50);border-color:var(--primary-200)}
.cal.who{background:#F3F0FF;border-color:#E0D7FF}
.cal.file{background:var(--g100);border-color:var(--g200)}.cal.file .cal-b{font-family:var(--mono);font-size:12.5px}
.cal.note{background:var(--primary-50);border-color:var(--primary-200)}
/* 우측 목차 */
.toc{position:sticky;top:0;align-self:start;max-height:100vh;overflow-y:auto;padding:26px 0 60px}
.toc-h{font-size:11px;font-weight:800;color:var(--g600);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;padding-left:11px}
.toc a{display:block;padding:4px 11px;font-size:12.8px;line-height:1.4;color:var(--muted);border-left:2px solid var(--g200)}
.toc a:hover{color:var(--text);text-decoration:none;border-left-color:var(--g600)}
.toc a.toc-h3{padding-left:22px;font-size:12.3px}
.toc a.active{color:var(--primary-700);font-weight:700;border-left-color:var(--primary);background:var(--primary-50)}
.toc-empty{font-size:12px;color:var(--g600);padding-left:11px}
/* 페이저 */
.pager{display:flex;gap:14px;margin-top:40px;padding-top:22px;border-top:1px solid var(--line)}
.pg{flex:1;display:flex;flex-direction:column;gap:3px;padding:13px 16px;border:1px solid var(--line);border-radius:12px;background:var(--surface);
  box-shadow:var(--shadow-sm);transition:transform .12s,box-shadow .12s,border-color .12s}
.pg:hover{text-decoration:none;transform:translateY(-2px);box-shadow:var(--shadow);border-color:var(--primary)}
.pg small{font-size:11.5px;color:var(--muted)}.pg b{font-size:13.5px;color:var(--text)}
.pg.next{text-align:right}
/* index */
.hero{padding:40px 36px;border-radius:var(--radius);background:linear-gradient(135deg,#fff,var(--primary-50));
  border:1px solid var(--line);box-shadow:var(--shadow-sm);margin:30px 0 12px}
.hero-tag{font-size:11.5px;font-weight:800;letter-spacing:.12em;color:var(--primary);background:var(--primary-100);padding:5px 11px;border-radius:999px}
.hero h1{font-size:31px;line-height:1.2;margin:16px 0 10px}
.hero p{color:var(--muted);margin:8px 0;max-width:760px}.hero p b{color:var(--muted-strong)}
.hero-cta{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 6px}
.cta{padding:10px 16px;border-radius:10px;font-size:13.5px;font-weight:700;background:#fff;color:var(--primary-700);
  border:1px solid var(--primary-200);box-shadow:var(--shadow-sm);transition:transform .12s,box-shadow .12s}
.cta:hover{text-decoration:none;transform:translateY(-2px);box-shadow:var(--shadow)}
.cta.primary{background:var(--primary);color:#fff;border-color:var(--primary)}
.hero-note{font-size:13px}
.cardsec{margin-top:34px}
.cardsec-h{display:flex;gap:12px;align-items:center;margin-bottom:14px}
.cs-ico{width:40px;height:40px;flex:none;display:flex;align-items:center;justify-content:center;font-size:19px;background:#fff;border:1px solid var(--line);border-radius:11px;box-shadow:var(--shadow-sm)}
.cardsec-h h2{font-size:18px;margin:0}.cardsec-h p{margin:2px 0 0;font-size:12.8px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{display:flex;gap:12px;padding:14px 15px;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow-sm);
  transition:transform .12s,box-shadow .12s,border-color .12s}
.card:hover{text-decoration:none;transform:translateY(-3px);box-shadow:var(--shadow);border-color:var(--primary)}
.card-no{flex:none;width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;
  color:var(--primary-700);background:var(--primary-100);border-radius:8px}
.card-b{min-width:0;display:flex;flex-direction:column;gap:3px}
.card-b b{font-size:14px;color:var(--text);line-height:1.3}
.card-d{font-size:12.3px;color:var(--muted);line-height:1.45;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
/* 떠있는 버튼 */
.top{position:fixed;right:24px;bottom:24px;width:46px;height:46px;border:none;border-radius:50%;background:var(--primary);color:#fff;
  font-size:20px;cursor:pointer;box-shadow:var(--shadow-lg);opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;z-index:45}
.top.show{opacity:1;pointer-events:auto}.top:hover{transform:translateY(-3px)}
.menu-btn{display:none}.scrim{display:none}
/* 반응형 */
@media(max-width:1100px){.main{grid-template-columns:minmax(0,1fr)}.toc{display:none}}
@media(max-width:820px){
  .menu-btn{display:flex;align-items:center;justify-content:center;position:fixed;top:12px;left:12px;z-index:50;width:42px;height:42px;
    border:1px solid var(--line);border-radius:11px;background:#fff;font-size:18px;box-shadow:var(--shadow);cursor:pointer}
  .sidebar{transform:translateX(-100%);transition:transform .22s;box-shadow:var(--shadow-lg)}
  .sidebar.open{transform:none}
  .scrim{display:block;position:fixed;inset:0;background:rgba(10,15,30,.4);opacity:0;pointer-events:none;transition:opacity .22s;z-index:39}
  .scrim.show{opacity:1;pointer-events:auto}
  .main{margin-left:0;padding:0 16px 90px}.crumb{padding-top:60px}
  .hero{padding:26px 20px}.hero h1{font-size:25px}
}
@media print{.sidebar,.toc,.top,.progress,.menu-btn,.pager{display:none}.main{margin:0;grid-template-columns:1fr;max-width:none}
  .doc pre,.card,.pg{box-shadow:none}}
"""


JS = r"""(function(){
  var q=document.getElementById('q');
  if(q){q.addEventListener('input',function(){
    var v=this.value.trim().toLowerCase();
    document.querySelectorAll('.nav-link').forEach(function(a){
      a.classList.toggle('hidden', v && a.textContent.toLowerCase().indexOf(v)<0);
    });
  });}
  var sb=document.getElementById('sidebar'),mb=document.getElementById('menuBtn'),sc=document.getElementById('scrim');
  function close(){sb&&sb.classList.remove('open');sc&&sc.classList.remove('show');}
  if(mb){mb.addEventListener('click',function(){sb.classList.toggle('open');sc.classList.toggle('show');});}
  if(sc){sc.addEventListener('click',close);}
  var tlinks={};document.querySelectorAll('.toc a').forEach(function(a){tlinks[a.getAttribute('href').slice(1)]=a;});
  var heads=document.querySelectorAll('.doc h2[id],.doc h3[id]');
  if(heads.length){
    var obs=new IntersectionObserver(function(es){
      es.forEach(function(e){
        if(e.isIntersecting){
          document.querySelectorAll('.toc a.active').forEach(function(x){x.classList.remove('active');});
          var a=tlinks[e.target.id];if(a){a.classList.add('active');a.scrollIntoView({block:'nearest'});}
        }
      });
    },{rootMargin:'-8% 0px -78% 0px'});
    heads.forEach(function(h){obs.observe(h);});
  }
  var bar=document.getElementById('progress'),top=document.getElementById('topBtn');
  function onScroll(){
    var h=document.documentElement,s=h.scrollTop||document.body.scrollTop,max=(h.scrollHeight-h.clientHeight)||1;
    if(bar)bar.style.width=(s/max*100)+'%';
    if(top)top.classList.toggle('show',s>320);
  }
  window.addEventListener('scroll',onScroll,{passive:true});onScroll();
  if(top){top.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});}
})();
"""


def sync_to_manual():
    """매뉴얼 사이트(docs/manual/dev_manual/)로 사본 동기화.

    매뉴얼 폴더 아래(한 단계 더 깊음)에서 외부 상위 링크(../foo)가 깨지지 않도록
    HTML의 href="../ 를 href="../../ 로 보정해 복사한다. (내부 페이지 링크는 '../'가
    아니므로 영향 없음.) 대상 폴더가 없으면 조용히 건너뛴다."""
    import shutil

    dst = os.path.normpath(os.path.join(HERE, "..", "manual", "dev_manual"))
    if not os.path.isdir(dst):
        return None
    # 마크다운 원본 + build 스크립트 동기화
    for name in os.listdir(HERE):
        src = os.path.join(HERE, name)
        if name == "assets":
            continue
        if os.path.isfile(src) and not name.endswith(".html"):
            shutil.copy2(src, os.path.join(dst, name))
    # assets 동기화
    os.makedirs(os.path.join(dst, "assets"), exist_ok=True)
    for name in os.listdir(os.path.join(HERE, "assets")):
        shutil.copy2(os.path.join(HERE, "assets", name), os.path.join(dst, "assets", name))
    # HTML 은 상위 링크 깊이 보정 후 기록
    count = 0
    for name in os.listdir(HERE):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            html_text = f.read()
        html_text = html_text.replace('href="../', 'href="../../')
        with open(os.path.join(dst, name), "w", encoding="utf-8") as f:
            f.write(html_text)
        count += 1
    return dst, count


if __name__ == "__main__":
    n = build()
    print(f"생성 완료: {n}개 문서 → index.html + 페이지들 + assets/ (출력 {HERE})")
    synced = sync_to_manual()
    if synced:
        print(f"매뉴얼 사본 동기화: {synced[1]}개 HTML → {synced[0]} (상위 링크 깊이 보정 적용)")
