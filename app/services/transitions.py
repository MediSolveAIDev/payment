"""구독 상태 전이 중앙화 — 허용 전이 테이블 + transition() 헬퍼 (감사 Phase 4 — S1).

과거에는 `sub.status = SubscriptionStatus.X` 직접 대입이 11곳(subscriptions/renewals/
reconciliation)에 흩어져 있어, "EXPIRED는 종단" 같은 상태 머신 규칙이 호출부의
if문과 주석에만 존재했고 잘못된 전이(예: EXPIRED→ACTIVE)를 어디서도 막지 못했다.
이제 모든 상태 변경은 이 모듈의 transition()을 거친다.

상태 머신 (docs/dev_manual/05 문서와 동일):
    TRIAL ──→ ACTIVE ──→ PAST_DUE ──→ SUSPENDED ──→ EXPIRED
      │         │  ↑        │  ↑          │
      │         │  └────────┘  │          └──(수동결제)──→ ACTIVE
      └────┬────┴──────────────┘
           ↓
       CANCELED ──→ EXPIRED        (재개: CANCELED → ACTIVE | PAST_DUE)

역할 분담:
- transition()은 **전이 허용 검증 + 보편 불변식**(아래)만 책임진다.
- 전이별 고유 필드(기간 전진, 재시도 스케줄, 체험 즉시만료 등)는 호출측이
  transition() 호출 **후에** 설정한다 — 정책(얼마 뒤 재시도 등)은 호출측 소관.

보편 불변식(어느 경로로 전이하든 항상 참이어야 하는 것):
- EXPIRED/CANCELED 진입 → next_billing_at=None (자동결제 중지; CANCELED 재개 시
  호출측이 다시 설정)
- SUSPENDED 진입 → suspended_at=now 기록 + next_billing_at=None (유예 판정 기준)
- ACTIVE 진입 → retry_count=0, suspended_at=None (정상 복귀 시 실패 흔적 초기화)
"""
from datetime import datetime

from app.core.clock import utcnow
from app.models import Subscription, SubscriptionStatus


class InvalidStateTransition(RuntimeError):
    """허용되지 않은 구독 상태 전이 — 도메인 규칙 위반(프로그래밍 오류).

    사용자 입력 오류가 아니라 코드 버그이므로 DomainError(HTTP 매핑)가 아닌
    RuntimeError 계열로 분류한다 — 발생 시 500으로 드러나 즉시 수정 대상이 된다.
    """


# 상태별 허용 전이 집합. 여기 없는 (현재 → 새) 조합은 InvalidStateTransition.
# 자기 자신으로의 전이는 갱신(ACTIVE→ACTIVE)·재시도 실패(PAST_DUE→PAST_DUE)에 필요하다.
ALLOWED_TRANSITIONS: dict[SubscriptionStatus, frozenset[SubscriptionStatus]] = {
    SubscriptionStatus.TRIAL: frozenset({
        SubscriptionStatus.ACTIVE,      # 체험 만료 → 첫 정기결제 성공
        SubscriptionStatus.PAST_DUE,    # 체험 만료 결제 실패 → 재시도
        SubscriptionStatus.SUSPENDED,   # 재시도 소진 → 정지
        SubscriptionStatus.CANCELED,    # 체험 중 취소(즉시 만료 예약)
        SubscriptionStatus.EXTENDED,    # 운영자 만료일 연장(요청)
    }),
    SubscriptionStatus.ACTIVE: frozenset({
        SubscriptionStatus.ACTIVE,      # 정기 갱신 성공(기간 전진)
        SubscriptionStatus.PAST_DUE,    # 갱신 결제 실패 → 재시도
        SubscriptionStatus.SUSPENDED,   # 재시도 소진 → 정지
        SubscriptionStatus.CANCELED,    # 사용자/관리자 취소
        SubscriptionStatus.EXTENDED,    # 운영자 만료일 연장(요청)
        SubscriptionStatus.EXPIRED,     # 첫 결제 실패·미체결 확정·비자동갱신 기간 종료
    }),
    SubscriptionStatus.PAST_DUE: frozenset({
        SubscriptionStatus.ACTIVE,      # 재시도/수동 결제 성공 → 복귀
        SubscriptionStatus.PAST_DUE,    # 재시도 실패(횟수 증가 후 재예약)
        SubscriptionStatus.SUSPENDED,   # 재시도 소진 → 정지
        SubscriptionStatus.CANCELED,    # 미수 상태에서 취소
        SubscriptionStatus.EXTENDED,    # 운영자 만료일 연장(요청)
    }),
    SubscriptionStatus.SUSPENDED: frozenset({
        SubscriptionStatus.ACTIVE,      # 수동 결제 성공 → 복귀(기준일 리셋)
        SubscriptionStatus.CANCELED,    # 정지 상태에서 취소
        SubscriptionStatus.EXTENDED,    # 운영자 만료일 연장(요청)
        SubscriptionStatus.EXPIRED,     # 유예(suspended_grace) 초과 → 최종 만료
    }),
    SubscriptionStatus.CANCELED: frozenset({
        SubscriptionStatus.ACTIVE,      # 만료 전 재개(resume) — 미수금 없음
        SubscriptionStatus.PAST_DUE,    # 만료 전 재개 — 미수금 있어 즉시 재시도
        SubscriptionStatus.EXTENDED,    # 운영자 만료일 연장(요청)
        SubscriptionStatus.EXPIRED,     # 기간 만료 → 최종 종료
    }),
    # 연장처리(요청): 활성 구독과 동등하게 동작 — 새 만료일에 자동결제 갱신/취소/만료/재연장.
    SubscriptionStatus.EXTENDED: frozenset({
        SubscriptionStatus.ACTIVE,      # 새 만료일 갱신 결제 성공 → 정상 복귀
        SubscriptionStatus.PAST_DUE,    # 갱신 결제 실패 → 재시도
        SubscriptionStatus.SUSPENDED,   # 재시도 소진 → 정지
        SubscriptionStatus.CANCELED,    # 관리자 강제 취소
        SubscriptionStatus.EXTENDED,    # 재연장(만료일 추가 연장)
        SubscriptionStatus.EXPIRED,     # 비자동갱신/미체결 종료
    }),
    # EXPIRED는 종단 상태 — 어떤 전이도 불가(재이용은 신규 구독으로).
    SubscriptionStatus.EXPIRED: frozenset(),
}


def transition(sub: Subscription, new_status: SubscriptionStatus,
               *, now: datetime | None = None) -> SubscriptionStatus:
    """구독 상태를 전이하고 보편 불변식을 적용한다. 변경 전 상태를 반환.

    허용되지 않은 전이는 InvalidStateTransition — 호출측 버그이므로 잡지 말고
    드러나게 둔다(배치는 항목별 예외 격리가 이미 있어 전체가 죽지 않는다).

    전이별 고유 필드(기간/재시도 스케줄 등)는 이 함수 호출 후 호출측이 설정한다.
    """
    old = SubscriptionStatus(sub.status)
    if new_status not in ALLOWED_TRANSITIONS[old]:
        raise InvalidStateTransition(
            f"허용되지 않은 구독 상태 전이: {old} → {new_status} (sub={sub.id})")
    sub.status = new_status
    # ── 보편 불변식 — 모든 경로에서 동일하게 적용 ──
    if new_status in (SubscriptionStatus.EXPIRED, SubscriptionStatus.CANCELED):
        sub.next_billing_at = None        # 자동결제 중지(재개 시 호출측이 재설정)
    elif new_status == SubscriptionStatus.SUSPENDED:
        sub.suspended_at = now or utcnow()  # 유예(suspended_grace) 판정 기준 시각
        sub.next_billing_at = None          # 자동결제 중지(수동결제만 가능)
    elif new_status == SubscriptionStatus.ACTIVE:
        sub.retry_count = 0               # 정상 복귀 — 실패 카운터 초기화
        sub.suspended_at = None           # 정지 흔적 제거
    return old
