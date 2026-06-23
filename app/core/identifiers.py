"""외부 서비스 사용자 식별자(external_user_id) 정규화·검증.

전역 룰: external_user_id 는 **반드시 이메일**이어야 한다.
사내 서비스들이 사용자를 이메일로 식별·전달하므로, 구독/결제 서버도 이메일을
사용자 키로 통일한다. 이를 코드로 강제해 다음 문제를 방지한다.
  - 대소문자/공백 차이로 같은 사용자가 다른 식별자로 중복 생성되는 것
    (예: "User@x.com" 과 "user@x.com" 이 서로 다른 구독/카드로 잡히는 것)
  - 이메일이 아닌 임의 문자열이 섞여 들어오는 것

정규화는 서비스 전 구간에서 동일하게 적용해야 저장·조회가 일관된다(쓰기 때 저장한
값과 읽기 때 조회하는 값이 항상 같은 형태가 되도록).
"""
import re

from app.core.errors import InputValidationError

# 실용적 이메일 형식 검사: 공백 없는 local@domain.tld 한 개.
# (RFC 전수 검증 대신 운영상 충분한 보수적 패턴 — 외부 의존성 없이 사용)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# DB 컬럼(String(255))·이메일 최대 길이(254)에 맞춘 상한
MAX_EXTERNAL_USER_ID_LEN = 255


def normalize_external_user_id(value: str) -> str:
    """external_user_id 를 정규화하고 이메일 형식을 검증해 반환한다.

    - 앞뒤 공백 제거 후 **소문자**로 정규화한다(대소문자/공백 차이로 인한 중복 방지).
    - 이메일 형식이 아니거나 255자를 초과하면 InputValidationError(422)를 던진다.

    쓰기(등록)·읽기(조회) 모든 진입점에서 이 함수를 거친 값을 사용해야 한다.
    """
    normalized = (value or "").strip().lower()
    if (not normalized
            or len(normalized) > MAX_EXTERNAL_USER_ID_LEN
            or not _EMAIL_RE.match(normalized)):
        raise InputValidationError("external_user_id는 이메일 형식이어야 합니다")
    return normalized
