#!/usr/bin/env python3
"""user_manual 마크다운을 '사용자 매뉴얼 + 개발자 매뉴얼' 사이트(HTML)로 변환한다.

요청 018: 처음 사용하는 사람이 전반을 이해하는 '사용자 매뉴얼'과,
설치·배포·API·기능 코드 흐름을 다루는 '개발자 매뉴얼'을 하나의 사이트로 묶는다.
좌측 네비 · 우측 목차 · 콜아웃 · 코드위치 배지 · 검색 · 프로세스 다이어그램(.flow)을 지원한다.

CSS/JS는 검증된 dev_manual 자산을 재사용(+ 다이어그램 스타일 추가)한다.

실행: uv run --with markdown python docs/user_manual/build.py
"""
import html
import os
import re

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))

# 사이드바 표시 순서·그룹.
DOCS = [
    ("00-overview.md", "사용자 매뉴얼"),
    ("01-admin-console.md", "사용자 매뉴얼"),
    ("19-admin-services.md", "사용자 매뉴얼"),   # 서비스 관리(목록·등록·키) — 콘솔 다음에 배치
    ("02-admin-card.md", "사용자 매뉴얼"),
    ("03-admin-subscription.md", "사용자 매뉴얼"),
    ("04-admin-plan.md", "사용자 매뉴얼"),
    ("05-admin-payment-refund.md", "사용자 매뉴얼"),
    ("06-admin-accounts.md", "사용자 매뉴얼"),
    ("07-admin-settings.md", "사용자 매뉴얼"),
    ("08-admin-audit.md", "사용자 매뉴얼"),
    ("09-dashboard.md", "사용자 매뉴얼"),
    ("18-status-reference.md", "사용자 매뉴얼"),
    ("10-install-deploy.md", "개발자 매뉴얼"),
    ("11-service-api.md", "개발자 매뉴얼"),
    ("12-feature-card.md", "개발자 매뉴얼"),
    ("13-feature-subscription.md", "개발자 매뉴얼"),
    ("14-feature-payment.md", "개발자 매뉴얼"),
    ("15-feature-notifications.md", "개발자 매뉴얼"),
    ("16-admin-screens.md", "개발자 매뉴얼"),
    ("17-sample-service.md", "개발자 매뉴얼"),
]

GROUP_ICON = {"사용자 매뉴얼": "📘", "개발자 매뉴얼": "🛠️"}
GROUP_DESC = {
    "사용자 매뉴얼": "처음 사용하는 사람을 위한 전체 프로세스·관리자 콘솔·대시보드 안내.",
    "개발자 매뉴얼": "설치·설정·배포(docker), 서비스용 API, 기능별 코드 흐름, 어드민 화면.",
}


def slug(relpath):
    return relpath[:-3].replace("/", "--")


SLUG = {rel: slug(rel) for rel, _ in DOCS}
GROUP_ORDER = list(dict.fromkeys(g for _, g in DOCS))
TITLES = {}


def title_of(relpath, text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return relpath


def short_no(title):
    m = re.match(r"\s*([0-9]+)[.\s]", title)
    return m.group(1) if m else "·"


def plain(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[`*_>#|]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def summary_text(text):
    m = re.search(r"#+\s*[0-9.\s]*한\s*줄\s*요약\s*\n+([^\n]+)", text)
    if m:
        return plain(m.group(1))
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ">", "-", "|", "```", "<")):
            continue
        return plain(s)
    return ""


def convert_leftover_md_links(htm):
    """raw HTML 블록(<ol class="steps"> 등) 안의 마크다운 링크 `[글](url)`를 <a>로 변환.

    md_in_html이 raw HTML 내부 인라인 마크다운은 변환하지 않아 `[글](파일.md)`가
    그대로 남는다. <pre>/<code>(코드)는 건드리지 않도록 보호한 뒤 변환한다.
    변환 결과의 href="...md"는 이어서 rewrite_md_links가 .html로 바꾼다.
    """
    holds = []

    def stash(m):
        holds.append(m.group(0))
        return f"\x00H{len(holds) - 1}\x00"

    tmp = re.sub(r"<pre>.*?</pre>", stash, htm, flags=re.S)
    tmp = re.sub(r"<code[^>]*>.*?</code>", stash, tmp, flags=re.S)
    tmp = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', tmp)
    for i, h in enumerate(holds):
        tmp = tmp.replace(f"\x00H{i}\x00", h)
    return tmp


def rewrite_md_links(htm, doc_dir):
    def repl(m):
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


def badge_locs(htm):
    return re.sub(
        r"<code>([^<]+)</code>",
        lambda m: f'<code class="loc">{m.group(1)}</code>' if LOC_RE.match(m.group(1)) else m.group(0),
        htm,
    )


CALLOUT_RULES = [
    (("쉽게 말하면", "비유하면", "비유로", "한마디로"), "easy", "🍎"),
    (("주의", "경고", "중요", "위험"), "warn", "⚠️"),
    (("상호참조", "관련 문서", "관련 테스트", "다음 문서", "함께 보기"), "ref", "🔗"),
    (("대상 독자", "대상", "권한"), "who", "👤"),
    (("파일 위치", "파일"), "file", "📄"),
    (("참고", "팁", "TIP", "예시", "예)"), "note", "💡"),
]


def style_blockquotes(htm):
    def repl(m):
        inner = m.group(1)
        text = re.sub(r"<[^>]+>", "", inner).lstrip()
        for keys, cls, ico in CALLOUT_RULES:
            if any(text.startswith(k) for k in keys):
                return f'<blockquote class="cal {cls}"><span class="cal-i">{ico}</span><div class="cal-b">{inner}</div></blockquote>'
        return f'<blockquote class="cal note"><span class="cal-i">💬</span><div class="cal-b">{inner}</div></blockquote>'

    return re.sub(r"<blockquote>(.*?)</blockquote>", repl, htm, flags=re.S)


def strip_first_h1(htm):
    return re.sub(r"<h1[^>]*>.*?</h1>", "", htm, count=1, flags=re.S)


def build_toc(htm):
    items = []
    for m in re.finditer(r'<(h2|h3)\s+id="([^"]+)">(.*?)</\1>', htm, re.S):
        lvl, hid = m.group(1), m.group(2)
        txt = html.escape(re.sub(r"<[^>]+>", "", m.group(3)).strip())
        items.append(f'<a class="toc-{lvl}" href="#{hid}">{txt}</a>')
    if not items:
        return ""
    return '<div class="toc-h">이 페이지 내용</div>\n' + "\n".join(items)


def sidebar_html(active):
    out = ['<a class="brand" href="index.html"><span class="brand-ico">💳</span>'
           '<span class="brand-t"><b>구독·결제 서버</b><small>사용·개발 매뉴얼</small></span></a>',
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


def pager_html(idx):
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
<title>{title} — 구독·결제 서버 매뉴얼</title>
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
    <nav class="crumb"><a href="index.html">매뉴얼 홈</a> <span>/</span> <em>{group}</em></nav>
    <header class="doc-head">
      <div class="dh-eyebrow"><span class="dh-chip">{group_icon} {group}</span></div>
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
<title>구독·결제 서버 — 사용·개발 매뉴얼</title>
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
      <span class="hero-tag">USER &amp; DEVELOPER MANUAL</span>
      <h1>구독·결제 서버 매뉴얼</h1>
      <p>사내 서비스 공통 <b>구독·결제 API 서버</b>를 <b>이해하고 운영</b>하고, <b>연동·확장</b>하기 위한 안내서입니다.
         처음이라면 왼쪽 <b>사용자 매뉴얼</b>부터, 연동·배포가 목적이면 <b>개발자 매뉴얼</b>을 보세요.</p>
      <div class="entry2">
        <a class="entry" href="00-overview.html"><span class="entry-ico">📘</span>
          <b>사용자 매뉴얼</b><span>처음 사용자를 위한 전체 프로세스·관리자 콘솔·대시보드 안내</span></a>
        <a class="entry" href="10-install-deploy.html"><span class="entry-ico">🛠️</span>
          <b>개발자 매뉴얼</b><span>설치·배포(docker)·서비스 API·기능별 코드 흐름·어드민 화면</span></a>
      </div>
    </header>
{cards}
  </article>
</main>
<button class="top" id="topBtn" title="맨 위로" aria-label="맨 위로">↑</button>
<script src="assets/manual.js"></script>
</body>
</html>
"""


def build_cards(summaries):
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
    summaries, raw = {}, {}
    for rel, _ in DOCS:
        path = os.path.join(HERE, rel)
        if not os.path.exists(path):
            raw[rel] = f"# {rel}\n\n(작성 예정)\n"
        else:
            with open(path, encoding="utf-8") as f:
                raw[rel] = f.read()
        TITLES[rel] = title_of(rel, raw[rel])
        summaries[rel] = summary_text(raw[rel])

    for idx, (rel, group) in enumerate(DOCS):
        md.reset()
        body = md.convert(raw[rel])
        body = convert_leftover_md_links(body)   # raw HTML 블록 내부 [글](url) → <a>
        body = rewrite_md_links(body, os.path.dirname(rel))
        body = strip_first_h1(body)
        body = style_blockquotes(body)
        body = badge_locs(body)
        toc = build_toc(body)
        pagehtml = PAGE.format(
            title=html.escape(TITLES[rel]), sidebar=sidebar_html(SLUG[rel]),
            group=html.escape(group), group_icon=GROUP_ICON.get(group, "•"),
            summary="", content=body,
            toc=toc or '<div class="toc-h">이 페이지 내용</div><p class="toc-empty">소제목 없음</p>',
            pager=pager_html(idx))
        with open(os.path.join(HERE, SLUG[rel] + ".html"), "w", encoding="utf-8") as f:
            f.write(pagehtml)

    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX.format(sidebar=sidebar_html(""), cards=build_cards(summaries)))

    # CSS/JS — 검증된 dev_manual 자산 재사용 + 다이어그램 스타일 추가
    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
    dev_assets = os.path.join(HERE, "..", "dev_manual", "assets")
    base_css = open(os.path.join(dev_assets, "manual.css"), encoding="utf-8").read()
    with open(os.path.join(HERE, "assets", "manual.css"), "w", encoding="utf-8") as f:
        f.write(base_css + DIAGRAM_CSS)
    base_js = open(os.path.join(dev_assets, "manual.js"), encoding="utf-8").read()
    with open(os.path.join(HERE, "assets", "manual.js"), "w", encoding="utf-8") as f:
        f.write(base_js)
    return len(DOCS)


DIAGRAM_CSS = r"""
/* ── 프로세스 다이어그램(.flow) — 사용자 매뉴얼 그림 설명 ─────────────────── */
.flow{display:flex;flex-wrap:wrap;gap:18px;align-items:stretch;margin:20px 0}
.flow-step{flex:1;min-width:128px;background:#fff;border:1px solid var(--primary-200);border-radius:12px;
  padding:13px 15px;position:relative;box-shadow:var(--shadow-sm)}
.flow-step .fn{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:7px;
  background:var(--primary);color:#fff;font-size:12px;font-weight:800;margin-bottom:7px}
.flow-step b{display:block;color:var(--primary-700);font-size:13.5px;margin-bottom:3px}
.flow-step span{font-size:12.5px;color:var(--muted);line-height:1.5}
.flow-step:not(:last-child)::after{content:"→";position:absolute;right:-15px;top:50%;transform:translateY(-50%);
  color:var(--primary);font-weight:800;font-size:18px;z-index:2}
@media(max-width:640px){.flow{flex-direction:column}
  .flow-step:not(:last-child)::after{content:"↓";right:auto;left:50%;top:auto;bottom:-18px;transform:translateX(-50%)}}
/* 세로 단계(.steps) */
.steps{counter-reset:st;margin:18px 0;padding:0;list-style:none}
.steps>li{position:relative;padding:2px 0 16px 40px;margin:0}
.steps>li::before{counter-increment:st;content:counter(st);position:absolute;left:0;top:0;width:26px;height:26px;
  display:flex;align-items:center;justify-content:center;background:var(--primary-100);color:var(--primary-700);
  font-weight:800;font-size:13px;border-radius:50%}
.steps>li:not(:last-child)::after{content:"";position:absolute;left:13px;top:28px;bottom:0;width:2px;background:var(--g200)}
/* 시퀀스 다이어그램(서비스 입장 연동 그림) */
.seqwrap{margin:18px 0;overflow-x:auto;border:1px solid var(--line);border-radius:14px;background:#fff;
  box-shadow:var(--shadow-sm);padding:6px}
.seqwrap svg{display:block;width:100%;min-width:680px;height:auto}
.seqwrap .lane{fill:#fff;stroke:var(--primary-200)}
.seqwrap .lane-t{fill:var(--primary-700);font-weight:800;font-size:13px}
.seqwrap .life{stroke:var(--g300);stroke-width:1.5;stroke-dasharray:4 4}
.seqwrap .msg{stroke:var(--primary);stroke-width:2}
.seqwrap .msg2{stroke:var(--g600);stroke-width:2}
.seqwrap .lbl{fill:var(--muted-strong);font-size:12px}
.seqwrap .lbl b{font-weight:800;fill:var(--primary-700)}
.seqwrap .band{fill:var(--g100)}
.seqwrap .band-t{fill:var(--muted);font-size:11.5px;font-weight:700}
.seqwrap text{font-family:var(--font)}
/* 역할/상태 칩 */
.pill{display:inline-block;font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;
  background:var(--g100);border:1px solid var(--g200);color:var(--muted-strong)}
.pill.ok{background:#EAF8F1;border-color:#BBE9CB;color:var(--green)}
.pill.no{background:#FFEFEF;border-color:#FAD1D1;color:var(--red)}
.pill.pri{background:var(--primary-100);border-color:var(--primary-200);color:var(--primary-700)}
/* ── 매뉴얼 본문 가로 전체 폭 사용(중앙 max-width 제한 해제) ──────────────────── */
.main{max-width:none}
.main-index{max-width:none}
"""


if __name__ == "__main__":
    n = build()
    print(f"생성 완료: {n}개 문서 → index.html + 페이지들 + assets/ (출력 {HERE})")
