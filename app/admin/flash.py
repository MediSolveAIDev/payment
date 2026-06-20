"""Admin flash 메시지 유틸리티.

Admin 화면은 서버 측 세션(쿠키)이 아닌 리다이렉트 URL의 쿼리스트링을
flash 메시지 전달 수단으로 사용한다.

흐름:
  1. POST 처리 후 결과 메시지를 ?flash=<인코딩된 메시지>&flash_type=<타입> 형태로
     리다이렉트 URL에 붙여 303 응답한다.
  2. 이동된 GET 페이지의 템플릿이 flash 파라미터를 읽어 토스트 알림을 표시한다.
  3. 사용자가 페이지를 새로고침하면 쿼리스트링이 없어 메시지가 사라진다(1회성).

flash_type 기본값은 "success". 오류는 "error"를 사용한다.
"""

from urllib.parse import quote

EMAIL_FAIL_MSG = "메일 발송에 실패했습니다. SMTP 설정을 확인하세요"


def email_flash_qs(sent: bool, success_msg: str) -> str:
    """메일 발송 결과를 리다이렉트 URL에 붙일 flash 쿼리스트링으로 변환한다.

    Args:
        sent: EmailSender.send()의 반환값. True면 성공, False면 실패.
        success_msg: 성공 시 표시할 메시지 (URL 인코딩 전 원본 문자열).

    Returns:
        "flash=<메시지>" 또는 "flash=<오류메시지>&flash_type=error" 형태의 문자열.
        호출 측에서 리다이렉트 URL 뒤에 "?" 또는 "&"로 연결해 사용한다.
    """
    if sent:
        return f"flash={quote(success_msg)}"
    return f"flash={quote(EMAIL_FAIL_MSG)}&flash_type=error"
