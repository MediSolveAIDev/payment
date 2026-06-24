import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin.deps import AdminContext
from app.admin.payment_error_labels import payment_error_meaning
from app.core.clock import kst_format
from app.models.payment import receipt_url_from_raw  # 매출전표 URL 공통 추출(모델과 로직 통일)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"   # app/static
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# 표시용 시각은 모두 KST로 변환(저장은 UTC). 사용: {{ dt|kst }} 또는 {{ dt|kst("%m-%d") }}
templates.env.filters["kst"] = kst_format
# 결제 실패 코드 → 한글 의미(결제 내역 툴팁). 사용: {{ payment_error_meaning(code) }}
templates.env.globals["payment_error_meaning"] = payment_error_meaning

# 결제 상태(PaymentStatus) → 한글 라벨. 사용: {{ payment_status_ko(p.status) }}
_PAYMENT_STATUS_KO = {"DONE": "완료", "FAILED": "실패", "PENDING": "대기", "CANCELED": "취소"}
templates.env.globals["payment_status_ko"] = lambda s: _PAYMENT_STATUS_KO.get(s, s)

# 구독 상태(SubscriptionStatus) → 한글 라벨. 사용: {{ sub_status_ko(sub.status) }}
_SUB_STATUS_KO = {"TRIAL": "체험", "ACTIVE": "활성", "PAST_DUE": "미수",
                  "SUSPENDED": "정지", "CANCELED": "취소",
                  "EXTENDED": "연장처리", "EXPIRED": "만료"}
templates.env.globals["sub_status_ko"] = lambda s: _SUB_STATUS_KO.get(s, s)


def receipt_url(payment) -> str | None:
    """결제의 토스 매출전표(영수증) URL을 반환한다(없으면 None) — 어드민 결제목록 템플릿 전용.

    추출 로직은 모델의 receipt_url_from_raw로 통일(서비스 응답·Payment.receipt_url과 동일).
    raw_response를 갖는 어떤 객체에도 동작하도록 getattr로 안전하게 읽는다.
    """
    return receipt_url_from_raw(getattr(payment, "raw_response", None))


# 매출전표(영수증) 링크. 사용: {{ receipt_url(p) }} (어드민 결제 목록)
templates.env.globals["receipt_url"] = receipt_url


def _asset_version() -> str:
    """정적 파일 캐시버스팅 버전 — admin.css/js의 최신 mtime 정수.

    파일을 수정하면 값이 바뀌어 `?v=` 쿼리가 갱신되고, 브라우저가 옛 CSS/JS 캐시 대신
    새 파일을 내려받는다(템플릿에서 `{{ asset_v() }}`로 호출 — 매 렌더 최신값 반영).
    """
    try:
        mtimes = [(STATIC_DIR / f).stat().st_mtime for f in ("admin.css", "admin.js")]
        return str(int(max(mtimes)))
    except OSError:
        return "0"


# 콜러블로 등록 → 렌더마다 현재 파일 mtime을 반영(서버 재시작 없이도 최신)
templates.env.globals["asset_v"] = _asset_version


def saved_redirect(url: str, message: str = "저장되었습니다",
                   status_code: int = 303) -> RedirectResponse:
    """DB 쓰기 성공 후 리다이렉트 — 대상 URL에 ?saved=<message>를 덧붙여
    다음 페이지에서 '완료' 모달(✓)을 띄운다. 기존 쿼리 파라미터는 보존한다."""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["saved"] = message
    new_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                          urlencode(q), parts.fragment))
    return RedirectResponse(new_url, status_code=status_code)


def render(request: Request, name: str, ctx: AdminContext | None = None, **extra):
    context = {"ctx": ctx, **extra}
    # 리다이렉트로 전달된 flash 메시지(?flash=...&flash_type=...) → 토스트
    context.setdefault("flash", request.query_params.get("flash"))
    context.setdefault("flash_type", request.query_params.get("flash_type"))
    # DB 쓰기 성공 후 ?saved= → 완료 모달 트리거
    context.setdefault("saved", request.query_params.get("saved"))
    resp = templates.TemplateResponse(request, name, context)
    # htmx 스왑 응답은 body[data-saved]가 교체 범위 밖이라 모달이 안 뜬다.
    # saved가 있으면 HX-Trigger로 showSaved 이벤트를 보내 admin.js가 모달을 띄우게 한다.
    # (일반 전체 페이지 로드에서는 htmx가 없어 헤더가 무시되고 body[data-saved]가 처리한다.)
    if context.get("saved"):
        resp.headers["HX-Trigger"] = json.dumps({"showSaved": context["saved"]})
    return resp


def render_list(request: Request, full_name: str, partial_name: str,
                ctx: AdminContext | None = None, **extra):
    """목록 라우트 공통 — htmx 요청(HX-Request)이면 리스트 partial만 렌더."""
    name = partial_name if request.headers.get("HX-Request") else full_name
    return render(request, name, ctx=ctx, **extra)


from app.admin.routes import (  # noqa: E402
    audit,
    auth,
    cards,
    dashboard,
    guide,
    payments,
    plans,
    services,
    services_export,
    services_managers,
    settings,
    settlement,
    subscriptions,
    users,
)

router = APIRouter(redirect_slashes=False)
router.include_router(auth.router)
# export 라우터를 services보다 먼저 등록 — /services/export.xlsx가
# /services/{service_id}(UUID 경로)에 잡혀 422가 되지 않도록 순서가 중요하다.
router.include_router(services_export.router)    # 엑셀 다운로드 4종(감사 Phase 4 — S6 분리)
router.include_router(services_managers.router)  # 담당자 관리 3종(감사 Phase 4 — S6 분리)
router.include_router(services.router)
router.include_router(plans.router)
router.include_router(subscriptions.router)
router.include_router(cards.router)             # 카드 상세·활성/비활성 토글(SYSTEM_ADMIN 전용)
router.include_router(payments.router)
router.include_router(settlement.router)
router.include_router(users.router)
router.include_router(audit.router)
router.include_router(settings.router)  # 전체 설정(SYSTEM_ADMIN 전용, 요청 013)
router.include_router(guide.router)
# dashboard route must be added directly (not via sub-router) so that
# GET /admin (no trailing slash) resolves correctly when prefix="/admin".
router.add_api_route("", dashboard.dashboard, methods=["GET"],
                     dependencies=dashboard.router.dependencies)
