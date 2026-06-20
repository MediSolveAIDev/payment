# 샘플 다중 서비스 테스트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 결제 서버에 무인증 서비스 목록 엔드포인트를 추가하고, 샘플 서비스가 서버 목록에서 서비스를 선택→키 저장→전환하며 전체 서비스를 테스트(인증 실패 시 키 재입력)하도록 한다.

**Architecture:** 서버는 `GET /api/v1/services`(id+name+status만) 추가. 샘플은 `ServiceCredential`(서비스별 키 저장) + 활성 서비스(session) 기반으로 payment_client가 해당 키로 서명. 401이면 키 재입력 유도.

**Tech Stack:** FastAPI(서버), Django(샘플), pytest / Django test.

**스펙:** `docs/superpowers/specs/2026-06-10-sample-multi-service-design.md`. 두 저장소: payment_system(main) + sample_service(별도 repo).

## 파일 구조
- 서버: `app/api/v1/services.py`(신규), `app/api/v1/__init__.py`(등록), 테스트.
- 샘플: `shop/models.py`(+ServiceCredential, 마이그레이션 0003), `shop/payment_client.py`(creds+list_services), `shop/views.py`(서비스 선택/저장/가드/401), `shop/urls.py`, `shop/templates/shop/services.html`(신규), `shop/templates/shop/base.html`(내비), `shop/tests.py`.

---

### Task 1: (서버) GET /api/v1/services 엔드포인트

**Files:**
- Create: `app/api/v1/services.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/e2e/test_api_endpoints.py`(또는 신규 `tests/e2e/test_services_list.py`)

- [ ] **Step 1: 실패 테스트** — 인증 없이 목록 조회 + 민감정보 미포함:
```python
async def test_services_list_no_auth_no_secrets(client, db, cipher):
    from tests.factories import create_service
    svc, _, _ = await create_service(db, cipher, name="서비스목록테스트")
    resp = await client.get("/api/v1/services")   # 인증 헤더 없음
    assert resp.status_code == 200
    body = resp.json()
    names = [s["name"] for s in body["services"]]
    assert "서비스목록테스트" in names
    one = next(s for s in body["services"] if s["name"] == "서비스목록테스트")
    assert set(one.keys()) == {"id", "name", "status"}   # 키/시크릿/해시 미포함
    text = resp.text.lower()
    assert "secret" not in text and "api_key" not in text and "hash" not in text
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_api_endpoints.py -k services_list -x -q` → FAIL(404, 라우트 없음).

- [ ] **Step 3: 엔드포인트** — `app/api/v1/services.py`:
```python
"""서비스 목록 조회 — 테스트/도구가 서비스를 식별·선택할 수 있도록 id·이름만 제공.

인증 없음(키 입력 전 단계에서 호출). 키/시크릿·구독 등 민감정보는 절대 포함하지 않는다.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models import Service

router = APIRouter()


@router.get("/services")
async def list_services(db: AsyncSession = Depends(get_db)):
    """등록된 서비스의 id·이름·상태 목록(이름 정렬). 민감정보 미포함."""
    rows = await db.scalars(select(Service).order_by(Service.name))
    return {"services": [{"id": str(s.id), "name": s.name, "status": s.status}
                         for s in rows.all()]}
```
`app/api/v1/__init__.py`에 추가:
```python
from app.api.v1 import services  # noqa
router.include_router(services.router, tags=["services"])
```
(기존 include_router들과 같은 위치. import 스타일은 파일 관례 따름.)

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/e2e/test_api_endpoints.py -k services_list -q` → PASS. 이어서 `uv run pytest -q`.

- [ ] **Step 5: 커밋(main repo)**
```bash
git add app/api/v1/services.py app/api/v1/__init__.py tests/e2e
git commit -m "feat: 무인증 서비스 목록 엔드포인트 GET /api/v1/services (id·이름·상태만)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: (샘플) ServiceCredential 모델 + 마이그레이션

**Files:**
- Modify: `sample_service/shop/models.py`
- Create: `sample_service/shop/migrations/0003_servicecredential.py`(makemigrations로 생성)

작업 디렉터리: `sample_service`(별도 repo). 변경 코드엔 한글 주석.

- [ ] **Step 1: 모델** — `shop/models.py`에:
```python
class ServiceCredential(models.Model):
    """서비스별 호출 자격증명 — 한번 입력하면 저장해 다시 묻지 않는다(요청).

    service_id는 결제 서버의 서비스 UUID(문자열). api_key/hmac_secret은 어드민
    서비스 생성 시 1회 발급된 평문을 운영자가 붙여넣어 저장한다(서버는 일괄 노출하지 않음).
    """
    service_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    api_key = models.CharField(max_length=128)
    hmac_secret = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
```

- [ ] **Step 2: 마이그레이션 생성** — Run: `.venv/bin/python manage.py makemigrations shop` → `0003_servicecredential.py` 생성.

- [ ] **Step 3: 통과 확인** — Run: `.venv/bin/python manage.py migrate` 정상, `.venv/bin/python manage.py makemigrations --check --dry-run` → No changes.

- [ ] **Step 4: 커밋(sample repo)** — Task 6에서 일괄 커밋(여기선 미커밋, 이어서 진행).

---

### Task 3: (샘플) payment_client — 활성 자격증명 + list_services

**Files:**
- Modify: `sample_service/shop/payment_client.py`

- [ ] **Step 1: _request에 creds 인자** — `_request` 시그니처/본문 변경:
```python
def _request(method: str, path: str, json_body: dict | None = None,
             creds: tuple[str, str] | None = None) -> dict:
    """creds=(api_key, hmac_secret) 지정 시 그 키로 서명. None이면 settings 폴백(단일 서비스 하위호환)."""
    api_key, hmac_secret = creds if creds else (settings.SERVICE_API_KEY, settings.SERVICE_HMAC_SECRET)
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    headers = {
        "x-service-key": api_key,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": sign_request(hmac_secret, method, path, timestamp, nonce, body),
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, settings.PAYMENT_API_BASE + path,
                            headers=headers, data=body or None, timeout=30)
    if resp.status_code >= 400:
        try:
            err = resp.json()["error"]
            raise PaymentAPIError(resp.status_code, err["code"], err["message"])
        except (ValueError, KeyError):
            raise PaymentAPIError(resp.status_code, "UNKNOWN", resp.text[:200]) from None
    return resp.json()
```

- [ ] **Step 2: list_services + 공개 함수 creds 전달** — `list_services` 추가 + 기존 공개 함수에 `creds=None` 인자를 받아 `_request(..., creds=creds)`로 전달:
```python
def list_services() -> list[dict]:
    """서버의 서비스 목록(id/name/status) — 무인증, GET /api/v1/services."""
    return _request("GET", "/api/v1/services")["services"]


def get_plans(creds=None) -> list[dict]:
    return _request("GET", "/api/v1/plans", creds=creds)["plans"]


def get_payments(external_user_id: str, creds=None) -> list[dict]:
    return _request("GET", f"/api/v1/payments/{external_user_id}", creds=creds)["payments"]
```
나머지 함수(create_subscription/create_one_off_payment/get_subscription/cancel/resume/manual_pay/change_card/cancel_one_off_payment)도 동일하게 **마지막에 `creds=None` 키워드 인자**를 추가하고 내부 `_request` 호출에 `creds=creds`를 전달. (기존 위치/시그니처의 다른 인자는 유지.)

- [ ] **Step 3: 통과 확인** — Run: `cd sample_service && .venv/bin/python manage.py test shop` → 기존 테스트 통과(폴백 동작 유지). (creds 미전달 시 settings 폴백 → 기존 테스트 영향 없음.)

- [ ] **Step 4: 커밋** — Task 6 일괄.

---

### Task 4: (샘플) 서비스 선택/저장 + 활성 서비스 가드 + 내비

**Files:**
- Modify: `sample_service/shop/views.py`, `shop/urls.py`, `shop/templates/shop/base.html`
- Create: `sample_service/shop/templates/shop/services.html`

- [ ] **Step 1: 활성 자격증명 헬퍼 + 가드** — `views.py` 상단(기존 `_current_user` 옆):
```python
from shop.models import SampleUser, ServiceCredential


def _active_cred(request) -> ServiceCredential | None:
    """세션의 활성 service_id로 저장된 자격증명을 조회(없으면 None)."""
    sid = request.session.get("service_id")
    return ServiceCredential.objects.filter(service_id=sid).first() if sid else None


def _creds(request):
    """payment_client 호출용 (api_key, hmac_secret) 튜플 — 활성 서비스 없으면 None(설정 폴백)."""
    c = _active_cred(request)
    return (c.api_key, c.hmac_secret) if c else None
```

- [ ] **Step 2: 서비스 선택 화면 + 저장/선택 뷰** — `views.py`에 추가:
```python
def services_view(request):
    """서비스 선택 화면 — 서버 목록 + 저장된 키 매칭. reauth=<id>면 키 재입력 강조."""
    user = _current_user(request)
    if user is None:
        return redirect("/")
    servers, error = [], None
    try:
        servers = payment_client.list_services()
    except Exception as exc:  # noqa: BLE001
        error = f"서비스 목록을 가져올 수 없습니다: {exc}"
    saved = {c.service_id: c for c in ServiceCredential.objects.all()}
    for s in servers:                      # 저장된 키 보유 여부 표시
        s["has_key"] = s["id"] in saved
    return render(request, "shop/services.html", {
        "user": user, "servers": servers, "error": error,
        "active_id": request.session.get("service_id", ""),
        "reauth_id": request.GET.get("reauth", "")})


def service_select_view(request):
    """저장된 키가 있는 서비스를 활성화(다시 묻지 않음)."""
    user = _current_user(request)
    if user is None:
        return redirect("/")
    if request.method == "POST":
        sid = request.POST.get("service_id", "")
        if ServiceCredential.objects.filter(service_id=sid).exists():
            request.session["service_id"] = sid
            messages.success(request, "서비스가 선택되었습니다")
            return redirect("/plans")
        messages.error(request, "저장된 키가 없습니다. 키를 입력하세요")
    return redirect("/services")


def service_save_key_view(request):
    """서비스 키 입력/갱신 후 활성화 — 한번 저장하면 이후 선택만으로 사용."""
    user = _current_user(request)
    if user is None:
        return redirect("/")
    if request.method == "POST":
        sid = request.POST.get("service_id", "").strip()
        name = request.POST.get("name", "").strip()
        api_key = request.POST.get("api_key", "").strip()
        hmac_secret = request.POST.get("hmac_secret", "").strip()
        if not (sid and api_key and hmac_secret):
            messages.error(request, "service_id·api_key·hmac_secret를 모두 입력하세요")
            return redirect("/services")
        ServiceCredential.objects.update_or_create(
            service_id=sid,
            defaults={"name": name or sid, "api_key": api_key, "hmac_secret": hmac_secret})
        request.session["service_id"] = sid
        messages.success(request, "키가 저장되었습니다")
        return redirect("/plans")
    return redirect("/services")
```

- [ ] **Step 3: URL** — `shop/urls.py`에:
```python
    path("services", views.services_view),
    path("services/select", views.service_select_view),
    path("services/save-key", views.service_save_key_view),
```

- [ ] **Step 4: 활성 서비스 가드 + creds 전달** — 요금제/구독/결제 뷰에서 활성 서비스가 없으면 `/services`로 유도하고, payment_client 호출에 `creds=_creds(request)` 전달. 구체:
  - `plans_view`: 시작에 `if _active_cred(request) is None: return redirect("/services")` 추가. `payment_client.get_plans(creds=_creds(request))`.
  - `my_view`: 동일 가드 + `get_subscription(user.email, creds=_creds(request))`.
  - `_action_view`(cancel/resume/pay): `fn(user.email, creds=_creds(request))`.
  - `billing_success_view`: 구독/카드/단건 호출 모두 `creds=_creds(request)` 전달.
  - `oneoff_view`/`billing_success_view`(oneoff)·`oneoff_cancel_view`·`history_view`: 동일하게 creds 전달 + 가드.
  (활성 서비스 없으면 결제/구독 진입 시 /services로.)

- [ ] **Step 5: 내비** — `base.html` 상단 user 영역에 활성 서비스명 + 서비스 변경 링크:
```html
{% if user %}<span class="muted">{{ user.email }}
  {% if active_service_name %}· <b>{{ active_service_name }}</b>{% endif %}
  · <a href="/services">서비스 변경</a> · <a href="/my">내 구독</a> · <a href="/plans">요금제</a>
  · <a href="/pay">일반 결제</a> · <a href="/history">결제 내역</a> · <a href="/logout">로그아웃</a></span>{% endif %}
```
활성 서비스명 표시를 위해 context processor 또는 각 render에 `active_service_name` 전달. 간단히 **context processor** `shop/context.py`의 `active_service(request)`를 settings TEMPLATES context_processors에 등록:
```python
def active_service(request):
    sid = request.session.get("service_id") if hasattr(request, "session") else None
    from shop.models import ServiceCredential
    c = ServiceCredential.objects.filter(service_id=sid).first() if sid else None
    return {"active_service_name": c.name if c else ""}
```
`config/settings.py` TEMPLATES.OPTIONS.context_processors에 `"shop.context.active_service"` 추가.

- [ ] **Step 6: services.html** — `shop/templates/shop/services.html`(신규): base 확장. 서버 목록 표(이름·상태·저장여부). 각 행:
  - 저장키 있음 → "선택" 폼(POST /services/select, service_id). 활성이면 "현재 활성" 표시.
  - 저장키 없음(또는 reauth_id 일치) → 키 입력 폼(POST /services/save-key: service_id(hidden), name(hidden), api_key, hmac_secret).
  - `reauth_id`와 일치하는 서비스는 상단에 경고 배너 + 키 입력 폼 강조.
  - error 메시지, API 안내("이 화면: GET /api/v1/services").

- [ ] **Step 7: 통과 확인** — Run: `cd sample_service && .venv/bin/python manage.py test shop` → 통과(가드로 기존 테스트가 영향받으면 해당 테스트가 ServiceCredential+session을 세팅하도록 Task 6에서 조정).

- [ ] **Step 8: 커밋** — Task 6 일괄.

---

### Task 5: (샘플) 인증 실패(401) → 키 재입력 유도

**Files:**
- Modify: `sample_service/shop/views.py`

- [ ] **Step 1: 401 공통 처리** — 각 뷰의 `except PaymentAPIError as exc` 블록에서 `exc.status == 401`이면 키 변경 유도. 공통 헬퍼:
```python
def _handle_api_error(request, exc):
    """401(인증 실패)이면 활성 서비스 키 재입력 화면으로, 그 외엔 메시지."""
    if isinstance(exc, PaymentAPIError) and exc.status == 401:
        c = _active_cred(request)
        messages.error(request, "이 서비스의 key가 변경되었습니다. 다시 입력하세요.")
        return redirect(f"/services?reauth={c.service_id}" if c else "/services")
    messages.error(request, f"{exc.message} ({exc.code})" if isinstance(exc, PaymentAPIError)
                   else f"구독서버에 연결할 수 없습니다: {exc}")
    return None
```
- [ ] **Step 2: 적용** — 구독 액션/결제/조회 뷰의 except에서 `redirect_resp = _handle_api_error(request, exc); if redirect_resp: return redirect_resp`로 401 시 재입력 유도. (결과 화면 렌더 케이스는 메시지 + /services?reauth 링크 안내로 일관.)

- [ ] **Step 3: 통과 확인 + 커밋** — Task 6 일괄.

---

### Task 6: (샘플) 테스트 + 일괄 커밋

**Files:**
- Modify: `sample_service/shop/tests.py`
- (기존 테스트 중 활성 서비스 가드의 영향을 받는 것 보정)

- [ ] **Step 1: 테스트 추가**
```python
class ServicesSelectTest(TestCase):
    def setUp(self):
        self.user = SampleUser.objects.create(email="s@example.com")
        self.client.session["user_id"] = self.user.id  # 로그인 헬퍼 패턴 따름

    @mock.patch("shop.payment_client.list_services")
    def test_services_lists_and_marks_saved(self, lst):
        lst.return_value = [{"id": "svc-1", "name": "A", "status": "ACTIVE"}]
        # 로그인 세션 세팅 후 GET /services → 목록 렌더, 저장 안된 서비스는 키 입력 폼
        ...
    def test_save_key_persists_and_activates(self):
        # POST /services/save-key → ServiceCredential 생성 + session service_id 설정 + /plans
        ...
    def test_select_requires_saved_key(self):
        # 저장 키 없이 select → /services + 에러
        ...
```
(로그인 세션 세팅은 기존 테스트의 `_login` 패턴 재사용. 활성 서비스 creds로 _request가 호출되는지 1건 검증.)
- [ ] **Step 2: 기존 테스트 보정** — 활성 서비스 가드(`/services` 리다이렉트)로 plans/my/pay 관련 기존 테스트가 깨지면, 각 테스트 setUp에서 ServiceCredential 생성 + `session["service_id"]` 설정해 활성 서비스를 구성.
- [ ] **Step 3: 전체 통과** — Run: `cd sample_service && .venv/bin/python manage.py test shop` → 전부 PASS. `makemigrations --check --dry-run` → No changes.
- [ ] **Step 4: 커밋(sample repo)**
```bash
cd sample_service && git add shop && git commit -m "feat: 다중 서비스 선택·키 저장·전환 + 401 키 재입력 (전체 서비스 테스트)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 매뉴얼/README 갱신
- [ ] **Step 1**: `sample_service/README.md`에 다중 서비스 흐름(서비스 선택 → 키 입력/저장 → 활성 전환 → 키 변경 시 재입력) 추가. 결제 서버 매뉴얼 `docs/claude/manual/08-api-auth.md`에 `GET /api/v1/services`(무인증, id·이름·상태) 추가.
- [ ] **Step 2**: 각 repo 커밋.

## 변경하지 않는 것
- 서버 HMAC 인증·구독/결제/요금제 로직. 토스 카드등록 흐름.
