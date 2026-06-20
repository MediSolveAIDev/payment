from django.urls import path

from shop import views

urlpatterns = [
    # ── 루트: 세션 상태 기반 라우터(로그인→서비스→카드 순으로 유도) ────────
    path("", views.root_view),
    # ── 로그인/로그아웃 ───────────────────────────────────────────────────
    path("login", views.login_view),       # 1단계: 이메일 선택(첫 화면)
    path("logout", views.logout_view),
    # ── 서비스 선택/저장 — 2단계(로그인 후) ───────────────────────────────
    path("services", views.services_view),
    path("services/select", views.service_select_view),
    path("services/save-key", views.service_save_key_view),
    # ── 보호 뷰(서비스 + 로그인 모두 필요) ───────────────────────────────
    path("plans", views.plans_view),
    # 카드 보관함 — 등록/변경(토스 인증)·조회·삭제. 구독/결제의 전제 조건.
    path("card", views.card_view),
    path("my", views.my_view),
    path("my/cancel", views.cancel_view),
    path("my/resume", views.resume_view),
    path("my/pay", views.pay_view),
    path("subscribe/<uuid:plan_id>", views.subscribe_view),
    path("pay", views.oneoff_view),
    path("pay/cancel", views.oneoff_cancel_view),
    path("billing/success", views.billing_success_view),
    path("billing/fail", views.billing_fail_view),
    # 결제 내역 화면 — 구독 결제(서버 API) + 단건 결제(로컬 DB) 통합 조회
    path("history", views.history_view),
    # 서비스 알림 수신(요청 016) — 결제 서버가 보내는 아웃고잉 웹훅 수신 + 목록 화면
    path("notify", views.notify_receive_view),        # POST: 결제 서버 → 이 URL로 알림
    path("notifications", views.notifications_view),  # 받은 알림 목록 화면
]
