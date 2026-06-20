# Django 샘플 서비스 (요청 006 — 실결제 데모) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 외부 서비스 역할의 Django 샘플(`~/Documents/medisolve/code/sample_service`, 포트 8001)이 실제 토스 카드등록창과 구독서버 API(HMAC 서명)로 구독 전체 라이프사이클을 처리하는 데모.

**Architecture:** Django 단일 앱(shop). `payment_client.py`가 구독서버 `sign_request`와 동일한 canonical string(`METHOD\npath\ntimestamp\nnonce\nsha256(body)`)으로 서명해 실제 HTTP 호출. 카드 등록은 토스 SDK v2 `requestBillingAuth` 실창 → `authKey`를 구독서버에 전달(빌링키 발급+실결제는 구독서버 몫). 런타임 mock 없음.

**Tech Stack:** Django ≥6.0(Python 3.14 호환), requests, python-dotenv, SQLite. 토스 SDK `https://js.tosspayments.com/v2/standard`.

**스펙:** `payment_system/docs/superpowers/specs/2026-06-07-sample-service-design.md`

참고 사실 (구독서버 — payment_system):
- 인증 헤더: `x-service-key`, `x-timestamp`(epoch초), `x-nonce`(1회용), `x-signature`
- 서명: `HMAC_SHA256(secret, "METHOD\n{path}\n{timestamp}\n{nonce}\n{sha256_hex(body)}")` — 검증 벡터(secret=`test-secret`):
  - `POST /api/v1/subscriptions`, ts=`1700000000`, nonce=`test-nonce`, body=`{"a":1}` → `414b0133d3e3fe5a0906cc7a52068ccdb0974a7b3dbea72c98d428064c585570`
  - `GET /api/v1/plans`, 동일 ts/nonce, body 빈 바이트 → `8cef984fdf9b4c2161f4e8dc05736306744a00c0aea6daa766dafe320ecd204b`
- 엔드포인트: `GET /api/v1/plans` → `{"plans": [PlanResponse...]}` (id/name/price/amount/billing_cycle/cycle_days/first_payment_type/value/trial_enabled/trial_days), `POST /api/v1/subscriptions` (body: external_user_id/plan_id/auth_key/customer_key/trial) → SubscriptionResponse(201), `GET|/cancel|/resume|/pay /api/v1/subscriptions/{external_user_id}`, `POST .../change-card` (body: auth_key/customer_key)
- SubscriptionResponse: id/external_user_id/plan_id/plan_name/status/access_allowed/current_period_start/current_period_end/next_billing_at/card(dict|null)/retry_count
- 에러 JSON: `{"error": {"code": "...", "message": "..."}}` (4xx/5xx)
- 작업 디렉토리: **새 폴더** `/Users/hanseungjin/Documents/medisolve/code/sample_service` — 자체 git 저장소(`git init`). 이 플랜의 커밋은 모두 그 저장소에서 수행.
- 테스트 실행: `cd /Users/hanseungjin/Documents/medisolve/code/sample_service && .venv/bin/python manage.py test shop`

---

### Task 1: 프로젝트 스캐폴드

**Files (모두 `/Users/hanseungjin/Documents/medisolve/code/sample_service/` 기준):**
- Create: `requirements.txt`, `.gitignore`, `.env.example`, `.env`, `config/settings.py`, `config/urls.py`, `manage.py`, `shop/` 앱 골격

- [ ] **Step 1: 폴더/venv/Django 설치**

```bash
mkdir -p /Users/hanseungjin/Documents/medisolve/code/sample_service
cd /Users/hanseungjin/Documents/medisolve/code/sample_service
git init
python3 -m venv .venv
.venv/bin/pip install "Django>=6.0" requests python-dotenv
.venv/bin/django-admin startproject config .
.venv/bin/python manage.py startapp shop
```

- [ ] **Step 2: requirements.txt / .gitignore / .env.example 작성**

`requirements.txt`:

```
Django>=6.0
requests>=2.32
python-dotenv>=1.0
```

`.gitignore`:

```
.venv/
__pycache__/
db.sqlite3
.env
```

`.env.example` (실제 `.env`는 이걸 복사해 채움 — 커밋 금지):

```
DJANGO_SECRET_KEY=change-me
PAYMENT_API_BASE=http://127.0.0.1:8000
SERVICE_API_KEY=svc_xxx        # 구독서버 admin > 서비스 상세 > 키 복사
SERVICE_HMAC_SECRET=xxx        # 〃
TOSS_CLIENT_KEY=test_ck_ex6BJGQOVD9YZDN6jvwqrW4w2zNb
```

`.env`도 같은 내용으로 생성(키는 사용자가 나중에 채움 — TOSS_CLIENT_KEY는 위 값 그대로).

- [ ] **Step 3: settings.py 수정** — 생성된 `config/settings.py`에서:

상단에 추가:

```python
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
```

변경/추가:

```python
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure")
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "shop",
]

# 데모 — admin/auth 미사용이므로 관련 미들웨어 제거
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# messages를 세션 기반으로 (auth 미들웨어 없음)
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"

# 구독서버 연동
PAYMENT_API_BASE = os.environ.get("PAYMENT_API_BASE", "http://127.0.0.1:8000")
SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")
SERVICE_HMAC_SECRET = os.environ.get("SERVICE_HMAC_SECRET", "")
TOSS_CLIENT_KEY = os.environ.get("TOSS_CLIENT_KEY", "")
```

(AUTH_PASSWORD_VALIDATORS, admin 관련 기본 항목은 삭제해도 됨. `django.contrib.auth`/`admin`을 INSTALLED_APPS에서 빼면 기본 urls.py의 admin 라인도 제거 필요 — Task 3에서 urls 전면 교체.)

- [ ] **Step 4: 구동 확인**

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
```

Expected: `System check identified no issues`

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "chore: Django 샘플 서비스 스캐폴드 (요청006)"
```

---

### Task 2: payment_client — HMAC 서명 + API 클라이언트 (TDD)

**Files:**
- Create: `shop/payment_client.py`
- Test: `shop/tests.py`

- [ ] **Step 1: 실패하는 서명 호환 테스트** — `shop/tests.py`를 다음으로 교체:

```python
"""구독서버(payment_system)와의 HMAC 서명 호환 검증.

기대값은 payment_system의 app.core.security.sign_request로 생성한 고정 벡터 —
어느 한쪽 서명 로직이 바뀌면 이 테스트가 깨진다.
"""
from django.test import SimpleTestCase

from shop.payment_client import sign_request


class SignRequestCompatTest(SimpleTestCase):
    def test_post_with_body_matches_server_vector(self):
        sig = sign_request("test-secret", "POST", "/api/v1/subscriptions",
                           "1700000000", "test-nonce", b'{"a":1}')
        self.assertEqual(
            sig,
            "414b0133d3e3fe5a0906cc7a52068ccdb0974a7b3dbea72c98d428064c585570")

    def test_get_empty_body_matches_server_vector(self):
        sig = sign_request("test-secret", "GET", "/api/v1/plans",
                           "1700000000", "test-nonce", b"")
        self.assertEqual(
            sig,
            "8cef984fdf9b4c2161f4e8dc05736306744a00c0aea6daa766dafe320ecd204b")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python manage.py test shop -v 2`
Expected: FAIL — `shop.payment_client` 모듈 없음(ImportError)

- [ ] **Step 3: 구현** — `shop/payment_client.py` 생성:

```python
"""구독서버(payment_system) API 클라이언트 — 외부 서비스와 동일한 3중 인증 경로.

서명 형식은 payment_system app/core/security.py:sign_request의 미러:
HMAC_SHA256(secret, "METHOD\n{path}\n{timestamp}\n{nonce}\n{sha256_hex(body)}")
"""
import hashlib
import hmac
import time
import uuid

import requests
from django.conf import settings


def sign_request(secret: str, method: str, path: str, timestamp: str,
                 nonce: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class PaymentAPIError(Exception):
    """구독서버 에러 응답({"error": {code, message}})."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _request(method: str, path: str, json_body: dict | None = None) -> dict:
    body = b""
    if json_body is not None:
        import json as _json
        body = _json.dumps(json_body).encode()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    headers = {
        "x-service-key": settings.SERVICE_API_KEY,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": sign_request(settings.SERVICE_HMAC_SECRET, method, path,
                                    timestamp, nonce, body),
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
            raise PaymentAPIError(resp.status_code, "UNKNOWN",
                                  resp.text[:200]) from None
    return resp.json()


def get_plans() -> list[dict]:
    return _request("GET", "/api/v1/plans")["plans"]


def create_subscription(*, plan_id: str, external_user_id: str, customer_key: str,
                        auth_key: str, trial: bool) -> dict:
    return _request("POST", "/api/v1/subscriptions", {
        "plan_id": plan_id, "external_user_id": external_user_id,
        "customer_key": customer_key, "auth_key": auth_key, "trial": trial})


def get_subscription(external_user_id: str) -> dict:
    return _request("GET", f"/api/v1/subscriptions/{external_user_id}")


def cancel(external_user_id: str) -> dict:
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/cancel")


def resume(external_user_id: str) -> dict:
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/resume")


def manual_pay(external_user_id: str) -> dict:
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/pay")


def change_card(external_user_id: str, *, customer_key: str, auth_key: str) -> dict:
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/change-card",
                    {"customer_key": customer_key, "auth_key": auth_key})
```

주의: 구독서버 서명은 **쿼리스트링 없는 path** 기준(`request.url.path`) — 위 함수들은 쿼리를 쓰지 않으므로 안전.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python manage.py test shop -v 2`
Expected: 2 PASS

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat: 구독서버 HMAC 클라이언트 + 서명 호환 테스트"
```

---

### Task 3: 로그인 + SampleUser + 요금제 페이지

**Files:**
- Modify: `shop/models.py`, `shop/views.py`, `config/urls.py`
- Create: `shop/urls.py`, `shop/templates/shop/base.html`, `login.html`, `plans.html`
- Test: `shop/tests.py`

- [ ] **Step 1: 실패하는 테스트** — `shop/tests.py`에 추가:

```python
from django.test import TestCase

from shop.models import SampleUser


class AuthFlowTest(TestCase):
    def test_login_required_redirect(self):
        resp = self.client.get("/plans")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_login_creates_user_with_customer_key(self):
        resp = self.client.post("/", {"email": "user@x.com"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/plans")
        u = SampleUser.objects.get(email="user@x.com")
        self.assertEqual(len(u.customer_key), 32)  # uuid4 hex
        # 재로그인 시 동일 customer_key 유지
        self.client.post("/", {"email": "user@x.com"})
        self.assertEqual(SampleUser.objects.count(), 1)
```

Run: `.venv/bin/python manage.py test shop -v 2` → FAIL (모델/뷰 없음)

- [ ] **Step 2: 모델** — `shop/models.py`:

```python
import uuid

from django.db import models


def _new_customer_key() -> str:
    return uuid.uuid4().hex


class SampleUser(models.Model):
    """데모 사용자 — 이메일이 구독서버 external_user_id, customer_key는 토스 빌링용."""

    email = models.EmailField(unique=True)
    customer_key = models.CharField(max_length=64, default=_new_customer_key)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email
```

```bash
.venv/bin/python manage.py makemigrations shop && .venv/bin/python manage.py migrate
```

- [ ] **Step 3: 뷰/URL** — `shop/views.py`:

```python
from django.contrib import messages
from django.shortcuts import redirect, render

from shop import payment_client
from shop.models import SampleUser
from shop.payment_client import PaymentAPIError


def _current_user(request) -> SampleUser | None:
    uid = request.session.get("user_id")
    return SampleUser.objects.filter(id=uid).first() if uid else None


def login_view(request):
    """이메일만 입력하는 데모 로그인 — 세션에 사용자 저장."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if email:
            user, _ = SampleUser.objects.get_or_create(email=email)
            request.session["user_id"] = user.id
            return redirect("/plans")
        messages.error(request, "이메일을 입력하세요")
    return render(request, "shop/login.html")


def logout_view(request):
    request.session.flush()
    return redirect("/")


def plans_view(request):
    user = _current_user(request)
    if user is None:
        return redirect("/")
    plans, error = [], None
    try:
        plans = payment_client.get_plans()
    except PaymentAPIError as exc:
        error = f"요금제 조회 실패: {exc.message} ({exc.code})"
    except Exception as exc:  # noqa: BLE001 — 구독서버 미기동 등 연결 오류 표시
        error = f"구독서버에 연결할 수 없습니다: {exc}"
    return render(request, "shop/plans.html",
                  {"user": user, "plans": plans, "error": error})
```

`shop/urls.py`:

```python
from django.urls import path

from shop import views

urlpatterns = [
    path("", views.login_view),
    path("logout", views.logout_view),
    path("plans", views.plans_view),
]
```

`config/urls.py` 전체 교체:

```python
from django.urls import include, path

urlpatterns = [path("", include("shop.urls"))]
```

- [ ] **Step 4: 템플릿** — `shop/templates/shop/base.html`:

```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Sample Shop{% endblock %}</title>
  <style>
    :root { --ink:#191F28; --muted:#8B95A1; --line:#E5E8EB; --blue:#3182F6; --red:#E5396E; --bg:#F9FAFB; }
    * { box-sizing:border-box; margin:0; }
    body { font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif; background:var(--bg); color:var(--ink); }
    .wrap { max-width:720px; margin:0 auto; padding:32px 20px; }
    .top { display:flex; align-items:center; justify-content:space-between; margin-bottom:24px; }
    .top b { font-size:20px; }
    .card { background:#fff; border:1px solid var(--line); border-radius:12px; padding:20px; margin-bottom:14px; }
    .muted { color:var(--muted); font-size:13px; }
    .btn { display:inline-block; border:0; border-radius:8px; padding:10px 16px; font-size:14px; font-weight:600; cursor:pointer; text-decoration:none; }
    .btn-primary { background:var(--blue); color:#fff; }
    .btn-ghost { background:#fff; color:var(--ink); border:1px solid var(--line); }
    .btn-danger { background:#fff; color:var(--red); border:1px solid var(--line); }
    input[type=email] { width:100%; padding:12px; border:1px solid var(--line); border-radius:8px; font-size:15px; }
    .msg { padding:12px 14px; border-radius:8px; margin-bottom:14px; font-size:14px; }
    .msg-error { background:#FDE7EF; color:var(--red); }
    .msg-success { background:#E8F3FF; color:var(--blue); }
    .badge { font-size:12px; padding:2px 8px; border-radius:999px; background:var(--bg); border:1px solid var(--line); }
    .price { font-size:18px; font-weight:700; }
    .row { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  </style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <b>🛍️ Sample Shop</b>
    {% if user %}<span class="muted">{{ user.email }} ·
      <a href="/my">내 구독</a> · <a href="/plans">요금제</a> · <a href="/logout">로그아웃</a></span>{% endif %}
  </div>
  {% if messages %}{% for m in messages %}
    <div class="msg {% if m.tags == 'error' %}msg-error{% else %}msg-success{% endif %}">{{ m }}</div>
  {% endfor %}{% endif %}
  {% block content %}{% endblock %}
</div>
</body>
</html>
```

`shop/templates/shop/login.html`:

```html
{% extends "shop/base.html" %}
{% block title %}로그인{% endblock %}
{% block content %}
<div class="card">
  <h2 style="margin-bottom:6px">데모 로그인</h2>
  <p class="muted" style="margin-bottom:16px">이메일만 입력하면 됩니다 — 이 이메일이 구독서버의 사용자 ID(external_user_id)가 됩니다.</p>
  <form method="post">
    {% csrf_token %}
    <input type="email" name="email" placeholder="you@example.com" required>
    <div style="margin-top:12px"><button class="btn btn-primary" type="submit">시작하기</button></div>
  </form>
</div>
{% endblock %}
```

`shop/templates/shop/plans.html`:

```html
{% extends "shop/base.html" %}
{% block title %}요금제{% endblock %}
{% block content %}
<h2 style="margin-bottom:14px">요금제 선택</h2>
{% if error %}<div class="msg msg-error">{{ error }}</div>{% endif %}
{% for p in plans %}
<div class="card row">
  <div>
    <div style="font-weight:700">{{ p.name }} <span class="badge">{{ p.billing_cycle }}{% if p.cycle_days %} {{ p.cycle_days }}일{% endif %}</span></div>
    <div class="muted" style="margin-top:4px">
      정기 결제 <span class="price">{{ p.amount }}원</span>
      {% if p.first_payment_type != 'NONE' %} · 첫 결제 혜택({{ p.first_payment_type }}){% endif %}
      {% if p.trial_enabled %} · 체험 {{ p.trial_days }}일{% endif %}
    </div>
  </div>
  <div style="display:flex;gap:8px;flex:none">
    <a class="btn btn-primary" href="/subscribe/{{ p.id }}">구독하기</a>
    {% if p.trial_enabled %}<a class="btn btn-ghost" href="/subscribe/{{ p.id }}?trial=1">체험 시작</a>{% endif %}
  </div>
</div>
{% empty %}
<div class="card muted">요금제가 없습니다 — 구독서버 admin에서 요금제를 생성하세요.</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 5: 통과 확인 + 커밋**

Run: `.venv/bin/python manage.py test shop -v 2` → 전부 PASS

```bash
git add -A && git commit -m "feat: 데모 로그인 + 요금제 페이지"
```

---

### Task 4: 구독 신청 — 토스 카드등록창 + 구독 생성

**Files:**
- Modify: `shop/views.py`, `shop/urls.py`
- Create: `shop/templates/shop/subscribe.html`, `shop/templates/shop/result.html`, `shop/templates/shop/fail.html`
- Test: `shop/tests.py`

- [ ] **Step 1: 실패하는 테스트** — `shop/tests.py`에 추가:

```python
class SubscribeFlowTest(TestCase):
    def _login(self):
        self.client.post("/", {"email": "sub@x.com"})
        return SampleUser.objects.get(email="sub@x.com")

    def test_subscribe_page_renders_toss_sdk(self):
        user = self._login()
        resp = self.client.get("/subscribe/11111111-1111-1111-1111-111111111111")
        self.assertContains(resp, "js.tosspayments.com/v2/standard")
        self.assertContains(resp, "requestBillingAuth")
        self.assertContains(resp, user.customer_key)

    def test_billing_success_requires_login(self):
        resp = self.client.get("/billing/success?authKey=a&customerKey=b")
        self.assertEqual(resp.status_code, 302)
```

Run: `.venv/bin/python manage.py test shop -v 2` → FAIL (URL 없음)

- [ ] **Step 2: 뷰 추가** — `shop/views.py`에:

```python
from django.conf import settings as dj_settings


def subscribe_view(request, plan_id):
    """토스 카드등록창(requestBillingAuth) 페이지 — 실제 토스 SDK 사용."""
    user = _current_user(request)
    if user is None:
        return redirect("/")
    trial = request.GET.get("trial") == "1"
    return render(request, "shop/subscribe.html", {
        "user": user, "plan_id": plan_id, "trial": trial,
        "toss_client_key": dj_settings.TOSS_CLIENT_KEY,
        "mode": request.GET.get("mode", "subscribe"),  # subscribe | change-card
    })


def billing_success_view(request):
    """토스 successUrl — authKey로 구독 생성(또는 카드 변경). 실제 결제 발생 지점."""
    user = _current_user(request)
    if user is None:
        return redirect("/")
    auth_key = request.GET.get("authKey", "")
    customer_key = request.GET.get("customerKey", "")
    mode = request.GET.get("mode", "subscribe")
    if not auth_key or customer_key != user.customer_key:
        messages.error(request, "토스 인증 정보가 올바르지 않습니다")
        return redirect("/plans")
    try:
        if mode == "change-card":
            payment_client.change_card(user.email, customer_key=customer_key,
                                       auth_key=auth_key)
            messages.success(request, "카드가 변경되었습니다")
            return redirect("/my")
        sub = payment_client.create_subscription(
            plan_id=request.GET.get("plan_id", ""), external_user_id=user.email,
            customer_key=customer_key, auth_key=auth_key,
            trial=request.GET.get("trial") == "1")
    except PaymentAPIError as exc:
        return render(request, "shop/result.html",
                      {"user": user, "ok": False,
                       "message": f"{exc.message} ({exc.code})"})
    return render(request, "shop/result.html",
                  {"user": user, "ok": True, "sub": sub,
                   "message": "구독이 시작되었습니다"})


def billing_fail_view(request):
    user = _current_user(request)
    return render(request, "shop/fail.html", {
        "user": user, "code": request.GET.get("code", ""),
        "message": request.GET.get("message", "카드 등록이 취소/실패했습니다")})
```

`shop/urls.py`의 urlpatterns에 추가:

```python
    path("subscribe/<uuid:plan_id>", views.subscribe_view),
    path("billing/success", views.billing_success_view),
    path("billing/fail", views.billing_fail_view),
```

- [ ] **Step 3: 템플릿** — `shop/templates/shop/subscribe.html`:

```html
{% extends "shop/base.html" %}
{% block title %}카드 등록{% endblock %}
{% block content %}
<div class="card">
  <h2 style="margin-bottom:6px">{% if mode == 'change-card' %}카드 변경{% else %}구독 결제 카드 등록{% endif %}</h2>
  <p class="muted" style="margin-bottom:16px">토스페이먼츠 카드 등록창이 열립니다. 등록 완료 시
    {% if mode == 'change-card' %}카드가 변경됩니다{% elif trial %}체험이 시작되고 만료 시 자동 결제됩니다{% else %}첫 결제가 즉시 진행됩니다{% endif %}.</p>
  <button class="btn btn-primary" id="open-toss">카드 등록창 열기</button>
  <a class="btn btn-ghost" href="{% if mode == 'change-card' %}/my{% else %}/plans{% endif %}">취소</a>
</div>
<script src="https://js.tosspayments.com/v2/standard"></script>
<script>
  // 실제 토스 SDK v2 — 빌링(자동결제) 카드 등록창
  const tossPayments = TossPayments("{{ toss_client_key }}");
  const payment = tossPayments.payment({ customerKey: "{{ user.customer_key }}" });
  const params = new URLSearchParams({
    mode: "{{ mode }}", plan_id: "{{ plan_id }}", trial: "{{ trial|yesno:'1,0' }}"
  });
  document.getElementById("open-toss").addEventListener("click", function () {
    payment.requestBillingAuth({
      method: "CARD",
      successUrl: window.location.origin + "/billing/success?" + params.toString(),
      failUrl: window.location.origin + "/billing/fail",
      customerEmail: "{{ user.email }}",
      customerName: "{{ user.email }}",
    });
  });
</script>
{% endblock %}
```

`shop/templates/shop/result.html`:

```html
{% extends "shop/base.html" %}
{% block title %}구독 결과{% endblock %}
{% block content %}
<div class="card">
  {% if ok %}
  <h2 style="color:#1A7F4B">✅ {{ message }}</h2>
  <p class="muted" style="margin-top:8px">요금제: {{ sub.plan_name }} · 상태: {{ sub.status }}<br>
    이용 기간: {{ sub.current_period_start }} ~ {{ sub.current_period_end }}</p>
  {% else %}
  <h2 style="color:#E5396E">❌ 구독 실패</h2>
  <p class="muted" style="margin-top:8px">{{ message }}</p>
  {% endif %}
  <div style="margin-top:16px"><a class="btn btn-primary" href="/my">내 구독 보기</a></div>
</div>
{% endblock %}
```

`shop/templates/shop/fail.html`:

```html
{% extends "shop/base.html" %}
{% block title %}카드 등록 실패{% endblock %}
{% block content %}
<div class="card">
  <h2 style="color:#E5396E">카드 등록 실패</h2>
  <p class="muted" style="margin-top:8px">{{ message }}{% if code %} ({{ code }}){% endif %}</p>
  <div style="margin-top:16px"><a class="btn btn-primary" href="/plans">요금제로 돌아가기</a></div>
</div>
{% endblock %}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `.venv/bin/python manage.py test shop -v 2` → 전부 PASS

```bash
git add -A && git commit -m "feat: 토스 카드등록창 + 구독 생성 흐름"
```

---

### Task 5: 내 구독 페이지 + 라이프사이클 액션

**Files:**
- Modify: `shop/views.py`, `shop/urls.py`
- Create: `shop/templates/shop/my.html`
- Test: `shop/tests.py`

- [ ] **Step 1: 실패하는 테스트** — `shop/tests.py`에 추가:

```python
from unittest.mock import patch


class MySubscriptionViewTest(TestCase):
    """뷰 라우팅/렌더 스모크 — payment_client 함수 경계까지만 검증.

    (실제 API/결제 검증은 README의 수동 시나리오 — 런타임 경로에 mock 없음)
    """

    def _login(self):
        self.client.post("/", {"email": "my@x.com"})

    def test_my_requires_login(self):
        resp = self.client.get("/my")
        self.assertEqual(resp.status_code, 302)

    @patch("shop.views.payment_client.get_subscription")
    def test_my_renders_subscription(self, get_sub):
        get_sub.return_value = {
            "plan_name": "베이직", "status": "ACTIVE", "access_allowed": True,
            "current_period_start": "2026-06-01T00:00:00Z",
            "current_period_end": "2026-07-01T00:00:00Z",
            "next_billing_at": "2026-07-01T00:00:00Z",
            "card": {"number": "1234-****-****-5678"}, "retry_count": 0}
        self._login()
        resp = self.client.get("/my")
        self.assertContains(resp, "베이직")
        self.assertContains(resp, "구독 취소")

    @patch("shop.views.payment_client.get_subscription")
    def test_my_no_subscription(self, get_sub):
        from shop.payment_client import PaymentAPIError
        get_sub.side_effect = PaymentAPIError(404, "NOT_FOUND", "구독이 없습니다")
        self._login()
        resp = self.client.get("/my")
        self.assertContains(resp, "구독이 없습니다")
```

Run: `.venv/bin/python manage.py test shop -v 2` → FAIL (/my 없음)

- [ ] **Step 2: 뷰** — `shop/views.py`에 추가:

```python
def my_view(request):
    user = _current_user(request)
    if user is None:
        return redirect("/")
    sub, error = None, None
    try:
        sub = payment_client.get_subscription(user.email)
    except PaymentAPIError as exc:
        if exc.status != 404:
            error = f"{exc.message} ({exc.code})"
    except Exception as exc:  # noqa: BLE001
        error = f"구독서버에 연결할 수 없습니다: {exc}"
    return render(request, "shop/my.html", {"user": user, "sub": sub, "error": error})


def _action_view(request, fn, success_msg):
    """취소/재개/수동결제 공통 — POST 후 /my 리다이렉트."""
    user = _current_user(request)
    if user is None:
        return redirect("/")
    if request.method == "POST":
        try:
            fn(user.email)
            messages.success(request, success_msg)
        except PaymentAPIError as exc:
            messages.error(request, f"{exc.message} ({exc.code})")
    return redirect("/my")


def cancel_view(request):
    return _action_view(request, payment_client.cancel,
                        "구독이 취소되었습니다 — 만료일까지 이용 가능합니다")


def resume_view(request):
    return _action_view(request, payment_client.resume, "구독이 재개되었습니다")


def pay_view(request):
    return _action_view(request, payment_client.manual_pay, "결제가 완료되었습니다")
```

`shop/urls.py`에 추가:

```python
    path("my", views.my_view),
    path("my/cancel", views.cancel_view),
    path("my/resume", views.resume_view),
    path("my/pay", views.pay_view),
```

- [ ] **Step 3: 템플릿** — `shop/templates/shop/my.html`:

```html
{% extends "shop/base.html" %}
{% block title %}내 구독{% endblock %}
{% block content %}
<h2 style="margin-bottom:14px">내 구독</h2>
{% if error %}<div class="msg msg-error">{{ error }}</div>{% endif %}
{% if sub %}
<div class="card">
  <div class="row">
    <div>
      <div style="font-weight:700">{{ sub.plan_name }}
        <span class="badge">{{ sub.status }}</span>
        <span class="badge">접근 {{ sub.access_allowed|yesno:"O,X" }}</span></div>
      <div class="muted" style="margin-top:6px">
        기간: {{ sub.current_period_start }} ~ {{ sub.current_period_end }}<br>
        다음 결제: {{ sub.next_billing_at|default:"-" }}<br>
        카드: {{ sub.card.number|default:"-" }}{% if sub.retry_count %} · 재시도 {{ sub.retry_count }}회{% endif %}
      </div>
    </div>
  </div>
  <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
    {% if sub.status == 'ACTIVE' or sub.status == 'TRIAL' or sub.status == 'PAST_DUE' %}
    <form method="post" action="/my/cancel" onsubmit="return confirm('만료일까지 이용 후 종료됩니다. 취소할까요?')">
      {% csrf_token %}<button class="btn btn-danger" type="submit">구독 취소</button></form>
    <a class="btn btn-ghost" href="/subscribe/{{ sub.plan_id }}?mode=change-card">카드 변경</a>
    {% elif sub.status == 'CANCELED' %}
    <form method="post" action="/my/resume">{% csrf_token %}
      <button class="btn btn-primary" type="submit">구독 재개</button></form>
    {% elif sub.status == 'SUSPENDED' %}
    <form method="post" action="/my/pay">{% csrf_token %}
      <button class="btn btn-primary" type="submit">수동 결제</button></form>
    <a class="btn btn-ghost" href="/subscribe/{{ sub.plan_id }}?mode=change-card">카드 변경</a>
    {% endif %}
  </div>
</div>
{% else %}
<div class="card">
  <p class="muted">구독이 없습니다.</p>
  <div style="margin-top:12px"><a class="btn btn-primary" href="/plans">요금제 보러가기</a></div>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `.venv/bin/python manage.py test shop -v 2` → 전부 PASS

```bash
git add -A && git commit -m "feat: 내 구독 페이지 + 취소/재개/수동결제/카드변경"
```

---

### Task 6: README — 셋업 + 실결제 수동 시나리오

**Files:**
- Create: `README.md`

- [ ] **Step 1: README 작성** — `/Users/hanseungjin/Documents/medisolve/code/sample_service/README.md`:

````markdown
# Sample Shop — 구독결제 데모 (실제 토스 연동)

payment_system 구독서버의 외부 서비스 역할을 하는 Django 데모.
**실제 토스페이먼츠 테스트 키로 카드 등록 → 빌링키 발급 → 결제 승인까지 실제 API**로 동작한다.

## 셋업

1. **구독서버에서 서비스 등록** (http://127.0.0.1:8000/admin)
   - 서비스 관리 > 서비스 등록 — 허용 IP에 `127.0.0.1`
   - 등록 직후 키 화면(또는 서비스 상세의 **키 복사** 버튼)에서 API 키/HMAC Secret 복사
   - 요금제 1개 이상 생성 (체험 요금제 포함 권장)
2. **이 프로젝트 설정**
   ```bash
   cp .env.example .env   # SERVICE_API_KEY / SERVICE_HMAC_SECRET 채우기
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python manage.py migrate
   ```
3. **두 서버 구동**
   ```bash
   # 터미널 1 — 구독서버 (payment_system)
   cd ../payment_system && .venv/bin/uvicorn app.main:app --port 8000
   # 터미널 2 — 샘플 서비스
   .venv/bin/python manage.py runserver 8001
   ```

## 실결제 테스트 시나리오 (수동)

1. http://127.0.0.1:8001 접속 → 이메일 입력해 로그인
2. 요금제 선택 → "구독하기" → **카드 등록창 열기** → 실제 토스 카드등록창에서
   테스트 카드 입력 (토스 테스트 모드에서는 실제 카드를 넣어도 청구되지 않음)
3. 등록 완료 → 첫 결제가 즉시 승인되고 결과 페이지 표시
4. 확인 지점:
   - 샘플: `/my`에서 상태 ACTIVE, 카드/다음 결제일
   - 구독서버 admin: 구독·결제 내역
   - [토스 개발자센터](https://developers.tosspayments.com/) 테스트 결제내역
5. 라이프사이클: `/my`에서 **구독 취소**(만료일까지 유지) → **재개**,
   **카드 변경**(카드등록창 재진입), SUSPENDED 시 **수동 결제**
6. 체험: 체험 요금제에서 "체험 시작" — 결제 없이 TRIAL 시작, 만료 시 자동 결제

## 테스트

```bash
.venv/bin/python manage.py test shop
```

서명 호환(구독서버 sign_request와 동일 벡터) + 뷰 스모크. 결제 검증은 위 수동 시나리오.
````

- [ ] **Step 2: 전체 테스트 + 커밋**

Run: `.venv/bin/python manage.py test shop -v 2` → 전부 PASS, `.venv/bin/python manage.py check` → no issues

```bash
git add -A && git commit -m "docs: 셋업 + 실결제 테스트 시나리오 README"
```

- [ ] **Step 3: payment_system 쪽 회귀 확인** (코드 무변경 검증)

```bash
cd /Users/hanseungjin/Documents/medisolve/code/payment_system && git status --short && .venv/bin/pytest -q | tail -1
```

Expected: payment_system 워킹트리 clean(스펙/플랜 문서 외), 전체 테스트 PASS
