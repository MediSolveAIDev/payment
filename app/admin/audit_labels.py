"""감사 로그를 관리자가 이해할 수 있는 한글로 변환."""

ACTION_LABELS: dict[str, str] = {
    "auth.login": "로그인",
    "auth.login_failed": "로그인 실패",
    "auth.password_set": "비밀번호 설정",
    "account.create": "계정 생성",
    "account.update": "계정 정보 수정",
    "account.disable": "계정 비활성화",
    "account.enable": "계정 활성화",
    "account.delete": "계정 삭제",
    "account.assign_service": "서비스 담당 추가",
    "account.unassign_service": "서비스 담당 해제",
    "user.create_admin": "관리자 계정 생성",
    "user.password_reset_issued": "비밀번호 재설정 메일 발송",
    "service.register": "서비스 등록",
    "service.rotate_keys": "서비스 키 재발급",
    "service.keys_viewed": "서비스 키 조회",
    "service.update_ips": "허용 IP 변경",
    "service.set_status": "서비스 상태 변경",
    "service.delete": "서비스 삭제",
    "service.set_primary_manager": "대표 담당자 지정",
    "service.cancel_policy_updated": "취소 정책 변경",
    "service.notification_url_updated": "알림 URL 변경",
    # 토스 시크릿 키 설정/변경 — 평문은 감사 detail에 절대 기록하지 않음(Task 8)
    "service.toss_secret_key.set": "토스 시크릿 키 설정",
    "service.toss_secret_key.changed": "토스 시크릿 키 변경",
    "plan.create": "요금제 생성",
    "plan.update": "요금제 수정",
    "plan.archive": "요금제 비활성화",
    "plan.activate": "요금제 활성화",
    "plan.delete": "요금제 삭제",
    "plan.bonus_days": "사용일 추가(보너스)",
    "subscription.create": "구독 생성",
    "subscription.cancel": "구독 취소",
    "subscription.resume": "구독 재개",
    "subscription.change_card": "카드 변경",
    "subscription.force_cancel": "구독 강제 취소",
    "subscription.extended": "만료일 연장",
    "subscription.usage_added": "사용일 추가",
    "subscription.renewed": "구독 자동연장 결제",
    "subscription.suspended": "구독 정지(재시도 소진)",
    "subscription.expired": "구독 만료",
    "subscription.payment_failed": "갱신 결제 실패",
    "subscription.renewal_unresolved": "갱신 결제 결과 불명",
    "subscription.first_payment_failed": "첫 결제 실패",
    "subscription.first_payment_unresolved": "첫 결제 결과 불명",
    "subscription.manual_pay": "수동 결제(정지 복구)",
    "subscription.manual_pay_failed": "수동 결제 실패",
    "subscription.manual_pay_unresolved": "수동 결제 결과 불명",
    "card.register": "카드 등록",
    "card.replace": "카드 교체",
    "card.delete": "카드 삭제",
    "card.activate": "카드 활성화",
    "card.deactivate": "카드 비활성화",
    "payment.one_off": "단건 결제",
    "payment.one_off_failed": "단건 결제 실패",
    "payment.one_off_unresolved": "단건 결제 결과 불명",
    "payment.canceled": "결제 취소",
    "payment.cancel_failed": "결제 취소 실패",
    "payment.reconciled_done": "결제 정산 확정(성공)",
    "payment.reconciled_failed": "결제 정산 확정(실패)",
    "audit.purge": "감사로그 삭제",
    # 전역설정(요청 013)
    "settings.retry_updated": "재시도 설정 변경",
    "settings.security_policy_updated": "보안/결제 정책 변경",
    "settings.admin_ips_updated": "어드민 IP 변경",
    "server.disabled": "결제서버 비활성화",
    "server.enabled": "결제서버 활성화",
}

ACTOR_TYPE_LABELS = {"USER": "관리자", "SERVICE": "외부 서비스", "SYSTEM": "시스템"}
TARGET_TYPE_LABELS = {
    "service": "서비스", "plan": "요금제", "subscription": "구독",
    "user": "계정", "payment": "결제", "card": "카드",
}

# detail JSONB에서 사람에게 보여줄 단일 필드 → 한글 라벨
_DETAIL_FIELDS = {
    "name": "이름", "email": "이메일", "role": "역할", "status": "상태",
    "code": "사유코드", "reason": "사유", "amount": "금액",
    "refund": "환불액", "fee": "취소 수수료", "ips": "IP",
    "service_count": "서비스 수", "external_user_id": "사용자",
    # service_id는 스코프 필터(_events_tab)용으로만 detail에 저장 — 원시 UUID라 화면 표시는 생략.
    # 사람이 읽을 서비스명은 service_name으로 표시한다.
    "service_name": "서비스",
    "days": "추가 일수", "affected_count": "적용 구독 수",
    "manager_emails": "담당자", "ip_count": "IP 개수",
    "cancel_enabled": "취소 허용", "cancel_fee_percent": "취소 수수료율(%)",
    "plan_name": "요금제", "phone": "전화번호", "order_id": "주문번호",
    "note": "내용",   # 키재발급/키조회/메일발송 등 설명 텍스트
    "card_number": "카드번호", "issuer": "발급사",  # 카드 등록/교체/활성토글 상세
}

# old_<base>/new_<base> 쌍으로 기록된 "변경 전 → 변경 후" 표시 대상 → 한글 라벨.
# 설정값·요금제 수정·서비스/계정 변경 등에서 무엇이 어떻게 바뀌었는지 보여준다.
_DIFF_FIELDS = {
    "retry_limit": "재시도 횟수", "interval_hours": "재시도 간격(시간)",
    "grace_days": "유예일수", "ips": "허용 IP",
    # 보안/결제 정책(settings.security_policy_updated)
    "max_failed_logins": "로그인 실패 잠금 임계치", "account_lock_minutes": "잠금 지속(분)",
    "one_off_max_amount": "단건결제 최대 금액",
    # 킬스위치 사유 변경(server.disabled/enabled — old_reason/new_reason)
    "reason": "사유",
    "price": "정가", "name": "이름", "billing_cycle": "결제주기",
    "cycle_days": "주기일수",
    "status": "상태", "enabled": "취소 허용", "fee_percent": "취소 수수료율(%)",
    "email": "이메일", "phone": "전화번호",
    "period_end": "만료일", "next_billing_at": "다음 결제일",
    # 요금제 수정 상세(요청) — 할인은 '정률 N% / 정액 N원'으로 결합 기록(비율/값 구분)
    "first_payment": "첫결제 할인", "recurring_discount": "상시 할인",
    "trial_enabled": "체험 제공", "trial_days": "체험일수",
    "auto_renew": "자동갱신", "extra_info": "추가정보",
}


def _fmt_detail_value(base: str, value) -> str:
    """detail 값 하나를 표시 문자열로 변환.

    금액류는 천단위 콤마+'원', IP 등 목록은 콤마 결합, 빈값(None/""/[])은 '없음'.
    """
    if value is None or value == "" or value == []:
        return "없음"
    if base in ("price", "amount", "one_off_max_amount") and isinstance(value, int):
        return f"{value:,}원"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def actor_label(actor_type: str, actor_email: str | None) -> str:
    if actor_email:
        return actor_email
    return ACTOR_TYPE_LABELS.get(actor_type, actor_type)


def target_label(target_type: str | None, target_name: str | None) -> str:
    if target_type is None:
        return "-"
    ko = TARGET_TYPE_LABELS.get(target_type, target_type)
    return f"{ko} · {target_name}" if target_name else ko


def detail_summary(detail: dict | None) -> str:
    """감사로그 detail(dict) → 한 줄 요약 문자열.

    1) old_<base>/new_<base> 쌍은 '라벨 변경전 → 변경후'로 표시(실제 바뀐 항목만).
    2) 나머지 단일 필드는 '라벨 값'으로 표시.
    """
    if not detail:
        return ""
    parts: list[str] = []
    consumed: set[str] = set()
    # 1) 변경 전/후 쌍 → "라벨 변경전 → 변경후"
    for base, label in _DIFF_FIELDS.items():
        ok, nk = f"old_{base}", f"new_{base}"
        if ok not in detail and nk not in detail:
            continue
        consumed.update({ok, nk})
        old_raw, new_raw = detail.get(ok), detail.get(nk)
        if old_raw == new_raw:
            continue  # 값이 바뀌지 않은 항목은 생략
        parts.append(f"{label} {_fmt_detail_value(base, old_raw)} → "
                     f"{_fmt_detail_value(base, new_raw)}")
    # 2) 단일 필드(변경 쌍이 아닌 부가정보)
    for key, label in _DETAIL_FIELDS.items():
        if key in consumed or key not in detail or detail[key] in (None, ""):
            continue
        val = detail[key]
        if key in ("amount", "refund", "fee") and isinstance(val, int):
            val = f"{val:,}원"
        parts.append(f"{label} {val}")
    return " · ".join(parts)
