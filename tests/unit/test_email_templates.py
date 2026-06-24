"""트랜잭션 이메일 HTML 템플릿(render_action_email) 단위 테스트."""
from app.notifications.email_templates import render_action_email

_URL = "https://pay.example.com/admin/setup-password?token=abc123"


def test_action_email_has_button_title_and_link():
    text, html = render_action_email(
        title="비밀번호 재설정 안내", intro="아래 버튼을 눌러 설정하세요.",
        button_label="비밀번호 재설정하기", button_url=_URL,
        note="48시간 동안 유효합니다.")
    # HTML: 제목·버튼 라벨·CTA href·안내문 포함
    assert "비밀번호 재설정 안내" in html
    assert "비밀번호 재설정하기" in html
    assert f'href="{_URL}"' in html            # 버튼 링크(원본 URL)
    assert "48시간 동안 유효합니다." in html
    # 평문 대체 본문: 제목·링크 포함
    assert "비밀번호 재설정 안내" in text and _URL in text


def test_action_email_escapes_text_but_keeps_link():
    # 표시 텍스트는 escape(인젝션 방지), href의 &는 속성 인코딩(&amp;)
    text, html = render_action_email(
        title="<script>x</script>", intro="i", button_label="b",
        button_url="https://x.test/p?a=1&b=2")
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "a=1&amp;b=2" in html               # href 속성 인코딩


def test_action_email_default_note_and_footer():
    _, html = render_action_email(title="t", intro="i", button_label="b",
                                  button_url=_URL)
    assert "자동 발송" in html                  # 기본 note
    assert "무시" in html                        # 기본 footer
