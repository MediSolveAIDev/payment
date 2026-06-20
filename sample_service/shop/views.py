"""샘플 쇼핑몰 뷰 — 구독서버 API를 호출하는 참고 구현.

흐름 순서(신규): / (라우터) → /login (이메일 선택) → /services (서비스 선택)
                → /card (카드 등록 + 보유 카드 조회) → /plans (구독) 또는 /pay (일반결제)
활성 서비스(세션의 service_id → ServiceCredential)를 기준으로 payment_client 함수에
creds=(api_key, hmac_secret) 를 전달한다.

보호 뷰는 공통 가드 _gate()를 사용: 로그인 없으면 /login, 서비스 없으면 /services 유도.
"""
import uuid

from django.conf import settings as dj_settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render

from shop import payment_client
from shop.models import NotificationRecord, OneOffRecord, SampleUser, ServiceCredential
from shop.payment_client import PaymentAPIError


# ─────────────────────────────────────────────
# 세션 헬퍼
# ─────────────────────────────────────────────

def _current_user(request) -> SampleUser | None:
    """세션에서 현재 로그인 사용자 조회."""
    uid = request.session.get("user_id")
    return SampleUser.objects.filter(id=uid).first() if uid else None


def _active_cred(request) -> ServiceCredential | None:
    """세션의 활성 service_id로 저장된 자격증명을 조회(없으면 None)."""
    sid = request.session.get("service_id")
    return ServiceCredential.objects.filter(service_id=sid).first() if sid else None


def _creds(request):
    """payment_client 호출용 (api_key, hmac_secret) 튜플.

    활성 서비스 자격증명이 있으면 해당 키를, 없으면 None(settings 폴백)을 반환.
    """
    c = _active_cred(request)
    return (c.api_key, c.hmac_secret) if c else None


# ─────────────────────────────────────────────
# 보호 뷰 공통 가드
# ─────────────────────────────────────────────

def _gate(request):
    """보호 뷰 공통 가드 — 로그인 먼저, 그다음 서비스. 통과 시 None 반환.

    새 흐름 순서:
      1. 로그인 사용자 없으면 /login (이메일 선택이 첫 단계)
      2. 활성 서비스 없으면 /services (두 번째 단계)
      3. 둘 다 있으면 None(통과)
    """
    if _current_user(request) is None:
        return redirect("/login")   # 이메일 선택이 첫 단계
    if _active_cred(request) is None:
        return redirect("/services")  # 그다음 서비스 선택
    return None


# ─────────────────────────────────────────────
# 인증 오류 공통 처리
# ─────────────────────────────────────────────

def _handle_api_error(request, exc):
    """401(인증 실패)이면 활성 서비스 키 재입력 화면으로, 그 외엔 메시지.

    반환값이 None이면 호출자가 기본 오류 처리를 이어간다.
    반환값이 redirect 응답이면 즉시 반환해야 한다.
    """
    if isinstance(exc, PaymentAPIError) and exc.status == 401:
        # 키가 변경된 서비스 — 재입력 유도
        c = _active_cred(request)
        messages.error(request, "이 서비스의 key가 변경되었습니다. 다시 입력하세요.")
        return redirect(f"/services?reauth={c.service_id}" if c else "/services")
    # 그 외 에러 — 메시지만 표시하고 None 반환(호출자가 처리)
    messages.error(request, f"{exc.message} ({exc.code})" if isinstance(exc, PaymentAPIError)
                   else f"구독서버에 연결할 수 없습니다: {exc}")
    return None


# ─────────────────────────────────────────────
# 로그인/로그아웃
# ─────────────────────────────────────────────

def login_view(request):
    """이메일 선택/입력 — 새 흐름의 첫 단계(서비스 선택 전).

    새 흐름: /login (이메일 선택) → /services (서비스 선택) → /card → /plans
    서비스가 없어도 진입 가능 — 이메일이 첫 단계이기 때문이다.
    이미 로그인된 경우 서비스 없으면 /services, 있으면 /card 로 라우팅.
    """
    # 이미 로그인+서비스 세팅 완료 → /card 로 바로
    if _current_user(request) and _active_cred(request):
        return redirect("/card")
    # 이미 로그인됐지만 서비스 미선택 → /services 로
    if _current_user(request):
        return redirect("/services")

    # 로그인 화면에 표시할 기존 등록 이메일 목록
    existing_users = SampleUser.objects.order_by("-created_at")

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if email:
            try:
                validate_email(email)  # get_or_create는 모델 검증을 안 거침 — XSS/쓰레기값 차단
            except ValidationError:
                messages.error(request, "올바른 이메일 형식이 아닙니다")
                return render(request, "shop/login.html", {"existing_users": existing_users})
            user, _ = SampleUser.objects.get_or_create(email=email)
            request.session["user_id"] = user.id
            # 로그인 성공 → 다음 단계인 서비스 선택으로
            return redirect("/services")
        messages.error(request, "이메일을 입력하세요")
    return render(request, "shop/login.html", {"existing_users": existing_users})


def logout_view(request):
    """세션 전체 삭제 후 이메일 선택 화면(/login)으로 — 새 흐름의 첫 단계."""
    request.session.flush()
    return redirect("/login")


# ─────────────────────────────────────────────
# 서비스 선택/저장
# ─────────────────────────────────────────────

def services_view(request):
    """서비스 선택 화면 — 새 흐름의 2단계(로그인 후 진입).

    로그인 없이 접근하면 /login 으로 유도한다(이메일이 첫 단계).
    reauth=<service_id> 쿼리 파라미터가 있으면 해당 서비스에 키 재입력 강조 표시.
    이메일 선택 UI는 이 화면에서 제거됨 — /login 에서 처리한다.
    """
    # 로그인 필수 — 이메일 선택이 선행되어야 함
    user = _current_user(request)
    if user is None:
        return redirect("/login")
    servers, error = [], None
    try:
        # 서버에서 서비스 목록 조회(무인증 엔드포인트)
        servers = payment_client.list_services()
    except Exception as exc:  # noqa: BLE001
        error = f"서비스 목록을 가져올 수 없습니다: {exc}"
    # 저장된 키 보유 여부 — 서비스별 표시 분기
    saved = {c.service_id: c for c in ServiceCredential.objects.all()}
    for s in servers:
        s["has_key"] = s["id"] in saved
    # 이메일 목록은 /login 에서 처리 — 이 화면은 서비스 선택만 담당
    return render(request, "shop/services.html", {
        "user": user,
        "servers": servers,
        "error": error,
        "active_id": request.session.get("service_id", ""),
        "reauth_id": request.GET.get("reauth", ""),
    })


def service_select_view(request):
    """저장된 키가 있는 서비스를 세션에 활성화(다시 묻지 않음).

    새 흐름: 로그인 후 서비스 선택 → /card (카드 등록) 으로 이동.
    이메일 선택은 /login 에서 이미 처리되었으므로 이 뷰에서 email 파라미터를 받지 않는다.
    """
    if request.method == "POST":
        sid = request.POST.get("service_id", "")
        if ServiceCredential.objects.filter(service_id=sid).exists():
            # 저장된 키가 있는 서비스만 활성화 허용
            request.session["service_id"] = sid
            messages.success(request, "서비스가 선택되었습니다")
            # 새 흐름: 서비스 선택 후 카드 등록 화면으로
            return redirect("/card")
        # 저장 키 없이 선택 시도 — 에러 후 서비스 화면으로
        messages.error(request, "저장된 키가 없습니다. 키를 입력하세요")
    return redirect("/services")


def service_save_key_view(request):
    """서비스 키 입력/갱신 후 세션 활성화 — 한번 저장하면 이후 선택만으로 사용.

    새 흐름: 키 저장 성공 후 /card (카드 등록) 으로 이동.
    로그인은 /login 에서 이미 처리되므로 여기선 로그인 여부 분기가 없다.
    """
    if request.method == "POST":
        sid = request.POST.get("service_id", "").strip()
        name = request.POST.get("name", "").strip()
        api_key = request.POST.get("api_key", "").strip()
        hmac_secret = request.POST.get("hmac_secret", "").strip()
        # 필수 필드 검증
        if not (sid and api_key and hmac_secret):
            messages.error(request, "service_id·api_key·hmac_secret를 모두 입력하세요")
            return redirect("/services")
        # 기존 키가 있으면 갱신, 없으면 신규 생성
        ServiceCredential.objects.update_or_create(
            service_id=sid,
            defaults={"name": name or sid, "api_key": api_key, "hmac_secret": hmac_secret})
        # 키 저장 후 즉시 활성화
        request.session["service_id"] = sid
        messages.success(request, "키가 저장되었습니다")
        # 새 흐름: 서비스 키 저장 후 카드 등록 화면으로
        return redirect("/card")
    return redirect("/services")


# ─────────────────────────────────────────────
# 루트 라우터
# ─────────────────────────────────────────────

def root_view(request):
    """루트(/) — 세션 상태에 따라 적절한 단계로 라우팅.

    새 흐름:
      · 로그인 없음 → /login (이메일 선택, 1단계)
      · 로그인 있고 서비스 없음 → /services (서비스 선택, 2단계)
      · 로그인 + 서비스 모두 있음 → /card (카드 등록/조회, 3단계)
    """
    if _current_user(request) is None:
        return redirect("/login")
    if _active_cred(request) is None:
        return redirect("/services")
    return redirect("/card")


# ─────────────────────────────────────────────
# 요금제
# ─────────────────────────────────────────────

def plans_view(request):
    """요금제 목록 — 서비스 없으면 /, 로그인 없으면 /login 유도."""
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    plans, error = [], None
    try:
        # 활성 서비스의 자격증명으로 요금제 조회
        plans = payment_client.get_plans(creds=_creds(request))
    except PaymentAPIError as exc:
        redirect_resp = _handle_api_error(request, exc)
        if redirect_resp:
            return redirect_resp
        error = f"요금제 조회 실패: {exc.message} ({exc.code})"
    except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류 표시
        error = f"구독서버에 연결할 수 없습니다: {exc}"
    for p in plans:  # 표시용 첫 결제액 — 구독서버 정책(정가 기준) 미러
        t, v = p.get("first_payment_type"), p.get("first_payment_value") or 0
        if t == "FREE":
            p["first_amount"] = 0
        elif t == "DISCOUNT_AMOUNT":
            p["first_amount"] = max(0, p["price"] - v)
        elif t == "DISCOUNT_PERCENT":
            p["first_amount"] = p["price"] - (p["price"] * v) // 100
        else:
            p["first_amount"] = p["price"]
    return render(request, "shop/plans.html",
                  {"user": user, "plans": plans, "error": error})


# ─────────────────────────────────────────────
# 카드 보관함(Card Vault) — 카드 등록/변경/조회/삭제
#
# 서버가 카드 보관함 모델로 전환되면서, 카드 등록이 구독/결제와 분리되었다.
#   · 카드 등록/변경: 토스 빌링 인증(authKey) → POST /api/v1/cards (재등록=변경)
#   · 구독/단건결제 : 토스 인증 불필요 — 사전 등록된 카드를 서버가 사용
# 이 화면(/card)이 카드 보관함의 단일 진입점이다.
# ─────────────────────────────────────────────

def card_view(request):
    """카드 보관함 화면 — 등록 카드 조회 + 토스 SDK로 등록/변경/삭제.

    GET: 현재 등록된 카드(GET /api/v1/cards/{email})를 조회해 보여준다.
         카드 등록/변경 버튼은 토스 빌링 인증창을 열어 authKey를 받는다.
    POST(delete): 등록 카드 삭제(DELETE /api/v1/cards/{email}).
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    creds = _creds(request)
    # 카드 삭제 요청 처리(폼 action=/card, name=delete)
    if request.method == "POST" and request.POST.get("delete"):
        try:
            payment_client.delete_card(user.email, creds=creds)
            messages.success(request, "카드가 삭제되었습니다")
        except PaymentAPIError as exc:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
            messages.error(request, f"{exc.message} ({exc.code})")
        except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
            messages.error(request, f"구독서버에 연결할 수 없습니다: {exc}")
        return redirect("/card")
    # 현재 등록된 카드 조회 — 없으면(404) card=None 으로 렌더
    card, error = None, None
    try:
        card = payment_client.get_card(user.email, creds=creds)
    except PaymentAPIError as exc:
        if exc.status == 401:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
        elif exc.status != 404:  # 404=미등록은 정상 — 등록 유도 화면 표시
            error = f"{exc.message} ({exc.code})"
    except Exception as exc:  # noqa: BLE001
        error = f"구독서버에 연결할 수 없습니다: {exc}"
    return render(request, "shop/card.html", {
        "user": user, "card": card, "error": error,
        "toss_client_key": dj_settings.TOSS_CLIENT_KEY})


def billing_success_view(request):
    """토스 successUrl — authKey로 카드를 등록/교체(POST /api/v1/cards).

    카드 보관함 전환: 이 콜백은 **카드 등록 전용**이 되었다(구독 생성/단건 결제는
    더 이상 토스 authKey가 필요 없고, 사전 등록된 카드를 서버가 사용한다).
    next 파라미터로 등록 후 돌아갈 곳을 지정한다(예: 구독 흐름이면 /subscribe/...).
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인 (폴백 키로 잘못 등록되는 것을 방지)
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    auth_key = request.GET.get("authKey", "")
    customer_key = request.GET.get("customerKey", "")
    # 카드 등록 후 돌아갈 경로(기본은 카드 화면). 오픈 리다이렉트 방지를 위해 내부 경로만 허용.
    nxt = request.GET.get("next", "/card")
    if not nxt.startswith("/"):
        nxt = "/card"
    if not auth_key or customer_key != user.customer_key:
        messages.error(request, "토스 인증 정보가 올바르지 않습니다")
        return redirect("/card")
    try:
        # authKey로 카드 등록 또는 교체 — 재등록이 곧 "카드 변경"이다.
        payment_client.register_card(
            external_user_id=user.email, customer_key=customer_key,
            auth_key=auth_key, creds=_creds(request))
    except PaymentAPIError as exc:
        redirect_resp = _handle_api_error(request, exc)
        if redirect_resp:
            return redirect_resp
        messages.error(request, f"카드 등록 실패: {exc.message} ({exc.code})")
        return redirect("/card")
    except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
        messages.error(request, f"구독서버에 연결할 수 없습니다: {exc}")
        return redirect("/card")
    messages.success(request, "카드가 등록되었습니다")
    return redirect(nxt)


def subscribe_view(request, plan_id):
    """구독 생성 — 사전 등록된 카드로 즉시 구독(POST /api/v1/subscriptions).

    카드 보관함 전환: 더 이상 토스 인증을 거치지 않는다. 카드가 없으면(서버 404)
    카드 등록 화면(/card)으로 유도한다(등록 후 다시 이 구독으로 돌아옴).
    GET 으로 진입하면 확인 화면을 보여주고, POST 로 실제 구독을 생성한다.
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    trial = request.GET.get("trial") == "1"
    creds = _creds(request)
    if request.method == "POST":
        trial = request.POST.get("trial") == "1"
        try:
            # 구독 생성 — 등록 카드 빌링키로 서버가 첫 결제 처리
            sub = payment_client.create_subscription(
                plan_id=str(plan_id), external_user_id=user.email,
                trial=trial, creds=creds)
        except PaymentAPIError as exc:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
            # 카드 미등록(404) → 카드 등록 후 이 구독으로 복귀
            if exc.status == 404:
                messages.error(request, "먼저 결제 카드를 등록하세요")
                nxt = f"/subscribe/{plan_id}?trial={'1' if trial else '0'}"
                return redirect(f"/card?next={nxt}")
            return render(request, "shop/result.html",
                          {"user": user, "ok": False,
                           "message": f"{exc.message} ({exc.code})"})
        except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
            return render(request, "shop/result.html",
                          {"user": user, "ok": False,
                           "message": f"구독서버에 연결할 수 없습니다: {exc}"})
        return render(request, "shop/result.html",
                      {"user": user, "ok": True, "sub": sub,
                       "message": "구독이 시작되었습니다"})
    # GET — 등록 카드 유무를 확인해 확인 화면 또는 카드 등록 유도
    card = None
    try:
        card = payment_client.get_card(user.email, creds=creds)
    except PaymentAPIError as exc:
        if exc.status == 401:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
    except Exception:  # noqa: BLE001 — 서버 미기동 등은 확인 화면에서 안내
        pass
    return render(request, "shop/subscribe.html", {
        "user": user, "plan_id": plan_id, "trial": trial, "card": card})


def oneoff_view(request):
    """일반(단건) 결제 — 상품명/금액 입력 후 등록 카드로 즉시 결제.

    카드 보관함 전환: 토스 인증을 거치지 않고 사전 등록된 카드로 바로 청구한다.
    카드가 없으면(서버 404) 카드 등록 화면으로 유도한다.
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    creds = _creds(request)
    if request.method == "POST":
        order_name = request.POST.get("order_name", "").strip()
        amount_raw = request.POST.get("amount", "").strip()
        if not order_name:
            messages.error(request, "상품명을 입력하세요")
            return render(request, "shop/oneoff.html", {"user": user})
        try:
            amount = int(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            messages.error(request, "금액은 1원 이상의 숫자여야 합니다")
            return render(request, "shop/oneoff.html", {"user": user})
        # 멱등 order_id 생성 — 같은 order_id 재요청은 서버가 멱등 처리(이중결제 방지)
        order_id = f"oo-{uuid.uuid4().hex}"
        try:
            # 등록 카드 빌링키로 즉시 1회 청구 — 토스 인증 불필요
            payment = payment_client.create_one_off_payment(
                order_id=order_id, order_name=order_name, amount=amount,
                external_user_id=user.email, creds=creds)
        except PaymentAPIError as exc:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
            # 카드 미등록(404) → 카드 등록 후 일반 결제 화면으로 복귀
            if exc.status == 404:
                messages.error(request, "먼저 결제 카드를 등록하세요")
                return redirect("/card?next=/pay")
            return render(request, "shop/result.html",
                          {"user": user, "ok": False,
                           "message": f"{exc.message} ({exc.code})"})
        except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
            return render(request, "shop/result.html",
                          {"user": user, "ok": False,
                           "message": f"구독서버에 연결할 수 없습니다: {exc}"})
        # 단건 결제 성공 — 로컬 DB에 기록해 history 화면에서 조회·취소 테스트 가능하게 한다.
        OneOffRecord.objects.create(
            user=user, order_id=order_id, order_name=order_name, amount=amount)
        return render(request, "shop/result.html",
                      {"user": user, "ok": True, "payment": payment,
                       "message": "결제가 완료되었습니다"})
    return render(request, "shop/oneoff.html", {"user": user})


def billing_fail_view(request):
    """토스 failUrl — 카드 등록 취소/실패."""
    user = _current_user(request)
    return render(request, "shop/fail.html", {
        "user": user, "code": request.GET.get("code", ""),
        "message": request.GET.get("message", "카드 등록이 취소/실패했습니다")})


def my_view(request):
    """내 구독 화면 — 서비스 없으면 /, 로그인 없으면 /login 유도."""
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    sub, error = None, None
    try:
        # 활성 서비스 creds로 구독 조회
        sub = payment_client.get_subscription(user.email, creds=_creds(request))
    except PaymentAPIError as exc:
        if exc.status == 401:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
        elif exc.status != 404:
            error = f"{exc.message} ({exc.code})"
    except Exception as exc:  # noqa: BLE001
        error = f"구독서버에 연결할 수 없습니다: {exc}"
    return render(request, "shop/my.html", {"user": user, "sub": sub, "error": error})


def _action_view(request, fn, success_msg):
    """취소/재개/수동결제 공통 — POST 후 /my 리다이렉트.

    활성 서비스 creds를 fn에 전달. 401 에러 시 키 재입력 유도.
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    if request.method == "POST":
        try:
            # creds 키워드 인자로 활성 서비스 자격증명 전달
            fn(user.email, creds=_creds(request))
            messages.success(request, success_msg)
        except PaymentAPIError as exc:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
            messages.error(request, f"{exc.message} ({exc.code})")
        except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
            messages.error(request, f"구독서버에 연결할 수 없습니다: {exc}")
    return redirect("/my")


def cancel_view(request):
    """구독 취소 — POST /my/cancel."""
    return _action_view(request, payment_client.cancel,
                        "구독이 취소되었습니다 — 만료일까지 이용 가능합니다")


def resume_view(request):
    """구독 재개 — POST /my/resume."""
    return _action_view(request, payment_client.resume, "구독이 재개되었습니다")


def pay_view(request):
    """수동 결제 — POST /my/pay."""
    return _action_view(request, payment_client.manual_pay, "결제가 완료되었습니다")


def oneoff_cancel_view(request):
    """단건(일반) 결제 취소 — POST /pay/cancel.

    result.html 또는 history.html의 취소 폼에서 order_id를 받아 구독서버에 취소 요청한다.
    취소 불가(CANCEL_DISABLED 등) 또는 오류는 messages.error로 표시하고 /history로 돌아간다.
    성공 시 OneOffRecord.canceled=True 업데이트 후 /history로 리다이렉트.
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    if request.method == "POST":
        order_id = request.POST.get("order_id", "")
        try:
            payment_client.cancel_one_off_payment(order_id, creds=_creds(request))
            # 취소 성공 — 로컬 기록도 canceled=True 로 업데이트
            OneOffRecord.objects.filter(order_id=order_id).update(canceled=True)
            messages.success(request, "결제가 취소되었습니다")
        except PaymentAPIError as exc:
            redirect_resp = _handle_api_error(request, exc)
            if redirect_resp:
                return redirect_resp
            messages.error(request, f"{exc.message} ({exc.code})")
        except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
            messages.error(request, f"구독서버에 연결할 수 없습니다: {exc}")
    # 취소 후 결제 내역 화면으로 이동(취소 전/후 상태를 한 눈에 확인 가능)
    return redirect("/history")


def history_view(request):
    """결제 내역 — GET /history.

    ① 구독 결제 내역: get_payments(external_user_id)에서 kind=SUBSCRIPTION만 추림.
    ② 단건 결제 내역: 로컬 OneOffRecord(상품명·취소여부)에, 서버 API가 함께 반환한
       취소 수수료 정보(cancel_fee/cancel_refund_amount/cancel_fee_percent/cancelable)를
       order_id로 매칭해 부착 → 화면에 "취소 시 수수료/환불 예정액"을 노출한다.
    서버 미기동/에러는 message로 표시하고 빈 목록으로 렌더(기존 패턴 통일).

    API: GET /api/v1/payments/{external_user_id} (구독+단건 모두 반환, 취소 수수료 필드 포함)
    """
    # 공통 가드: 서비스 먼저, 그다음 로그인
    g = _gate(request)
    if g:
        return g
    user = _current_user(request)
    # 서버 API 호출(활성 서비스 creds 사용) — 구독·단건 결제를 모두 반환
    all_payments, pay_error = [], None
    try:
        all_payments = payment_client.get_payments(user.email, creds=_creds(request))
    except PaymentAPIError as exc:
        redirect_resp = _handle_api_error(request, exc)
        if redirect_resp:
            return redirect_resp
        pay_error = f"결제 내역 조회 실패: {exc.message} ({exc.code})"
    except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류
        pay_error = f"구독서버에 연결할 수 없습니다: {exc}"
    # ① 구독 결제 내역 — kind=SUBSCRIPTION만 표시
    sub_payments = [p for p in all_payments if p.get("kind") == "SUBSCRIPTION"]
    # ② 단건 결제 내역 — 로컬 기록(상품명·취소여부) + 서버의 취소 수수료 정보 결합
    #    order_id → 서버가 계산한 취소 수수료/환불액 맵(단건만)
    cancel_info = {p["order_id"]: p for p in all_payments if p.get("kind") == "ONE_OFF"}
    oneoff_records = list(OneOffRecord.objects.filter(user=user).order_by("-created_at"))
    for rec in oneoff_records:
        # 서버 응답에 매칭되는 단건 결제가 있으면 취소 수수료/환불액을 부착(없으면 0)
        info = cancel_info.get(rec.order_id, {})
        rec.cancelable = info.get("cancelable", False)
        rec.cancel_fee = info.get("cancel_fee", 0)
        rec.cancel_refund_amount = info.get("cancel_refund_amount", 0)
        rec.cancel_fee_percent = info.get("cancel_fee_percent", 0)
        # 서버 기준 실제 환불액·실수령액·상태 — 어드민이 (부분)취소하면 로컬 canceled 플래그는
        # 갱신되지 않으므로, 서버의 canceled_amount/net_amount/status로 취소 반영을 판단한다.
        rec.refunded = info.get("canceled_amount", 0)        # 누적 환불액(부분취소 포함)
        rec.net_amount = info.get("net_amount", rec.amount)  # 실수령 = 금액 − 환불
        rec.server_status = info.get("status", "")           # DONE / CANCELED
        # 표시용 취소 상태: 전액(CANCELED) → 취소됨, 일부 환불(DONE+refunded>0) → 부분취소
        if rec.server_status == "CANCELED":
            rec.cancel_state = "취소됨"
        elif rec.refunded:
            rec.cancel_state = "부분취소"
        else:
            rec.cancel_state = ""
    return render(request, "shop/history.html", {
        "user": user,
        "sub_payments": sub_payments,
        "oneoff_records": oneoff_records,
        "pay_error": pay_error,
    })


# ── 서비스 알림 수신(요청 016) — 결제 서버가 보내는 아웃고잉 웹훅 처리 ─────────────
import hashlib  # noqa: E402
import hmac as _hmac  # noqa: E402
import json as _json  # noqa: E402

from django.http import JsonResponse  # noqa: E402
from django.views.decorators.csrf import csrf_exempt  # noqa: E402
from django.views.decorators.http import require_POST  # noqa: E402


def _verify_notify_signature(request, body: bytes, service_name: str) -> bool:
    """수신 알림의 HMAC 서명을 검증한다(payment_system sign_request 미러).

    canonical = "POST\n{path}\n{X-Timestamp}\n{X-Nonce}\n{sha256_hex(body)}".
    service_name으로 ServiceCredential을 찾아 그 hmac_secret으로 비교하고,
    못 찾으면 저장된 모든 자격증명으로 시도한다(데모 편의).
    """
    ts = request.headers.get("X-Timestamp", "")
    nonce = request.headers.get("X-Nonce", "")
    sig = request.headers.get("X-Signature", "")
    if not (ts and nonce and sig):
        return False
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join(["POST", request.path, ts, nonce, body_hash]).encode()
    secrets = list(ServiceCredential.objects.filter(name=service_name)
                   .values_list("hmac_secret", flat=True))
    if not secrets:  # 이름 매칭 실패 → 저장된 모든 시크릿으로 시도(데모)
        secrets = list(ServiceCredential.objects.values_list("hmac_secret", flat=True))
    for secret in secrets:
        expected = _hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if _hmac.compare_digest(expected, sig):
            return True
    return False


@csrf_exempt
@require_POST
def notify_receive_view(request):
    """결제 서버 서비스 알림 수신 엔드포인트 — POST /notify.

    JSON 본문 + X-Signature/X-Timestamp/X-Nonce 헤더(HMAC 서명)를 검증해 저장한다.
    서명이 유효하지 않아도 데모 확인을 위해 기록은 남기되 verified=False로 표시한다.
    어드민 서비스 상세의 '서비스 알림 URL'에 이 엔드포인트(https://.../notify)를 등록하면 동작한다.
    """
    body = request.body
    try:
        payload = _json.loads(body.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid json"}, status=400)
    verified = _verify_notify_signature(request, body, payload.get("service_name", ""))
    NotificationRecord.objects.create(
        event=payload.get("EVENT", ""), status=payload.get("STATUS", ""),
        email=payload.get("email", ""), order_id=payload.get("order_id", ""),
        subscribe_id=payload.get("subscribe_id", ""), desc=payload.get("DESC", ""),
        payload=payload, verified=verified)
    return JsonResponse({"ok": True, "verified": verified})


def notifications_view(request):
    """받은 서비스 알림 목록 화면 — /notifications. 최근 100건 + 등록용 수신 URL 안내."""
    records = list(NotificationRecord.objects.all()[:100])
    return render(request, "shop/notifications.html", {
        "records": records,
        # 브라우저로 접근할 때의 주소(참고용)
        "notify_url": request.build_absolute_uri("/notify"),
        # 결제 서버가 '별도 docker'에서 호출하므로, 이 샘플(호스트 8001 공개)에 닿으려면
        # host.docker.internal:8001 을 등록해야 한다. localhost:8001(컨테이너 자기 자신)·
        # sample:8000(다른 네트워크)은 닿지 않는다.
        "notify_url_register": "http://host.docker.internal:8001/notify",
    })
