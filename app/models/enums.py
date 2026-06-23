"""결제·구독 시스템 전반에 걸쳐 사용되는 상태·유형 열거형 모음.

결제 흐름은 두 갈래로 분리된다.
  - 구독(SUBSCRIPTION): 정기 자동결제. 최초 billingKey 발급 후 서버 측에서 반복 청구.
  - 단건(ONE_OFF): billingKey 없이 즉시 결제. 구독과 무관하게 독립 처리.

ACCESS_ALLOWED_STATUSES / OPEN_SUBSCRIPTION_STATUSES 는 이 파일 하단에 정의되며
구독 상태 머신의 핵심 집합으로 다른 모듈에서 직접 임포트해 사용한다.
"""
from enum import StrEnum


class ServiceStatus(StrEnum):
    ACTIVE = "ACTIVE"      # 정상 운영 중인 서비스
    INACTIVE = "INACTIVE"  # 비활성화된 서비스(API 키 인증 불가)


class UserRole(StrEnum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"        # 전체 관리자 — 서비스·관리자 계정 전체 접근
    SERVICE_MANAGER = "SERVICE_MANAGER"  # 서비스 담당자 — 자신이 담당하는 서비스만 접근


class UserStatus(StrEnum):
    PENDING = "PENDING"    # 생성됨, 비밀번호 설정 대기
    ACTIVE = "ACTIVE"      # 정상
    LOCKED = "LOCKED"      # 로그인 연속 실패로 잠김(자동 해제)
    DISABLED = "DISABLED"  # 관리자가 비활성화(복구 가능)
    DELETED = "DELETED"    # 관리자가 삭제(소프트, 숨김)


class BillingCycle(StrEnum):
    """요금제 결제 주기. DAY 선택 시 Plan.cycle_days, MINUTE 선택 시 Plan.cycle_minutes(5 이상)를 함께 지정.

    MINUTE는 자동연장 테스트용이며 비운영 환경(environment != prod)에서만 생성 가능하다.
    """

    YEAR = "YEAR"      # 연 단위 결제
    MONTH = "MONTH"    # 월 단위 결제
    WEEK = "WEEK"      # 주 단위 결제
    DAY = "DAY"        # 일 단위 결제(cycle_days로 실제 일수 지정)
    MINUTE = "MINUTE"  # 분 단위 결제(cycle_minutes로 실제 분 지정, 최소 5분; 테스트용·비운영 전용)


class FirstPaymentType(StrEnum):
    """첫 구독 시 적용되는 특별 혜택 유형.

    FREE: 첫 결제 금액을 0으로 처리(무료).
    DISCOUNT_AMOUNT: first_payment_value(원)만큼 금액 차감.
    DISCOUNT_PERCENT: first_payment_value(%)만큼 비율 할인.
    NONE이면 정상 금액 그대로 청구.
    """

    NONE = "NONE"                          # 첫 결제 혜택 없음
    FREE = "FREE"                          # 첫 결제 무료(0원)
    DISCOUNT_AMOUNT = "DISCOUNT_AMOUNT"    # 첫 결제 정액 할인(원)
    DISCOUNT_PERCENT = "DISCOUNT_PERCENT"  # 첫 결제 정률 할인(%)


class DiscountType(StrEnum):
    """상시(정기) 결제 할인 유형. 첫 결제(FirstPaymentType)와 달리 FREE 없음."""
    NONE = "NONE"
    DISCOUNT_AMOUNT = "DISCOUNT_AMOUNT"
    DISCOUNT_PERCENT = "DISCOUNT_PERCENT"


class PlanStatus(StrEnum):
    ACTIVE = "ACTIVE"      # 신규 구독 가능한 정상 요금제
    ARCHIVED = "ARCHIVED"  # 신규 구독 불가(기존 구독은 유지); 구독이 남아있으면 삭제 불가


class SubscriptionStatus(StrEnum):
    TRIAL = "TRIAL"          # 체험 — 만료 시 첫 정기 결제
    ACTIVE = "ACTIVE"        # 정상 이용
    PAST_DUE = "PAST_DUE"    # 결제 실패/유예(접근 유지)
    SUSPENDED = "SUSPENDED"  # 강제 정지(접근 차단) — 수동 결제 대기
    CANCELED = "CANCELED"    # 해지 예약(만료일까지 유지)
    EXTENDED = "EXTENDED"    # 연장처리 — 운영자가 만료일을 수동 연장(요청). 이용 허용·새 만료일에 자동결제 갱신
    EXPIRED = "EXPIRED"      # 완전 종료(종단)


# 서비스 접근 권한(O/X). 외부 서비스가 access_allowed로 판단.
ACCESS_ALLOWED_STATUSES = frozenset({
    SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELED,
    SubscriptionStatus.EXTENDED,   # 연장처리 — 이용 허용
})
# 구독이 '열려 있는'(서비스+사용자 당 1개 규칙) 상태 — EXPIRED만 제외.
OPEN_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED,
    SubscriptionStatus.CANCELED, SubscriptionStatus.EXTENDED,
)


def access_allowed(status: str) -> bool:
    return status in ACCESS_ALLOWED_STATUSES


class PaymentStatus(StrEnum):
    """개별 결제 레코드의 처리 상태."""

    PENDING = "PENDING"    # 결제 요청 생성됨, 토스 승인 응답 대기
    DONE = "DONE"          # 토스 승인 완료
    FAILED = "FAILED"      # 토스 거절 또는 네트워크 오류
    CANCELED = "CANCELED"  # 승인 후 취소 처리됨


class PaymentType(StrEnum):
    """구독 결제의 회차 구분(단건은 ONE_OFF만 사용)."""

    FIRST = "FIRST"      # 최초 결제(첫 구독 시 할인 적용 대상)
    RENEWAL = "RENEWAL"  # 정기 자동 갱신 결제
    RETRY = "RETRY"      # PAST_DUE 상태에서 재시도한 결제
    ONE_OFF = "ONE_OFF"  # 단건(구독 무관) 결제


class PaymentKind(StrEnum):
    """결제의 큰 분류 — 구독 정기결제 vs 단건 결제."""

    SUBSCRIPTION = "SUBSCRIPTION"  # 구독에 묶인 정기(자동) 결제
    ONE_OFF = "ONE_OFF"            # 구독과 무관한 단건 즉시 결제


class WebhookStatus(StrEnum):
    """토스페이먼츠로부터 수신한 웹훅 이벤트의 처리 상태."""

    RECEIVED = "RECEIVED"    # 수신됨, 아직 처리 전
    PROCESSED = "PROCESSED"  # 정상 처리 완료
    IGNORED = "IGNORED"      # 중복·무관 이벤트로 무시
    FAILED = "FAILED"        # 처리 중 오류 발생
