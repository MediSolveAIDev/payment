#!/usr/bin/env python3
"""new_manual(인수인계 가이드) 마크다운을 하나의 HTML로 묶는다.

dev_manual(기능별 상세 레퍼런스)과 달리 읽는 순서가 있는 학습 경로형 가이드다.

목적: 초급 개발자가 이 HTML 한 파일만으로 프로젝트 운영·기능추가·유지보수를 할 수 있게,
기존에 검증된 마크다운(기능 16 + 어드민 11 + 온보딩)을 그대로 변환해 사이드바 목차·검색·
문서 간 링크가 동작하는 단일 페이지로 만든다.

실행: uv run --with markdown python docs/new_manual/build_html.py
출력: docs/new_manual/index.html
"""
import html
import os
import re

import markdown  # uv run --with markdown 로 제공

HERE = os.path.dirname(os.path.abspath(__file__))

# 사이드바에 표시할 순서와 그룹. (relpath, group) — relpath는 HERE 기준.
DOCS = [
    ("README.md", "시작 — 이 가이드 사용법"),
    ("01-first-day.md", "첫 주 — 순서대로 읽기"),
    ("02-big-picture.md", "첫 주 — 순서대로 읽기"),
    ("03-domain.md", "첫 주 — 순서대로 읽기"),
    ("04-code-map.md", "첫 주 — 순서대로 읽기"),
    ("08-recipes.md", "작업 — 기능을 추가할 때"),
    ("05-database.md", "사전 — 필요할 때 찾기"),
    ("06-auth-ops.md", "사전 — 필요할 때 찾기"),
    ("07-batch-and-incidents.md", "사전 — 필요할 때 찾기"),
    ("09-testing.md", "사전 — 필요할 때 찾기"),
    ("10-faq.md", "사전 — 필요할 때 찾기"),
    ("11-known-issues.md", "주의 — 고치기 전에 반드시"),
]


def slug(relpath: str) -> str:
    """문서 relpath → 단일 페이지 내 섹션 id. 예: admin/03-services.md → doc-admin--03-services"""
    return "doc-" + relpath[:-3].replace("/", "--")


def title_of(relpath: str, text: str) -> str:
    """문서의 첫 H1(# ...)을 제목으로. 없으면 파일명."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return relpath


# relpath -> slug 매핑(링크 재작성용)
SLUG = {rel: slug(rel) for rel, _ in DOCS}


def rewrite_md_links(htm, doc_dir: str) -> str:
    """변환된 HTML 안의 .md 하이퍼링크를 단일 페이지 내 앵커(#doc-...)로 재작성.

    doc_dir 기준으로 상대경로를 정규화해 우리 문서 집합에 있으면 #slug 로,
    없으면(외부: docs/toss, ../claude 등) 원래 href 유지.
    """
    def repl(m: re.Match) -> str:
        href = m.group(1)
        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
            anchor = "#" + anchor
        if not href.endswith(".md"):
            return m.group(0)
        # doc_dir 기준 정규화 → HERE 기준 relpath
        target = os.path.normpath(os.path.join(doc_dir, href))
        target = target.replace(os.sep, "/")
        if target in SLUG:
            return f'href="#{SLUG[target]}"'
        return m.group(0)  # 우리 집합 밖 → 원본 유지

    return re.sub(r'href="([^"]+)"', repl, htm)


def build() -> str:
    md = markdown.Markdown(extensions=["extra", "toc", "sane_lists", "attr_list"])
    sections = []
    nav_groups: dict[str, list[tuple[str, str]]] = {}
    for rel, group in DOCS:
        path = os.path.join(HERE, rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        md.reset()
        body = md.convert(text)
        doc_dir = os.path.dirname(rel)
        body = rewrite_md_links(body, doc_dir)
        sid = SLUG[rel]
        title = title_of(rel, text)
        sections.append(
            f'<section id="{sid}" class="doc">\n'
            f'<div class="doc-src">파일: docs/new_manual/{html.escape(rel)}</div>\n'
            f"{body}\n</section>"
        )
        nav_groups.setdefault(group, []).append((sid, title))

    # 사이드바 네비게이션
    nav_html = []
    for group in dict.fromkeys(g for _, g in DOCS):  # 정의 순서 유지
        nav_html.append(f'<div class="nav-group">{html.escape(group)}</div>')
        for sid, title in nav_groups[group]:
            nav_html.append(f'<a class="nav-link" href="#{sid}" data-target="{sid}">{html.escape(title)}</a>')
    nav = "\n".join(nav_html)

    content = "\n".join(sections)
    return PAGE.replace("{{NAV}}", nav).replace("{{CONTENT}}", content)


# ── 단일 페이지 셸(임베디드 CSS/JS) ─────────────────────────────────────────────
PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>구독·결제 서버 — 인수인계 가이드 (초보 개발자용)</title>
<style>
  :root{
    --primary:#476CFF; --primary-100:#F0F4FF; --primary-700:#2A45C0;
    --g100:#FBFBFB; --g200:#F3F3F3; --g300:#E3E3E3; --g600:#9F9F9F; --g700:#6E6E6E; --g800:#3E3E3E;
    --red:#FF4E51; --text:#1a1a1a; --sidebar-w:300px;
    /* 가독성 토큰(2026-06-11): 흰 배경에서 잘 보이는 보조 텍스트/배경 —
       기존 g600(#9F9F9F)·primary-100 배경이 바탕과 비슷해 안 보이는 문제 해소 */
    --muted:#525A66;            /* 보조 텍스트 — 대비 7:1 이상 */
    --muted-strong:#3A4150;     /* 네비 등 진한 보조 텍스트 */
    --code-bg:#EDF1FB; --code-text:#2A45C0; --code-line:#D5DEF5;  /* 인라인 코드 */
    --quote-bg:#E9EFFF; --th-bg:#E7ECF7;    /* 인용구·표 헤더 배경(흰색과 확실히 구분) */
    --font:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:var(--font);color:var(--text);background:#fff;line-height:1.6;font-size:15px}
  a{color:var(--primary);text-decoration:none}
  a:hover{text-decoration:underline}
  /* 레이아웃 */
  .sidebar{position:fixed;top:0;left:0;width:var(--sidebar-w);height:100vh;overflow-y:auto;
    border-right:1px solid var(--g300);background:var(--g100);padding:16px 0}
  .brand{padding:8px 20px 14px;border-bottom:1px solid var(--g300);margin-bottom:8px}
  .brand b{display:block;font-size:15px}
  .brand small{color:var(--muted);font-size:12px}
  .search{padding:10px 16px}
  .search input{width:100%;height:38px;padding:0 12px;border:1px solid var(--g300);border-radius:8px;
    font:inherit;font-size:13px;outline:none}
  .search input:focus{border-color:var(--primary)}
  .nav-group{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;
    letter-spacing:.04em;padding:14px 20px 4px}
  .nav-link{display:block;padding:6px 20px;font-size:13.5px;color:var(--muted-strong);border-left:3px solid transparent}
  .nav-link:hover{background:var(--primary-100);text-decoration:none}
  .nav-link.active{background:var(--primary-100);color:var(--primary);border-left-color:var(--primary);font-weight:600}
  .nav-link.hidden{display:none}
  .content{margin-left:var(--sidebar-w);padding:0 48px 120px;max-width:1000px}
  .hero{padding:40px 0 8px;border-bottom:1px solid var(--g300);margin-bottom:24px}
  .hero h1{font-size:28px;margin:0 0 8px}
  .hero p{color:var(--muted);margin:6px 0}
  .quick{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}
  .quick a{background:var(--primary-100);color:var(--primary-700);border:1px solid var(--code-line);padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600}
  /* 문서 섹션 */
  .doc{padding:32px 0;border-bottom:1px dashed var(--g300);scroll-margin-top:16px}
  .doc-src{font-size:11px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin-bottom:8px}
  .doc h1{font-size:24px;margin:8px 0 16px;padding-bottom:8px;border-bottom:2px solid var(--primary)}
  .doc h2{font-size:19px;margin:28px 0 10px}
  .doc h3{font-size:16px;margin:22px 0 8px}
  .doc h4{font-size:14px;margin:18px 0 6px;color:var(--muted-strong)}
  .doc p{margin:8px 0}
  .doc ul,.doc ol{margin:8px 0;padding-left:22px}
  .doc li{margin:3px 0}
  /* 인라인 코드: 테두리 없이 파란 배경+글자만으로 구분(2026-06-11) —
     테두리는 디렉터리 트리/촘촘한 텍스트에서 사각형 외곽선 클러터를 만들어 제거.
     pre(코드블록) 안의 code는 배경/색/테두리를 모두 리셋해 외곽선이 생기지 않게 한다. */
  .doc code{background:var(--code-bg);color:var(--code-text);padding:1px 5px;border-radius:4px;font-size:13px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .doc pre{background:#1e2233;color:#e6e9f0;padding:14px 16px;border-radius:10px;overflow-x:auto;font-size:13px}
  .doc pre code{background:none;color:inherit;border:none;padding:0}
  .doc table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px;display:block;overflow-x:auto}
  .doc th,.doc td{border:1px solid var(--g300);padding:7px 10px;text-align:left;vertical-align:top}
  .doc th{background:var(--th-bg);color:var(--muted-strong);font-weight:700;white-space:nowrap}
  .doc tbody tr:nth-child(even) td{background:var(--g100)}
  .doc blockquote{margin:12px 0;padding:10px 14px;background:var(--quote-bg);
    border-left:4px solid var(--primary);border-radius:0 8px 8px 0;color:#222A38}
  .doc blockquote p{margin:4px 0}
  .doc hr{border:none;border-top:1px solid var(--g300);margin:20px 0}
  .top{position:fixed;right:24px;bottom:24px;background:var(--primary);color:#fff;width:44px;height:44px;
    border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;cursor:pointer;
    box-shadow:0 4px 14px rgba(0,0,0,.2);border:none}
  @media(max-width:900px){
    .sidebar{position:static;width:100%;height:auto;border-right:none;border-bottom:1px solid var(--g300)}
    .content{margin-left:0;padding:0 20px 80px}
  }
  @media print{.sidebar,.top{display:none}.content{margin:0;max-width:none}.doc{break-inside:avoid}}
</style>
</head>
<body>
<nav class="sidebar">
  <div class="brand"><b>구독·결제 서버</b><small>인수인계 가이드 — 학습 경로형</small></div>
  <div class="search"><input id="q" type="text" placeholder="목차 검색…" autocomplete="off"></div>
  <a class="nav-link" href="#hero" data-target="hero">⌂ 시작 / 이 문서 사용법</a>
  {{NAV}}
</nav>
<main class="content">
  <section id="hero" class="hero" style="scroll-margin-top:16px">
    <h1>구독·결제 서버 — 인수인계 가이드</h1>
    <p>이 프로젝트를 <b>처음 맡는 개발자</b>가 혼자서 운영·유지보수·기능추가까지 할 수 있게 만드는
       학습 경로형 가이드입니다. 첫날 셋업부터 큰그림·도메인 규칙·기능 추가 레시피·장애 대응까지
       <b>읽는 순서대로</b> 배치했습니다.</p>
    <p style="color:var(--muted);font-size:13px">기능별 상세 레퍼런스는
       <a href="../dev_manual/manual.html"><b>개발자 매뉴얼(dev_manual)</b></a>을 함께 보세요 —
       이 가이드가 지도라면 dev_manual은 백과사전입니다.</p>
    <div class="quick">
      <a href="#doc-01-first-day">▶ 첫날: 셋업·실행</a>
      <a href="#doc-03-domain">▶ 도메인: 상태머신·결제 3원칙</a>
      <a href="#doc-08-recipes">▶ 기능 추가 레시피</a>
      <a href="#doc-07-batch-and-incidents">▶ 장애 대응</a>
      <a href="#doc-11-known-issues">▶ 알려진 이슈</a>
    </div>
    <p style="font-size:12px;color:var(--muted);margin-top:16px">
      이 HTML은 <code>docs/new_manual/</code>의 마크다운을 변환해 생성됩니다.
      내용이 코드와 다르면 <b>코드가 정답</b>입니다. 갱신: <code>uv run --with markdown python docs/new_manual/build_html.py</code></p>
  </section>
  {{CONTENT}}
</main>
<button class="top" title="맨 위로" onclick="scrollTo({top:0,behavior:'smooth'})">↑</button>
<script>
  // 사이드바 검색 — 목차 항목 필터
  var q=document.getElementById('q');
  q.addEventListener('input',function(){
    var v=this.value.trim().toLowerCase();
    document.querySelectorAll('.nav-link').forEach(function(a){
      a.classList.toggle('hidden', v && a.textContent.toLowerCase().indexOf(v)<0);
    });
  });
  // 스크롤스파이 — 현재 섹션 네비 강조
  var links={};
  document.querySelectorAll('.nav-link').forEach(function(a){links[a.dataset.target]=a;});
  var obs=new IntersectionObserver(function(es){
    es.forEach(function(e){
      if(e.isIntersecting){
        document.querySelectorAll('.nav-link.active').forEach(function(x){x.classList.remove('active');});
        var a=links[e.target.id]; if(a){a.classList.add('active');
          a.scrollIntoView({block:'nearest'});}
      }
    });
  },{rootMargin:'-10% 0px -80% 0px'});
  document.querySelectorAll('section[id]').forEach(function(s){obs.observe(s);});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    out = os.path.join(HERE, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print("생성:", out)
