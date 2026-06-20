# user_manual 이해도 개선 워크로그

작성일: 2026-06-20
대상: `docs/user_manual/` (처음 접근하는 사용자 + 개발자 관점 분석 후 보완)

## 배경

전체 매뉴얼(17개 문서, 약 3,587줄)을 두 독자(비개발자 운영자 / 연동 개발자) 관점에서 분석.
"잘 이해할 수 있는가"를 점검한 결과, 구조는 우수하나 **번호 불일치 · 용어 정의 공백 · 연동 시작점/웹훅 수신 규약 누락**이 확인되어 6개 항목을 보완.

## 변경 내용

### 1. 문서 번호 체계 정렬 (중복·건너뜀 제거)
- 증상: 좌측 네비/H1 번호가 00·01 모두 "1.", 11·12 모두 "12.", 09→"10.", 16→"17." 로 **중복 2건 + 건너뜀 2건**.
- 조치: "파일 인덱스 = 섹션 번호" 체계로 통일(개요=0, 01=1 … 16=16). 02–08·12–15는 이미 정합이라 무수정.
  - `00-overview.md`: H1 `1.`→`0.`, 하위 `1-N`→`0-N`
  - `09-dashboard.md`: `10.`→`9.` (H1 및 10.1–10.6)
  - `10-install-deploy.md`: H1 `11.`→`10.`
  - `11-service-api.md`: `12.`→`11.` (H1 및 12.1–12.7, 본문 자기참조 2곳 포함)
  - `16-admin-screens.md`: `17.`→`16.` (H1 및 17.1–17.12)
- 결과: 네비 배지 0~16 연속(중복/누락 없음) 확인.

### 2. PAST_DUE 라벨 통일 → "미수" (UI 표준에 맞춤)
- 증상: 동일 상태를 `연체`(00) / `미수`(09) / `미납`(02·03·04) 세 가지로 표기.
- **정정 경위(중요)**: 1차로 `미납`으로 통일했으나, 실제 UI 렌더 라벨을 소스에서 확인하니 **PAST_DUE = "미수"**였다(`app/admin/__init__.py:26` `_SUB_STATUS_KO`, `app/services/dashboard.py:69` `_STATUS_KO`, `dashboard.html:147` "미수 구독", 구독 목록·필터 드롭다운 전부 "미수"). 매뉴얼은 화면 표기를 따라야 하므로 **전부 `미수`로 재정정**.
- 조치: user_manual 전체 `미납`→`미수`(상태 라벨) 및 `미납금`→`미수금`(금액, 코드 `미수금`과 일치). dev_manual `15`의 `연체`/`미납금`, new_manual `03-domain`의 `연체`도 `미수`로 통일. admin_manual·dev_manual 나머지는 이미 `미수`라 무수정.
- 참고: 의미상 `미납`이 더 명확하다는 의견이 있으면 **UI 코드(enums 라벨맵·템플릿) + 문서**를 함께 바꾸는 별도 작업으로 분리(이번엔 출시 UI에 맞춤).

### 3. 00-overview — 글로서리 + 독자 분기
- 핵심개념 표에 누락 용어 추가: `빌링키(billingKey)` · `customerKey` · `미수(PAST_DUE)` · `HMAC 서명` · `킬스위치`.
- 상단에 "길잡이(독자별 시작점)" 콜아웃 추가: 관리자→0-5/01, 개발자→0-2/11·10.

### 4. 11-service-api — 연동 시작점·E2E·헤더
- 11.3.1에 **`customer_key`/`auth_key` 취득 흐름**(클라이언트 토스 결제창 `requestBillingAuth`→1회용 authKey→서버 전달) 설명 추가. 가장 큰 막힘 지점 해소.
- 11.6 발송 헤더에 **`X-Event`** 명시(실제 발신 `service_notify.py:104` 대조 확인) + 타임아웃 5초·재시도 없음 명시.
- **11.8 "처음부터 끝까지 — 최소 연동 예제"** 신설(카드 등록→구독 생성→알림 수신 + sample_service 포인터).

### 5. 15-feature-notifications — 수신 측 구현 규약
- **15.7 "수신 측 구현 규약(중요)"** 신설(기존 15.7·15.8 → 15.8·15.9로 이동):
  - 전달 보장 없음(단발 at-most-once, 재시도 없음 → 수신 실패 시 영구 유실 → 조회 API 보완 권고)
  - 응답·타임아웃 계약(2xx 빠른 반환, 본문 무시, 발송 타임아웃 5초)
  - 멱등·중복 처리(전용 전달 ID 없음, `order_id`/`subscribe_id`+EVENT+STATUS+date 키, `X-Nonce`는 중복판별 금지)
  - payload 함정(`subscribe_id`≠subscription_id, `email`에 external_user_id, plan.* 비귀속)
- 15.2 제목 "이벤트 16종" → "이벤트 16종(+테스트 1종)" (16 vs 17 모순 제거).

### 6. 10-install-deploy — 백업·복구·롤백
- **7. 백업·복구·롤백** 신설: pg_dump 백업 / pg_restore 복구 / `alembic downgrade` 롤백(데이터 손실·스키마-이미지 정합 주의) / Redis 캐시 성격.
- 6장에 "세션 보안(추가 키 없음)" 보완: 세션은 별도 서명키 없이 Redis 무작위 토큰(`auth.py`) 방식 — `SECRET_KEY` 류 환경변수 없음(소스 확인 후 반영, 없는 키를 문서화하지 않음).

## 검증

- `uv run --with markdown python docs/user_manual/build.py` → "생성 완료: 17개 문서".
- 네비 배지 `0 1 … 16` 연속 확인. 신규 섹션 4종(수신 규약/E2E/백업·복구/길잡이) HTML 렌더 확인. 잔여 미변환 `.md` 링크 없음.
- 본문 자기참조(11의 11.5.4·11.2.2) 동기화, `X-Event`/타임아웃 5초는 `app/notifications/service_notify.py` 소스 대조.

### 7. 00-overview — 연동 시퀀스 개발자용 분리 (후속)
- 0-2의 "서비스가 실제로 호출하는 순서" H3를 **"(개발자용) … 연동 시퀀스"**로 재명명하고, 시퀀스 다이어그램·엔드포인트·HMAC·웹훅은 개발자용이며 운영자는 0-3으로 건너뛰어도 된다는 안내 콜아웃 추가. (앵커는 깨진 `(#)` 대신 텍스트 참조로 처리)

## 후속(정합성 점검 3종)

### A. dev_manual / new_manual / admin_manual 정합성
- **번호**: dev_manual(01–17)·new_manual은 자체 번호 체계라 user_manual의 0–16과 무관 — 손대지 않음.
- **라벨**: PAST_DUE 드리프트를 전 매뉴얼에서 `미수`로 통일(위 2번). dev_manual `15`, new_manual `03-domain` 수정, 나머지는 이미 정합.
- **웹훅 규약**: dev_manual `17-service-notifications.md:91`이 이미 "재시도·아웃박스 없음(best-effort)"을 명시 — user_manual 15.7에 추가한 수신 규약과 모순 없음. 별도 동기화 불필요.
- **백업/복구**: user_manual 10에 신설한 절은 운영자 대상. dev_manual `02-database`와 역할이 달라 중복 동기화하지 않음.

### B. sample_service 실코드 대조 (11.8 E2E · 15.7 수신규약)
- **카드 등록(A)**: `payment_client.py:100-103`이 `{external_user_id, customer_key, auth_key}` 3필드로 `POST /api/v1/cards` 호출, `billing_success_view`(`views.py:332-333`)가 토스 successUrl에서 `authKey`/`customerKey`(camelCase) 수신 → **문서와 정확히 일치(CONFIRMED)**.
- **구독(B)**: `payment_client.py:131-133` `{plan_id, external_user_id, trial}` → **일치(CONFIRMED)**.
- **수신기(C)**: `_verify_notify_signature`(`views.py:642-664`)의 canonical `POST\n{path}\n{ts}\n{nonce}\n{sha256(body)}` 및 헤더 `X-Signature/X-Timestamp/X-Nonce` → **문서와 일치**. 서버 발신 `X-Event` 헤더는 `service_notify.py:104`로 실재 확인(샘플은 본문 `EVENT`로 읽음 — 둘 다 정상).
- **보강**: 샘플은 데모 편의상 서명 불일치도 `verified=False`로 기록만 하고 200 반환 → 운영 수신기가 복사하지 않도록 15.7에 "서명 검증은 운영에서 필수" 주의 추가.

### C. 저위험 일관성
- 글로서리(빌링키·customerKey·HMAC 등) 추가로 02 §2.2의 용어 공백을 1차 해소. 교차문서 앵커 링크는 빌더 자동 id 의존이라 깨질 위험이 있어 보류(텍스트 참조 유지).

## 비고

- 정식 매뉴얼은 `docs/user_manual`의 `build.py`로 재빌드(HTML 갱신 완료). 코드/기능 변경은 없어 `docs/dev_manual`은 무관.
- 분석 시 1차 에이전트 리뷰의 "HIGH" 일부(예: env 파일 git 커밋)는 검증 결과 오탐이라 제외하고, 실제 확인된 항목만 반영.
