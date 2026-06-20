"""Django 템플릿 컨텍스트 프로세서 — 전역 컨텍스트 변수 주입."""


def active_service(request):
    """활성 서비스명과 현재 이메일을 모든 템플릿에 주입 — base.html 내비 표시용.

    새 흐름(이메일→서비스→카드→구독)에서 내비에 현재 단계 상태를 보여주기 위해
    active_service_name(서비스명)과 nav_user_email(이메일)을 함께 반환한다.
    세션이 없거나 값이 없으면 빈 문자열 반환.
    """
    # 세션 접근 가능 여부 확인(테스트/미들웨어 미설정 환경 방어)
    if not hasattr(request, "session"):
        return {"active_service_name": "", "nav_user_email": ""}

    # 활성 서비스명 — service_id → ServiceCredential.name
    sid = request.session.get("service_id")
    active_service_name = ""
    if sid:
        # 순환 임포트 방지를 위해 함수 내부에서 임포트
        from shop.models import ServiceCredential
        cred = ServiceCredential.objects.filter(service_id=sid).first()
        active_service_name = cred.name if cred else ""

    # 현재 로그인 이메일 — user_id → SampleUser.email
    uid = request.session.get("user_id")
    nav_user_email = ""
    if uid:
        from shop.models import SampleUser
        u = SampleUser.objects.filter(id=uid).first()
        nav_user_email = u.email if u else ""

    return {
        "active_service_name": active_service_name,
        "nav_user_email": nav_user_email,
    }
