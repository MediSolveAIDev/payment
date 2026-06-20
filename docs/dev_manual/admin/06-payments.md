# 06. 결제 — 목록·상세·단건 취소

> **대상**: 운영자(결제 조회·실패 원인 확인·단건 취소) + 개발자(라우트·쿼리·실패코드 매핑 수정)
>
> 관련 기능 상세: [../07-one-off-payment.md](../07-one-off-payment.md)

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

결제 이력 화면은 구독 정기결제(SUBSCRIPTION)와 단건 결제(ONE_OFF) 두 종류를 **한 목록**에서 조회·관리한다. 외부 결제 내역 API(`GET /api/v1/payments/{external_user_id}`)도 구독·단건 결제를 모두 반환하며, 각 결제에 취소 수수료 안내 필드를 포함한다(15-external-api 참조).

| 항목 | 내용 |
|---|---|
| 목록 | `GET /admin/payments` |
| 상세 | `GET /admin/payments/{id}` |
| 단건 취소 | `POST /admin/payments/{id}/cancel` |
| 엑셀 내보내기 | `GET /admin/payments/export.xlsx` |
| 접근 권한 | `require_any` — SYSTEM_ADMIN, SERVICE_MANAGER 모두 허용 |
| 스코프 | SYSTEM_ADMIN은 전체 결제, SERVICE_MANAGER는 **담당 서비스** 결제만 |

> 담당 서비스가 아닌 결제 ID를 직접 URL에 입력하면 **403 대신 404**로 응답한다(결제 존재 여부 미노출).

---

## 2. 화면 구성

### 2-1. 결제 목록 (`list.html`)

**툴바(검색·필터)**

| 필터 | 쿼리 파라미터 | 동작 |
|---|---|---|
| 검색어 | `q` | `order_id` 또는 `external_user_id` 부분 일치(ilike) |
| 서비스 | `service_id` | UUID 파싱 성공 시만 적용, 실패 시 무시 |
| 요금제명 | `plan_name` | 구독 결제에만 의미 있음(단건은 Plan이 없으므로 자동 제외) |
| 종류 | `kind` | SUBSCRIPTION(구독) / ONE_OFF(일반) / 전체 |
| 상태 | `status` | DONE / FAILED / PENDING / CANCELED / 전체 |
| 날짜 범위 | `from`, `to` | `requested_at` 기준, YYYY-MM-DD. `to`는 익일 0시 미만(반개구간) |

서비스 필터를 변경하면 요금제 드롭다운이 자동으로 초기화된다(`onchange`에서 `plan_name.value = ''`).

**테이블 컬럼**

| 컬럼 | 정렬 가능 | 설명 |
|---|---|---|
| 주문번호 | Y | `payment.order_id` — 상세 링크. 모노스페이스 폰트 |
| 서비스 | — | 소속 서비스명 |
| 종류 | — | `SUBSCRIPTION` → "구독" 뱃지, `ONE_OFF` → "일반" 뱃지 |
| 사용자 | — | `external_user_id`(없으면 `-`) |
| 유형 | — | `payment_type` 뱃지: FIRST / RENEWAL / RETRY / ONE_OFF |
| 금액 | Y | 천 단위 콤마 + 원. **취소 건은 그 아래에 작게 "환불 N원 · 수수료 M원"** 표기(빨강) |
| 상태 | Y | `PaymentStatus` 한글 뱃지: 대기 / 완료 / 실패 / **취소** (`payment_status_ko()` 전역) |
| 실패 코드 | — | 있으면 점선 밑줄(`code-tip`), **마우스 올리면 의미 툴팁** |
| 요청 시각 | Y (기본 내림차순) | KST `YYYY-MM-DD HH:MM` |

**페이지네이션** — 기본 15건/페이지(`PER_PAGE_DEFAULT = 15`, `pagination.py:18`). 총 건수·범위 표시.

**엑셀 다운로드** — 툴바 오른쪽 "엑셀" 버튼. 현재 필터/정렬 그대로 **전체** 행을 `.xlsx`로 내보낸다(페이지 제한 없음).

---

### 2-2. 결제 상세 (`detail.html`)

결제 1건의 모든 항목을 표시한다.

| 항목 | 모델 필드 | 비고 |
|---|---|---|
| 주문번호 | `payment.order_id` | 페이지 헤더에도 표시(모노스페이스) |
| 상품명 | `payment.order_name` | 토스 orderName. 단건결제=클라이언트 전달값, 구독결제=요금제명(`plan.name`). 과거 데이터는 `-` |
| 종류 | `payment.kind` | 구독/일반 뱃지 |
| 결제유형 | `payment.payment_type` | FIRST·RENEWAL·RETRY·ONE_OFF 뱃지 |
| 서비스 | `service.name` | |
| 사용자 | `payment.external_user_id` | |
| 결제 카드 | `raw_response.card.number` 우선 / `card.card_info.number` 폴백 | 실제 충전 카드(토스 응답)를 우선 표시, 없으면 현재 보관함 카드. 카드 상세(`/admin/cards/{id}`) 링크 동반. `payment_detail`이 `get_card(service_id, external_user_id)`로 로드 |
| 금액 | `payment.amount` | 원 단위 정수(BigInteger) |
| 상태 | `payment.status` | 뱃지 |
| 실패 코드 | `payment.failure_code` | `code-tip` + `data-tip` 툴팁(의미 → 없으면 메시지) |
| 실패 메시지 | `payment.failure_message` | 토스페이먼츠 원문 메시지 |
| 요청 시각 | `payment.requested_at` | KST `YYYY-MM-DD HH:MM:SS` |
| 승인 시각 | `payment.approved_at` | DONE일 때만 채워짐 |
| 토스 결제키 | `payment.toss_payment_key` | 취소·조회 시 사용. 모노스페이스 |
| 연결 구독 | `payment.subscription_id` → `sub` | 구독 결제면 구독 상세 링크; 단건은 `-` |

**원본 응답 카드** — `payment.raw_response`가 있으면 토스페이먼츠 API 응답 JSON을 `<pre>`로 표시한다.

**결제 취소 카드** — `kind == ONE_OFF` AND 잔여 환불가능액(`amount − canceled_amount`) > 0일 때 표시(어드민은 항상 허용 — `cancellation_enabled` 무시). 취소 금액(원) 또는 비율(%) 입력 → 미리보기 → 취소 실행. 비우면 잔여 전액. 자세한 동작은 3-4 참조.

**취소·환불 내역 카드** — `canceled_amount > 0`이면 표시(전액취소 CANCELED·부분취소 DONE 모두). 총 결제금액(`amount`) · **누적 환불액**(`canceled_amount`, 빨강) · 잔여 환불가능액 + 부분/전액취소 뱃지 · (외부 취소 시) 취소 수수료(`cancel_fee`) · 최근 취소 시각(`canceled_at`). 상단 상태 셀에는 부분취소면 "부분취소" 뱃지가 병기된다.

---

## 3. 할 수 있는 동작

### 3-1. 검색·필터·정렬

목록 툴바의 검색어/드롭다운/날짜 입력을 채우고 "검색" 버튼을 누르거나, 드롭다운·날짜 변경 시 `onchange="this.form.requestSubmit()"` 로 자동 제출된다. 컬럼 헤더 링크를 클릭하면 정렬 방향이 토글된다.

- 필터를 모두 지우려면 "초기화" 텍스트 링크 클릭 → `GET /admin/payments`로 이동.
- 현재 필터·정렬 상태는 URL 쿼리스트링에 유지되어 링크 공유·새로고침에 안전하다.

### 3-2. 실패 코드 툴팁 확인

목록 또는 상세의 실패 코드 텍스트에 마우스를 올리면 **커스텀 팝업 툴팁**이 나타난다.

- 코드에 점선 밑줄(CSS `.code-tip`) + `data-tip` 속성에 의미 텍스트가 저장된다.
- `admin.js:246–252` — `mouseover`/`mouseout` 이벤트로 `.tip-pop` `<div>`를 body에 부착·제거한다. 테이블 overflow에 잘리지 않도록 `position: absolute` body 기준으로 배치된다.
- 의미 결정 우선순위: `payment_error_meaning(code)` 반환값 → 없으면 `payment.failure_message` → 없으면 코드 원문.

### 3-3. 엑셀 내보내기

"엑셀" 버튼 클릭 → `GET /admin/payments/export.xlsx?{현재 필터 파라미터}` 호출. 현재 검색/필터 조건이 쿼리스트링으로 그대로 전달된다. 다운로드되는 파일은 `payments_{timestamp}.xlsx`이며 시트명은 "결제".

엑셀 컬럼 순서: 주문번호 · 서비스 · 종류(구독/일반) · 사용자 · 유형 · 금액 · 상태 · 실패코드 · 요청시각.

### 3-4. 단건 결제 취소 — 어드민(전액/부분, 수수료 없음) vs 외부(수수료)

취소는 **행위자에 따라 두 갈래**다.

| 경로 | 함수 | 수수료 | 게이트(cancellation_enabled) | 부분/누적 |
|------|------|--------|------------------------------|-----------|
| **어드민(관리자)** | `admin_cancel_one_off_payment()` | **없음**(지정 금액 그대로 환불) | **무시**(항상 허용) | 전액 또는 부분, **여러 번 누적** |
| **외부 서비스(API/사용자)** | `cancel_one_off_payment()` | **수수료율 적용**(`compute_cancel_fee`) | 적용(꺼져 있으면 취소 불가) | 단발 전액(수수료 공제) |

#### 어드민 취소 (결제 상세 "결제 취소" 카드)

- 노출 조건: `payment.kind == ONE_OFF` AND **잔여 환불가능액(`amount − canceled_amount`) > 0**. (구독·전액취소 완료 건은 미노출)
- 입력: **취소 금액(원)** 또는 **비율(%)** — % 입력 시 JS가 `floor(잔여 × %/100)`로 취소금액을 계산해 미리보기에 표시한다. 비우면 **잔여 전액** 취소.
- 흐름: `confirm()` → `POST /admin/payments/{id}/cancel`(form `cancel_amount`, 빈값=전액) → CSRF·스코프 검증 → `admin_cancel_one_off_payment()`.
- 처리(`app/services/payments.py`):
  - `0 < cancel_amount ≤ 잔여` 검증(초과 시 `InputValidationError`).
  - 토스 `cancel_payment(cancel_amount=환불액)`(최초 전액취소만 생략).
  - `canceled_amount`에 **누적**, 잔여 0이면 `status=CANCELED`, 아니면 **`DONE` 유지(추가 취소 가능)**. `cancel_fee`는 0(무수수료).
  - 토스 실패 시 상태·누적액 보존 + `payment.cancel_failed` 감사.
- 상태 표현: 부분취소된 결제는 `status=DONE`이며 화면에서 **"부분취소" 뱃지**로 표시(`canceled_amount>0`로 파생). 전용 `PARTIAL_CANCELED` enum은 도입하지 않는다.

> 어드민이 이미 부분취소한 결제는 외부(사용자) 전액취소가 차단된다(`canceled_amount>0`이면 `ConflictError` — 이중환불 방지).

#### 외부 서비스(사용자) 취소 — 기존 동작

```
# app/services/billing_math.py — compute_cancel_fee() 공유
수수료 = payment.amount × service.cancellation_fee_percent // 100
환불액 = payment.amount − 수수료
```
- `cancellation_enabled`가 꺼져 있으면 취소 불가. 수수료율 0%면 전액, >0이면 수수료 공제 부분취소.

**감사 로그 actor 분기**: 어드민 취소 `actor_type="USER"`(actor_user_id), 외부 취소 `actor_type="SERVICE"`(actor_service_id). 액션은 둘 다 `payment.canceled`/`payment.cancel_failed`.

---

## 4. 개발 참조

### 라우트 함수 (`app/admin/routes/payments.py`)

| 함수 | 경로 | 라인 |
|---|---|---|
| `payments_list` | `GET /payments` | `payments.py:164` |
| `payment_detail` | `GET /payments/{payment_id}` | `payments.py:140` |
| `payment_cancel` | `POST /payments/{payment_id}/cancel` | `payments.py:112` |
| `payments_export` | `GET /payments/export.xlsx` | `payments.py:89` |

### JOIN 전략 (`_build_payments_query`, `payments.py:35–86`)

```
Payment
  OUTER JOIN Subscription  ← 단건(ONE_OFF)은 subscription_id가 NULL → INNER이면 누락
  OUTER JOIN Plan          ← Subscription 경유 → 단건은 Plan도 NULL
  INNER JOIN Service       ← 모든 결제는 반드시 서비스에 속함
```

- `plan_name` 필터 선택 시 Plan이 없는 단건 결제는 자동 제외된다.
- 스코프(`service_scope(ctx)`) — `None`이면 전체, UUID 목록이면 `Payment.service_id.in_(scope)`로 제한.
- `service_id` 파라미터가 UUID 파싱 실패 시 `pp.filters`에서 제거하고 조용히 무시(`payments.py:75–80`).

### 정렬 가능 컬럼 (`payments.py:29–32`)

```python
_PAY_SORT = {
    "order_id": Payment.order_id,
    "amount": Payment.amount,
    "status": Payment.status,
    "requested_at": Payment.requested_at,  # 기본 내림차순
}
```

### 템플릿

| 파일 | 역할 |
|---|---|
| `app/admin/templates/payments/list.html` | 결제 목록 전체 페이지 |
| `app/admin/templates/payments/detail.html` | 결제 상세·취소·환불 카드 |
| `app/admin/templates/_list.html` | toolbar·sort_th·pager 공통 매크로 |

> 결제 목록은 htmx 부분 갱신(partial 스왑) 대상이 아니다. `render_list` 대신 `render`를 사용하며 별도 `_table.html`이 없다(`payments.py:173` 주석 참조).

### 실패 코드 의미 매핑 (`app/admin/payment_error_labels.py`)

`PAYMENT_ERROR_LABELS` 딕셔너리(`payment_error_labels.py:12–48`)에 코드 → 한글 의미가 정의되어 있다.

**코드 분류:**

| 분류 | 예시 코드 |
|---|---|
| 카드 거절/한도/잔액 | `REJECT_CARD_COMPANY`, `REJECT_CARD_PAYMENT`, `REJECT_ACCOUNT_PAYMENT` |
| 카드 정보/상태 | `INVALID_CARD_NUMBER`, `INVALID_STOPPED_CARD`, `INVALID_CARD_LOST_OR_STOLEN` |
| 금액/한도 | `BELOW_MINIMUM_AMOUNT`, `EXCEED_MAX_AMOUNT`, `EXCEED_MAX_MONTHLY_PAYMENT_AMOUNT` |
| 일시/시스템 오류 | `PROVIDER_ERROR`, `CARD_PROCESSING_ERROR`, `UNKNOWN_PAYMENT_ERROR` |
| 인증/요청 | `INVALID_PASSWORD`, `UNAUTHORIZED_KEY`, `NOT_FOUND_PAYMENT_SESSION` |
| 우리 서버 내부 코드 | `NO_BILLING_KEY`, `CANCEL_DISABLED`, `PAYMENT_UNRESOLVED`, `SERVER_DISABLED` |

**새 코드 추가 방법:**

`PAYMENT_ERROR_LABELS` 딕셔너리에 `"코드": "한글 의미"` 항목을 추가한다. 매핑에 없는 코드는 `payment_error_meaning(code)`가 빈 문자열을 반환하고, 템플릿이 `payment.failure_message`로 폴백한다(`payment_error_labels.py:51–55`).

### 툴팁 동작 흐름 (운영자 문의 대응용)

```
템플릿(list.html:36 / detail.html:22)
  └─ <span class="code-tip" data-tip="{ payment_error_meaning(code) or failure_message or code }">
        ↓ mouseover
admin.js:246  e.target.closest("[data-tip]")
admin.js:231  showTip() → .tip-pop div를 body에 append, position:absolute
              요소 아래 6px에 표시, 우측 경계 넘지 않도록 left 보정
admin.js:245  hideTop() → div.remove()
admin.css:427 .code-tip { border-bottom: 1px dotted } ← 호버 가능 시각 힌트
admin.css:429 .tip-pop { position:absolute; z-index:1200; max-width:280px }
```

### 관련 서비스·모델 파일

| 파일 | 역할 |
|---|---|
| `app/models/payment.py` | Payment ORM(kind·status·failure_code·canceled_amount 등) |
| `app/models/enums.py` | PaymentKind·PaymentStatus·PaymentType 열거형 |
| `app/models/service.py` | Service.cancellation_enabled·cancellation_fee_percent |
| `app/services/payments.py:166` | `cancel_one_off_payment()` — 취소 도메인 로직·감사 기록 |
| `app/admin/export.py` | `xlsx_response()` — 엑셀 생성 |
| `app/admin/pagination.py` | `PageParams`, `paginate()`, `date_range()` |

---

## 5. 주의사항 / 자주 하는 실수

**취소 불가 상황 오해**

- "취소" 카드가 표시되지 않으면 `kind != ONE_OFF` 또는 `status != DONE`인 경우다. 구독 결제(`kind == SUBSCRIPTION`)는 어드민 결제 화면에서 취소할 수 없다. 구독 취소는 [05-subscriptions.md](05-subscriptions.md) 참조.
- `service.cancellation_enabled == False`이면 버튼 자체 없이 "취소 불가(서비스 정책)" 뱃지만 나타난다. 서비스 설정(`/admin/services/{id}`)에서 변경 가능.

**요금제 필터 선택 시 단건 결제 제외**

`plan_name` 드롭다운을 선택하면 Plan이 없는 단건 결제는 결과에서 사라진다. 이는 JOIN 설계상 의도된 동작이다(단건 결제에는 Plan이 없음).

**실패 코드 툴팁이 안 보일 때**

- `payment.failure_code`가 `None`이면 `<span class="code-tip">`이 렌더되지 않는다. 단지 `-`만 표시된다.
- `PAYMENT_ERROR_LABELS`에 없는 코드는 툴팁에 `failure_message`(있으면) 또는 코드 원문이 표시된다.
- htmx 스왑 후에도 툴팁이 화면에 남는 경우 `admin.js:253` — `htmx:beforeSwap` 이벤트에서 `hideTip()`을 호출하여 제거한다.

**엑셀 내보내기와 페이지네이션**

엑셀 URL은 `pp.query_without('page')`로 생성되어 항상 전체 행을 내보낸다. 현재 필터는 그대로 반영되므로, 필터 없이 내보내려면 "초기화" 후 다운로드한다.

**스코프와 직접 URL 접근**

SERVICE_MANAGER가 담당 외 결제의 `{id}`를 직접 URL에 입력하면 **404**를 반환한다. 403이 아닌 이유는 결제 존재 여부를 외부에 노출하지 않기 위해서다(`payments.py:155–156`).

**CSRF 토큰 누락 → 403**

"결제 취소" 폼에는 `<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">` 가 반드시 있어야 한다. 없거나 세션 토큰과 불일치하면 `validate_csrf()`(`deps.py:105`)에서 403으로 차단된다.
