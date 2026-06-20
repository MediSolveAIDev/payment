# Django 샘플 서비스 — 실제 구독결제 데모 (요청 006) 설계

날짜: 2026-06-07
상태: 승인됨
요청: docs/requests/006.md

## 목표

외부 서비스가 구독서버를 호출하는 실제 프로세스를 그대로 재현하는 Django 샘플 서비스를
만든다. **실제 토스페이먼츠 테스트 키로 카드 등록·빌링키 발급·결제 승인까지 실제 API 연동**
(런타임 경로에 mock/fake 없음).

## 구조

```
~/Documents/medisolve/code/sample_service/   ← payment_system의 형제 폴더, 포트 8001
├── manage.py
├── requirements.txt          (django, requests, python-dotenv)
├── .env.example / .env       (커밋 금지: .gitignore)
├── README.md                 (셋업 + 실결제 테스트 시나리오)
├── config/                   (settings.py, urls.py)
└── shop/                     (단일 앱)
    ├── payment_client.py     ← 구독서버 API 클라이언트 (HMAC 서명)
    ├── models.py             ← SampleUser(email, customer_key UUID)
    ├── views.py / urls.py
    └── templates/shop/       (login, plans, subscribe result, my subscription, fail)
```

- DB: SQLite (Django 기본). `SampleUser`는 사용자별 토스 `customerKey` 영속용
  (동일 사용자 카드 재등록 시 같은 키 — 토스 빌링 요구사항).
- 환경 변수: `PAYMENT_API_BASE=http://127.0.0.1:8000`, `SERVICE_API_KEY`,
  `SERVICE_HMAC_SECRET`, `TOSS_CLIENT_KEY`, `DJANGO_SECRET_KEY`.

## 인증 — payment_client.py

- 구독서버 `app/core/security.py:sign_request`와 **동일한 canonical string**
  (`method\npath\ntimestamp\nnonce\n` + body 처리 — 구현 시 원본 함수를 읽고 정확히 미러)
  으로 HMAC-SHA256 서명 생성.
- 모든 요청에 `x-service-key`/`x-timestamp`/`x-nonce`(UUID)/`x-signature` 헤더를 붙여
  `requests`로 **실제 HTTP 호출** — IP 화이트리스트·레이트리밋·nonce 재전송 방어까지
  외부 서비스와 100% 동일 경로 검증.
- 제공 메서드: `get_plans()`, `create_subscription(plan_id, external_user_id,
  customer_key, auth_key, trial)`, `get_subscription(external_user_id)`,
  `cancel(external_user_id)`, `resume(external_user_id)`, `manual_pay(external_user_id)`,
  `change_card(external_user_id, customer_key, auth_key)`.
- 구독서버 에러 응답(JSON `{code, message}` 형태 — 실제 스키마는 구현 시 확인)을
  예외로 변환해 뷰가 사용자에게 표시.

## 사용자 흐름

1. **로그인** (`/`): 이메일 입력 → 세션 저장. 이메일 = `external_user_id`.
   `SampleUser` get_or_create로 `customer_key`(UUID hex) 확보.
2. **요금제** (`/plans`): `get_plans()` 결과를 카드로 나열 — 이름/정가/첫 결제액/
   정기 결제액/주기/체험 가능 여부. "구독하기" / (trial_enabled 시) "체험 시작" 버튼.
3. **카드 등록** (`/subscribe/<plan_id>?trial=`): 토스 SDK v2
   (`https://js.tosspayments.com/v2/standard`) 로드 →
   `TossPayments(TOSS_CLIENT_KEY).payment({customerKey})` →
   `requestBillingAuth({method:"CARD", successUrl:"/billing/success?plan_id=&trial=",
   failUrl:"/billing/fail", customerEmail, customerName})` — **실제 토스 카드등록창**.
4. **구독 처리** (`/billing/success`): 쿼리의 `authKey`+`customerKey`로
   `create_subscription(...)` 호출 — 구독서버가 실제 토스 빌링키 발급
   (`/v1/billing/authorizations/issue`) + 실제 첫 결제 승인 수행.
   성공/실패(사유 포함) 결과 페이지 표시.
5. **내 구독** (`/my`): `get_subscription(email)` — 상태/요금제/만료일/다음 결제일/
   카드 정보 표시. 상태별 버튼:
   - ACTIVE/TRIAL → **구독 취소** / **카드 변경**(3번과 동일한 카드등록창 →
     `/billing/success?mode=change-card` → `change_card(...)`)
   - CANCELED(만료 전) → **재개**
   - SUSPENDED → **수동 결제**
   - 구독 없음 → 요금제 페이지 안내
6. 모든 액션 후 `/my`로 리다이렉트, 결과 메시지는 Django messages로 표시.

## 구독서버 측 변경

**없음.** 셋업 절차(README):
1. 구독서버 admin에서 서비스 등록 — 허용 IP에 `127.0.0.1`
2. 서비스 상세의 **키 복사** 모달에서 API 키/HMAC secret 복사 → sample_service `.env`
3. 요금제 1개 이상 생성(체험 포함 권장)
4. 두 서버 구동: 구독서버 `:8000`, 샘플 `:8001`

## 토스 연동 (실제 키)

- 샘플 서비스: `TOSS_CLIENT_KEY`(test_ck_...) — 카드등록창
- 구독서버: 기존 `.env`의 `TOSS_SECRET_KEY`(test_sk_...) — 이미 실제 `TossClient` 구성됨
- 테스트 결제는 토스 개발자센터(테스트 모드) 결제내역에서 확인 가능

## 에러 처리

- 구독서버 API 에러(중복 구독, 결제 실패 등) → 결과 페이지/messages에 사유 표시
- 토스 카드등록 실패 → `failUrl`(`/billing/fail?code=&message=`)에서 사유 표시
- 구독 없음(404) → `/my`에서 "구독 없음" 상태로 표현 (예외 아님)

## 테스트

- Django 테스트(자동): ① HMAC 서명 호환 — 구독서버 `sign_request`와 동일 입력→동일
  서명(고정 벡터, 구독서버 코드에서 생성한 기대값을 상수로) ② 뷰 스모크(비로그인
  리다이렉트, 로그인 후 페이지 렌더 — payment_client는 호출 직전까지만)
- **실결제 검증(수동)**: README 시나리오 — 토스 테스트카드로 카드등록 → 구독 생성 →
  구독서버 admin 결제내역 + 토스 개발자센터에서 확인 → 취소/재개/수동결제/카드변경 순회
- payment_system 레포에는 코드 변경 없음 → 기존 320+ 테스트 영향 없음

## 변경하지 않는 것

- payment_system 코드 무변경 (스펙 문서만 추가)
- Django 샘플은 데모 품질 — 회원가입/비밀번호/CSS 프레임워크 없음 (단일 CSS 파일,
  payment_system admin 디자인 토큰 차용 수준)
