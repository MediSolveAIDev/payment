"""트랜잭션 이메일 HTML 템플릿 — 이메일 클라이언트 호환을 위해 인라인 스타일을 사용한다.

이메일은 <style> 태그·외부 CSS가 무시되는 클라이언트가 많으므로 모든 스타일을 인라인으로 둔다.
값은 escape로 이스케이프해 HTML 인젝션을 막는다(링크 href는 서버 생성 신뢰 URL).
"""
from html import escape

# 브랜드/팔레트(관리자 이벤트 메일과 톤 통일)
_BRAND = "구독·결제 시스템"
_PRIMARY = "#2563eb"
_INK = "#0f172a"
_MUTED = "#64748b"
_FAINT = "#94a3b8"
_FONT = "Pretendard,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"


def render_action_email(*, title: str, intro: str, button_label: str,
                        button_url: str, note: str | None = None,
                        footer: str | None = None) -> tuple[str, str]:
    """제목·안내문·CTA 버튼이 있는 액션 메일의 (평문, HTML)을 만든다.

    - title/intro/button_label/note/footer: 사용자에게 보이는 텍스트(escape 처리).
    - button_url: 서버가 생성한 신뢰 링크. 버튼과 평문 대체 링크에 사용한다.
    반환한 평문은 HTML 미지원 클라이언트용 대체 본문이다.
    """
    note = note or "이 메일은 시스템이 자동 발송했습니다."
    footer = footer or "본인이 요청하지 않았다면 이 메일을 무시하셔도 됩니다."

    # ── 평문(대체 본문) ──────────────────────────────────────────────────────────
    text = (f"{title}\n\n{intro}\n\n"
            f"{button_label}: {button_url}\n\n{note}\n{footer}")

    # ── HTML 본문(인라인 스타일) ─────────────────────────────────────────────────
    href = escape(button_url, quote=True)
    html = f"""\
<div style="margin:0;padding:24px 12px;background:#f1f5f9;font-family:{_FONT}">
  <div style="max-width:480px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;
              border-radius:14px;overflow:hidden">
    <div style="padding:18px 28px;border-bottom:1px solid #eef2f7">
      <span style="font-size:15px;font-weight:700;color:{_INK}">💳 {escape(_BRAND)}</span>
    </div>
    <div style="padding:28px">
      <h1 style="margin:0 0 12px;font-size:19px;color:{_INK}">{escape(title)}</h1>
      <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:{_MUTED}">{escape(intro)}</p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 22px">
        <tr><td style="border-radius:8px;background:{_PRIMARY}">
          <a href="{href}" target="_blank" rel="noopener"
             style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:600;
                    color:#fff;text-decoration:none;border-radius:8px">{escape(button_label)}</a>
        </td></tr>
      </table>
      <p style="margin:0 0 6px;font-size:13px;color:{_MUTED}">{escape(note)}</p>
      <p style="margin:0;font-size:12px;color:{_FAINT};word-break:break-all">
        버튼이 열리지 않으면 아래 주소를 복사해 브라우저에 붙여넣으세요:<br>
        <a href="{href}" style="color:{_PRIMARY}">{escape(button_url)}</a>
      </p>
    </div>
    <div style="padding:14px 28px;border-top:1px solid #eef2f7;background:#fafcff">
      <p style="margin:0;font-size:12px;color:{_FAINT}">{escape(footer)}</p>
    </div>
  </div>
</div>"""
    return text, html
