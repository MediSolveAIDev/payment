# -*- coding: utf-8 -*-
"""QA 테스트케이스 정의 + 저장소 pytest 함수 매핑.

각 케이스는 (ID, 우선순위, 제목, [pytest 함수명...]) 형태.
- pytest 함수명은 junit 결과의 testcase name(파라미터 제외)과 매칭된다.
- 함수명 리스트가 비어 있으면 '수동(MANUAL)' 케이스로 처리한다.
- 매핑한 함수가 결과에 없으면 'N/A(미발견)'로 표시되어 매핑 노후화를 드러낸다.

매뉴얼 10장(10-qa-testcases.html)의 케이스와 ID가 1:1로 일치한다.
"""

MODULES = {
    "A": "인증·로그인·계정",
    "B": "서비스 관리",
    "C": "요금제",
    "D": "구독 생성",
    "E": "구독 운영(상태 전이)",
    "F": "자동 갱신·재시도·정산 스윕",
    "G": "단건 결제·환불",
    "H": "결제·정산 화면",
    "I": "API 인증·보안",
    "J": "웹훅(토스 → 서버)",
    "K": "권한 격리(담당자 범위)",
    "L": "감사 로그·전체 설정·킬스위치",
}

# (ID, 우선순위, 제목, [pytest 함수명...])
CASES = [
    # ── A. 인증·로그인·계정 ──────────────────────────────────────────────
    ("A-01", "P1", "올바른 자격증명 로그인", ["test_login_success_and_dashboard", "test_login_success_creates_redis_session"]),
    ("A-02", "P1", "틀린 비밀번호 로그인", ["test_login_wrong_password_generic_error", "test_wrong_password_shows_error"]),
    ("A-03", "P1", "없는 이메일 로그인(존재 비노출)", ["test_login_unknown_email_same_error_shape", "test_login_errors_do_not_reveal_account_existence"]),
    ("A-04", "P1", "비밀번호 5회 실패 잠금", ["test_lockout_after_5_failures", "test_lockout_via_http", "test_lock_expires_and_allows_login"]),
    ("A-05", "P2", "로그인 요청 제한", ["test_login_rate_limit_blocks_after_threshold"]),
    ("A-06", "P1", "비밀번호 설정 플로우", ["test_setup_password_full_flow", "test_setup_password_with_valid_token", "test_enable_account_without_password_is_pending"]),
    ("A-07", "P2", "만료된 설정 링크", ["test_setup_password_rejects_expired_token"]),
    ("A-08", "P2", "약한 비밀번호 거부", ["test_setup_password_rejects_weak_password"]),
    ("A-09", "P2", "PENDING 계정 로그인 불가", ["test_pending_user_cannot_login", "test_pending_user_cannot_login_http"]),
    ("A-10", "P2", "비활성/삭제 계정 로그인 거부", ["test_login_rejected_for_disabled_account", "test_login_rejected_for_deleted_account"]),
    ("A-11", "P1", "비밀번호 변경 시 세션 무효화", ["test_password_change_destroys_sessions_and_other_tokens", "test_password_reset_destroys_existing_session", "test_reset_password_destroys_target_user_sessions"]),
    ("A-12", "P2", "로그아웃 후 세션 무효", ["test_logout_destroys_session", "test_old_session_invalid_after_logout", "test_bogus_session_cookie_redirects"]),
    ("A-13", "P3", "세션 만료(유휴/절대수명)", ["test_session_absolute_expiry", "test_session_key_has_ttl"]),
    ("A-14", "P2", "중복 이메일 계정 거부", ["test_create_duplicate_email_conflicts", "test_account_edit_duplicate_email_blocked", "test_create_system_admin_duplicate_email_conflicts"]),
    ("A-15", "P3", "자기/대표 담당자 삭제 차단", ["test_cannot_delete_self", "test_delete_account_blocked_when_primary_manager"]),

    # ── B. 서비스 관리 ──────────────────────────────────────────────────
    ("B-01", "P1", "서비스 등록+키 발급", ["test_register_service_creates_keys_and_assigns_managers", "test_api_key_format_and_uniqueness"]),
    ("B-02", "P1", "키 1회만 노출", ["test_register_service_shows_keys_once", "test_keys_modal_shows_keys_and_audits"]),
    ("B-03", "P2", "담당자 없으면 등록 불가", ["test_new_service_form_no_managers_shows_guide", "test_register_rejects_empty_managers"]),
    ("B-04", "P2", "중복 서비스명 거부", ["test_register_duplicate_name_conflicts", "test_register_whitespace_duplicate_name_conflicts"]),
    ("B-05", "P1", "IP 빈값/잘못/IPv6 거부", ["test_register_rejects_empty_ip_list", "test_register_rejects_invalid_ip", "test_validate_ips_rejects_ipv6"]),
    ("B-06", "P2", "대표 담당자 필수", ["test_register_without_primary_shows_error", "test_register_primary_auto_included"]),
    ("B-07", "P1", "키 재발급 시 기존 키 무효화", ["test_rotate_keys_invalidates_old", "test_rotated_key_invalidates_old", "test_rotate_keys_invalidates_old_hash"]),
    ("B-08", "P2", "허용 IP 갱신/차단", ["test_update_allowed_ips", "test_update_ips", "test_ip_not_in_whitelist_rejected"]),
    ("B-09", "P2", "취소 정책 변경", ["test_service_cancel_policy_update_via_form", "test_service_create_with_cancel_policy"]),
    ("B-10", "P2", "담당자 추가/대표 변경/제거", ["test_service_detail_assign_and_remove_manager", "test_set_primary_manager_via_post"]),
    ("B-11", "P1", "서비스 비활성화", ["test_set_service_status", "test_inactive_service_rejected"]),
    ("B-12", "P1", "구독 있는 서비스 삭제 차단", ["test_delete_service_blocked_when_subscription_exists", "test_delete_service_with_subscription_blocked"]),
    ("B-13", "P3", "빈 서비스 삭제", ["test_delete_service_without_subscriptions", "test_delete_service_cascades_manager_user"]),
    ("B-14", "P3", "무인증 목록(민감정보 없음)", ["test_services_list_no_auth_no_secrets", "test_service_list_can_be_disabled"]),

    # ── C. 요금제 ───────────────────────────────────────────────────────
    ("C-01", "P1", "월/년/주 요금제 생성", ["test_create_plan_month", "test_week", "test_year"]),
    ("C-02", "P1", "일 주기 일수 검증", ["test_create_plan_day_requires_cycle_days", "test_day_with_cycle_days", "test_day_requires_cycle_days"]),
    ("C-03", "P2", "일 외 주기에 일수 거부", ["test_create_plan_non_day_rejects_cycle_days"]),
    ("C-04", "P1", "첫구독 할인 계산", ["test_first_subscription_discount_amount", "test_plan_first_amount_percent_on_full_price", "test_discount_amount_floors_at_zero"]),
    ("C-05", "P1", "상시 할인 계산", ["test_recurring_amount_none_amount_percent", "test_recurring_discount_can_reach_zero", "test_plan_recurring_amount"]),
    ("C-06", "P2", "첫구독+상시 독립 적용", ["test_first_and_recurring_discounts_are_independent", "test_plan_first_amount_ignores_recurring_discount"]),
    ("C-07", "P2", "할인율 범위/0 거부", ["test_percent_out_of_range_rejected", "test_zero_discount_value_rejected", "test_create_plan_validates_price_and_discount"]),
    ("C-08", "P2", "체험+자동결제안함 조합", ["test_auto_renew_false_allows_trial", "test_trial_with_no_auto_renew_keeps_first_charge_schedule"]),
    ("C-09", "P1", "결제 주기 불변", ["test_update_plan_billing_cycle_immutable"]),
    ("C-10", "P2", "추가정보 키/값", ["test_collect_extra_info_duplicate_key_last_wins", "test_collect_extra_info_empty_key_error", "test_invalid_extra_info_returns_form_error_not_500_create"]),
    ("C-11", "P2", "보관/재활성화", ["test_archive_plan_hides_from_active_list", "test_activate_plan_restores_to_active_list", "test_archived_plan_not_subscribable"]),
    ("C-12", "P1", "구독 있는 요금제 삭제 차단", ["test_delete_plan_blocked_when_subscription_exists", "test_plan_delete_conflict_error_shown_in_list"]),
    ("C-13", "P3", "가격 변경 감사", ["test_update_price_audited_with_old_and_new", "test_update_plan"]),

    # ── D. 구독 생성 ────────────────────────────────────────────────────
    ("D-01", "P1", "정상 구독 생성", ["test_create_subscription_endpoint", "test_create_with_full_price"]),
    ("D-02", "P1", "첫구독 무료", ["test_first_subscription_free_skips_charge", "test_free_benefit_not_repeatable"]),
    ("D-03", "P1", "첫구독 할인", ["test_first_subscription_discount_amount"]),
    ("D-04", "P1", "체험 시작", ["test_create_trial_subscription_api", "test_create_trial_no_charge_period_is_trial_days"]),
    ("D-05", "P1", "체험 미지원에 trial 요청 거부", ["test_trial_rejected_when_plan_has_no_trial"]),
    ("D-06", "P1", "중복 구독 409", ["test_duplicate_subscription_409", "test_duplicate_subscription_conflicts", "test_one_subscription_per_service_user_enforced_by_db"]),
    ("D-07", "P1", "금액 주입 무시", ["test_create_subscription_ignores_injected_amount"]),
    ("D-08", "P2", "보관 요금제 구독 불가", ["test_archived_plan_not_subscribable"]),
    ("D-09", "P2", "비활성 서비스 구독 거부", ["test_inactive_service_rejected"]),
    ("D-10", "P2", "잘못된 customer_key", ["test_invalid_customer_key_rejected"]),
    ("D-11", "P2", "빌링키 발급 실패", ["test_billing_key_issue_failure", "test_first_charge_failure_not_persisted_keeps_benefit"]),
    ("D-12", "P1", "동시 생성 1건만 성공", ["test_concurrent_create_only_one_wins", "test_redis_lock_prevents_double_charge"]),
    ("D-13", "P2", "만료 후 재구독(정가)", ["test_expired_subscription_allows_resubscribe", "test_resubscribe_after_expiry_pays_full_price"]),
    ("D-14", "P1", "구독 조회(access_allowed)", ["test_get_subscription_status", "test_access_allowed_flag_for_suspended"]),

    # ── E. 구독 운영 ────────────────────────────────────────────────────
    ("E-01", "P1", "구독 취소(해지 예약)", ["test_cancel_active_subscription", "test_cancel_and_resume_endpoints"]),
    ("E-02", "P2", "이미 취소된 구독 재취소 409", ["test_cancel_already_canceled_conflicts"]),
    ("E-03", "P2", "만료 전 재개", ["test_resume_before_period_end", "test_resume_paths_from_canceled"]),
    ("E-04", "P2", "만료 후 재개 거부", ["test_resume_after_period_end_conflicts"]),
    ("E-05", "P2", "체험 취소 즉시 종료", ["test_trial_cancel_is_immediate"]),
    ("E-06", "P1", "만료일 연장", ["test_extend_subscription_sets_extended_and_dates", "test_subscription_extend_via_admin"]),
    ("E-07", "P2", "연장 거부(과거/만료)", ["test_extend_subscription_rejects_expired", "test_extend_subscription_rejects_past_date"]),
    ("E-08", "P1", "강제 취소(환불 별도)", ["test_force_cancel_subscription"]),
    ("E-09", "P1", "즉시 재결제 복구", ["test_admin_retry_payment_revives_and_audits_as_user", "test_retry_success_restores_active_continuous_period", "test_admin_retry_payment_rejects_active"]),
    ("E-10", "P1", "수동 결제로 정지 복구", ["test_manual_pay_api_revives_suspended", "test_manual_pay_revives_suspended_and_resets_anchor"]),
    ("E-11", "P2", "수동결제 상태 조건", ["test_manual_pay_requires_suspended", "test_manual_pay_allows_past_due"]),
    ("E-12", "P2", "카드 변경", ["test_change_card", "test_change_card_endpoint", "test_change_card_on_past_due_schedules_immediate_retry"]),
    ("E-13", "P3", "카드 변경 실패 시 기존 키 유지", ["test_change_card_issue_failure_keeps_old_key", "test_change_card_survives_old_key_delete_failure"]),
    ("E-14", "P2", "사용일 추가(단건)", ["test_add_usage_days_extends_active", "test_add_days_endpoint", "test_add_usage_days_keeps_none_next_billing"]),
    ("E-15", "P2", "add-days 거부 조건", ["test_add_usage_days_rejects_non_active_state", "test_add_usage_days_rejects_bad_days", "test_add_usage_days_no_subscription"]),
    ("E-16", "P2", "요금제 일괄 사용일추가", ["test_add_bonus_days", "test_plan_bonus_days_extends_subscriptions", "test_add_bonus_days_rejects_non_positive"]),

    # ── F. 자동 갱신·재시도 ─────────────────────────────────────────────
    ("F-01", "P1", "만료일 자동 갱신", ["test_renews_due_subscription", "test_run_renewals_processes_due"]),
    ("F-02", "P1", "갱신 실패 → PAST_DUE+알림", ["test_failure_moves_to_past_due_and_notifies"]),
    ("F-03", "P1", "재시도 소진 → SUSPENDED", ["test_retries_exhausted_suspends_and_keeps_key", "test_full_retry_storyline_to_suspended"]),
    ("F-04", "P2", "유예 내 수동결제 복구", ["test_suspended_within_grace_kept", "test_manual_pay_revives_suspended_and_resets_anchor"]),
    ("F-05", "P1", "유예 경과 → EXPIRED", ["test_suspended_expires_after_grace"]),
    ("F-06", "P2", "체험 만료 첫 결제", ["test_trial_expiry_charges_to_active", "test_trial_charge_failure_goes_past_due"]),
    ("F-07", "P2", "자동결제 안함 만료", ["test_non_renewing_expires_at_period_end"]),
    ("F-08", "P1", "취소 구독 만료일 종료", ["test_canceled_expires_at_period_end_without_charge", "test_cancel_and_expire_stop_billing"]),
    ("F-09", "P2", "결제 타임아웃 PENDING 유지", ["test_one_off_timeout_pending", "test_renewal_timeout_unresolved_preserved_then_converges"]),
    ("F-10", "P3", "타임아웃 실제 승인 확정", ["test_timeout_with_actual_approval_resolves_done", "test_renewal_timeout_resolved_by_lookup", "test_crash_recovery_done_payment_advances_without_recharge"]),
    ("F-11", "P3", "스케줄러 락 중복방지", ["test_run_renewals_skips_when_global_lock_held", "test_redis_lock_prevents_double_charge"]),
    ("F-12", "P3", "월말/윤년 만료일 보정", ["test_month_end_clamps", "test_month_end_leap_year", "test_leap_year_boundaries"]),

    # ── G. 단건 결제·환불 ───────────────────────────────────────────────
    ("G-01", "P1", "단건 결제 생성", ["test_one_off_success_deletes_billing_key"]),
    ("G-02", "P1", "멱등(같은 order_id)", ["test_one_off_idempotent_same_order_id", "test_fake_idempotent_replay_same_key"]),
    ("G-03", "P2", "서비스별 order_id 격리", ["test_one_off_same_order_id_isolated_per_service", "test_fake_duplicate_order_different_key_rejected"]),
    ("G-04", "P1", "금액/order_id 제약", ["test_one_off_amount_over_cap_rejected"]),
    ("G-05", "P2", "카드 거절 실패", ["test_one_off_card_declined_failed"]),
    ("G-06", "P1", "취소 수수료 0% 전액 환불", ["test_cancel_full_refund_no_fee", "test_cancel_fee_zero_and_full_percent"]),
    ("G-07", "P1", "취소 수수료 N% 부분 환불", ["test_cancel_partial_with_fee", "test_cancel_fee_floor_favors_customer"]),
    ("G-08", "P1", "취소 정책 OFF 거부", ["test_cancel_disabled", "test_external_cancel_api_disabled_service"]),
    ("G-09", "P2", "비DONE/타서비스 취소 거부", ["test_cancel_rejects_non_done", "test_cancel_rejects_non_done_or_other_service"]),
    ("G-10", "P2", "존재하지 않는 결제 취소", ["test_cancel_nonexistent_not_found"]),
    ("G-11", "P2", "결제 내역 조회(취소수수료)", ["test_list_payments_endpoint", "test_list_payments_includes_cancel_fee"]),
    ("G-12", "P3", "단건 타임아웃 → 스윕 확정", ["test_one_off_timeout_pending", "test_reconcile_confirms_one_off"]),

    # ── H. 결제·정산 화면 ───────────────────────────────────────────────
    ("H-01", "P2", "결제 내역 필터", ["test_payments_kind_and_service_filter", "test_payments_date_range_filter", "test_payments_filter_order"]),
    ("H-02", "P2", "결제 상세(내부정보 비노출)", ["test_payment_detail_page_and_scope", "test_error_responses_do_not_leak_internals"]),
    ("H-03", "P1", "정산 순매출=총매출−환불", ["test_settlement_splits_subscription_and_one_off", "test_revenue_cards_total_sub_oneoff_refund", "test_settlement_split_counts_and_plan_filter"]),
    ("H-04", "P2", "환불 정산 반영", ["test_settlement_reflects_canceled_refund"]),
    ("H-05", "P2", "월/서비스별 집계", ["test_summary_groups_by_service_amount_desc", "test_settlement_service_mode_lists_payments"]),
    ("H-06", "P3", "KST/UTC 변환", ["test_kst_format_converts_utc_to_kst", "test_kst_format_naive_treated_as_utc"]),
    ("H-07", "P2", "엑셀 다운로드(안전)", ["test_xlsx_response_headers_and_content", "test_xlsx_safe_guards_formula", "test_xlsx_response_korean_filename"]),

    # ── I. API 인증·보안 ────────────────────────────────────────────────
    ("I-01", "P1", "정상 서명 요청", ["test_valid_signed_request_returns_plans", "test_sign_request_known_answer_vector"]),
    ("I-02", "P1", "헤더 누락 401", ["test_missing_auth_headers_rejected"]),
    ("I-03", "P1", "잘못된 서명/본문 변조 401", ["test_bad_signature_rejected", "test_body_tampering_rejected"]),
    ("I-04", "P1", "다른 경로 서명 거부", ["test_signature_for_other_path_rejected", "test_sign_request_rejects_newline_injection"]),
    ("I-05", "P1", "nonce 재사용 거부", ["test_nonce_replay_rejected", "test_bad_signature_does_not_burn_nonce", "test_nonce_replay_rejected_with_baseline"]),
    ("I-06", "P1", "타임스탬프 오차 거부", ["test_stale_timestamp_rejected", "test_future_timestamp_rejected"]),
    ("I-07", "P1", "잘못된 API 키 거부", ["test_unknown_api_key_rejected"]),
    ("I-08", "P1", "허용 IP 외 차단 403", ["test_ip_not_in_whitelist_rejected"]),
    ("I-09", "P2", "일반 API 429", ["test_rate_limit_returns_429"]),
    ("I-10", "P1", "결제 API 더 엄격한 429", ["test_payment_rate_limit_stricter"]),
    ("I-11", "P2", "잘못된 JSON 422", ["test_malformed_body_422_error_format"]),
    ("I-12", "P2", "에러 내부정보 비노출", ["test_error_responses_do_not_leak_internals"]),
    ("I-13", "P2", "nonce 서비스별 격리", ["test_nonce_scope_is_per_service"]),
    ("I-14", "P3", "보안 응답 헤더", ["test_security_headers_present"]),
    ("I-15", "P3", "prod Swagger 보호", ["test_openapi_hidden_in_prod"]),

    # ── J. 웹훅 ─────────────────────────────────────────────────────────
    ("J-01", "P1", "정상 웹훅 처리", ["test_payment_status_changed_verified_by_refetch"]),
    ("J-02", "P1", "허용 외 IP 거부", ["test_webhook_from_unallowed_ip_rejected"]),
    ("J-03", "P2", "transmissionId 없음 거부", ["test_webhook_without_transmission_id_rejected"]),
    ("J-04", "P2", "중복 이벤트 1회 처리", ["test_duplicate_transmission_processed_once"]),
    ("J-05", "P1", "위조 payload 미반영", ["test_payment_status_changed_spoofed_payload_not_applied", "test_payment_status_refetch_error_triggers_retry"]),
    ("J-06", "P3", "알 수 없는 이벤트 무시", ["test_unknown_event_ignored"]),

    # ── K. 권한 격리 ────────────────────────────────────────────────────
    ("K-01", "P1", "담당자 본인 서비스만 조회", ["test_manager_sees_only_own_subscriptions", "test_cross_service_isolation", "test_dashboard_manager_scope_no_service_tables"]),
    ("K-02", "P1", "타서비스 구독/강제취소 차단", ["test_manager_cannot_open_other_service_subscription_detail", "test_manager_cannot_force_cancel_other_service_subscription"]),
    ("K-03", "P1", "타서비스 요금제 변경 차단", ["test_manager_cannot_archive_or_delete_other_service_plan", "test_manager_cannot_touch_other_service_plan", "test_manager_cannot_manage_unassigned_service_plan"]),
    ("K-04", "P1", "서비스 관리/키 접근 차단", ["test_manager_cannot_access_services_admin", "test_manager_cannot_rotate_service_keys"]),
    ("K-05", "P1", "계정/설정/감사 접근 차단", ["test_audit_page_forbidden_for_manager", "test_settings_page_forbidden_for_manager", "test_create_account_page_requires_admin"]),
    ("K-06", "P2", "두 서비스 담당 매니저", ["test_manager_with_two_services_sees_both", "test_manager_manages_secondary_service_plan"]),
    ("K-07", "P2", "CSRF 토큰 검증", ["test_csrf_wrong_token_blocks_state_change", "test_logout_without_csrf_rejected"]),
    ("K-08", "P2", "비로그인 보호 페이지 차단", ["test_anonymous_redirected_to_login"]),

    # ── L. 감사·설정·킬스위치 ───────────────────────────────────────────
    ("L-01", "P2", "감사 로그 기록", ["test_record_audit_persists", "test_record_audit_actor_service_id", "test_audit_resolves_target_and_detail"]),
    ("L-02", "P2", "감사 검색/엑셀", ["test_audit_action_filter", "test_audit_q_searches_actor_target_detail", "test_audit_export_xlsx"]),
    ("L-03", "P3", "이전 로그 삭제 규칙", ["test_audit_purge_deletes_only_before_date", "test_audit_purge_rejects_future_date", "test_audit_purge_empty_date_shows_error"]),
    ("L-04", "P2", "재시도 정책 변경", ["test_update_retry_settings", "test_retry_limit_from_global_settings"]),
    ("L-05", "P2", "어드민 IP(본인 포함 강제)", ["test_update_admin_ips_requires_current_ip", "test_settings_admin_ips_form"]),
    ("L-06", "P1", "킬스위치 ON(API 503·어드민 정상)", ["test_external_api_returns_503_when_server_disabled", "test_admin_page_unaffected_when_server_disabled", "test_ensure_server_enabled_raises_when_disabled"]),
    ("L-07", "P2", "킬스위치 사유/비밀번호 필수", ["test_set_server_disabled_reason_required", "test_set_server_disabled_password"]),
    ("L-08", "P2", "킬스위치 OFF 복구", ["test_set_server_disabled_invalidates_cache", "test_ensure_server_enabled_passes_when_active"]),
]
