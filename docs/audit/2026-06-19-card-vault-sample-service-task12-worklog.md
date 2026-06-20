# 워크로그 — Task 12: 샘플 서비스(sample_service) 카드 보관함 흐름 전환

- 날짜: 2026-06-19
- 작업자: seungjinhan (oasis@medisolveai.com)
- 범위: `sample_service/` 만 수정 (메인 서버 `app/` 미변경)

## 배경

구독서버가 **카드 보관함(Card Vault)** 모델로 전환됨(서버 Task 7/9/10):
- 카드 등록/교체: `POST /api/v1/cards` (재등록=카드 변경)
- 카드 조회/삭제: `GET` / `DELETE /api/v1/cards/{external_user_id}`
- 구독 생성/단건 결제: 더 이상 `auth_key`/`customer_key` 불필요 — 사전 등록 카드 사용(미등록 시 404)
- `change-card` 구독 엔드포인트 제거

데모(sample_service)는 구버전 흐름(구독/결제 직전마다 토스 인증)을 쓰고 있어 전면 갱신.

## 변경 내용

### payment_client.py
- 추가: `register_card`, `get_card`, `delete_card`
- 변경: `create_subscription`(본문에서 customer_key/auth_key 제거), `create_one_off_payment`(동일)
- 제거: `change_card` (재등록으로 통합)

### views.py
- 신규 `card_view` (`/card`): 등록 카드 조회 + 토스 위젯 등록/변경 + 삭제(POST delete)
- `billing_success_view`: 카드 등록 전용 콜백으로 단순화(`register_card` 호출, `next`로 복귀, 오픈 리다이렉트 방지)
- `subscribe_view`: 토스 제거 — GET=확인 화면(등록 카드 표시), POST=`create_subscription`. 카드 404 시 `/card?next=…` 유도
- `oneoff_view`: 토스/세션 보관 제거 — POST 즉시 `create_one_off_payment`. 카드 404 시 `/card?next=/pay` 유도

### urls.py
- `path("card", views.card_view)` 추가

### 템플릿
- 신규 `card.html`: 카드 등록/변경/삭제 + 토스 SDK 위젯 + **수동 authKey 폴백**(토스 키 없이 테스트)
- `subscribe.html`: 확인 화면으로 재작성(토스 제거)
- `oneoff.html`: 개발자 노트 갱신(authKey 불필요)
- `oneoff_checkout.html`: 삭제(불필요)
- `my.html`: "카드 변경" 링크 → `/card`, 개발자 노트의 change-card → POST /api/v1/cards
- `base.html`: 내비에 "카드" 메뉴 추가

### tests.py
- 신규: `CardVaultClientTest`(register/get/delete 경로·본문, subscription/oneoff 본문에서 authKey 부재, change_card 제거 검증), `CardViewTest`, `BillingSuccessRegistersCardTest`
- 갱신: `SubscribeFlowTest`(확인 화면/404 유도/POST 생성), `OneOffPaymentFlowTest`·`OneOffRecordCreationTest`(즉시 결제)

### 문서/설정
- `README.md`: 카드 보관함 모델 설명, 수동 시나리오·기능 체크리스트(15종) 갱신
- `.env.example`: PAYMENT_API_BASE 포트 보정, 주석 보강

## 테스트 결과

```
cd sample_service && .venv/bin/python manage.py test shop
Ran 75 tests in 0.22s — OK
```

## 토스 SDK 연동 메모

- 카드 등록 위젯은 토스 SDK v2 `requestBillingAuth({method:"CARD", successUrl:/billing/success?next=…})`.
- `TOSS_CLIENT_KEY`(.env)가 비면 위젯 대신 안내 + **수동 authKey 입력 폼**으로 등록 흐름 테스트 가능.
- 실제 라이브 등록 검증에는 유효한 토스 테스트 client key + 토스 콜백으로 받은 authKey가 필요.
