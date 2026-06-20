# 05. 구독 관리 — 목록·상세·강제 취소

> **대상**: 운영자 + 개발자 하이브리드.
> 외부 API를 통한 구독 시작·재개·카드 변경 등의 흐름은 [../06-subscription-manage.md](../06-subscription-manage.md)를 참고하세요.

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

어드민 콘솔에서 **모든 구독 레코드를 조회·검색·필터링**하고, 필요한 경우 **운영자가 직접 구독을 강제 취소**하는 화면입니다.

| 경로 | 설명 |
|------|------|
| `GET /admin/subscriptions` | 구독 목록 |
| `GET /admin/subscriptions/{id}` | 구독 상세 |
| `POST /admin/subscriptions/{id}/force-cancel` | 강제 취소 처리 |
| `POST /admin/subscriptions/{id}/retry-payment` | **결제 처리(재결제)** — 실패/정지 구독을 등록 카드로 즉시 청구 |
| `POST /admin/subscriptions/{id}/extend` | **만료일 연장** — 입력 날짜로 만료일·다음결제 변경, 상태=연장처리(EXTENDED) |
| `GET /admin/subscriptions/export.xlsx` | 목록 엑셀 다운로드 |

**접근 권한**: `require_any` — SYSTEM_ADMIN과 SERVICE_MANAGER 모두 접근할 수 있습니다.

**스코프(중요)**:
- **SYSTEM_ADMIN**: 전체 서비스의 모든 구독에 접근합니다.
- **SERVICE_MANAGER**: `service_scope(ctx)`가 반환하는 **담당 서비스의 구독에만** 접근합니다. 담당 범위 밖 구독은 목록에서 보이지 않고, 직접 URL로 접근해도 404로 응답합니다(존재 여부 노출 방지).

---

## 2. 화면 구성

### 2-1. 구독 목록

**파일**: `app/admin/templates/subscriptions/list.html` (L1–7), `app/admin/templates/subscriptions/_table.html` (L1–39)

목록 화면은 `list.html`이 셸을 담당하고, 검색·테이블·페이지네이션은 `_table.html` partial이 렌더합니다. htmx 요청 시에는 partial만 교체됩니다(`render_list` — `app/admin/routes/subscriptions.py` L126).

#### 툴바 (검색·필터)

`_table.html` L3–9에서 `_list.html`의 `toolbar` 매크로를 호출합니다.

| 컨트롤 | 파라미터 | 동작 |
|--------|----------|------|
| 검색창 | `q` | `external_user_id` 부분 일치 검색(ilike) |
| 서비스 드롭다운 | `service_id` | 서비스별 필터. SYSTEM_ADMIN은 전체, SERVICE_MANAGER는 담당 서비스만 표시 |
| 요금제 드롭다운 | `plan_name` | 요금제명 정확 일치. 서비스 선택 시 해당 서비스 요금제만 표시 |
| 상태 드롭다운 | `status` | TRIAL / ACTIVE / PAST_DUE / SUSPENDED / CANCELED / EXPIRED / 전체 |
| 날짜 범위 | `from`, `to` | `created_at` 범위(YYYY-MM-DD). `to`는 익일 00:00(반개구간)으로 변환 |
| 엑셀 버튼 | — | 현재 필터 유지한 채 `/admin/subscriptions/export.xlsx` 다운로드 |
| 초기화 링크 | — | 검색어나 필터가 하나라도 있을 때 표시. 전체 초기화 |

서비스 드롭다운 변경 시 요금제 필터(`plan_name`)가 자동으로 빈 값으로 초기화됩니다(`_table.html` L12, `onchange` JS).

#### 테이블 컬럼

`_table.html` L11–36

| 컬럼 | 데이터 출처 | 정렬 가능 |
|------|-----------|---------|
| 서비스 | `Service.name` | 불가 |
| 사용자 | `Subscription.external_user_id` | 가능(`external_user_id`) |
| 요금제 | `Plan.name` | 불가 |
| 상태 | `Subscription.status` (배지) | 가능(`status`) |
| 만료일 | `Subscription.current_period_end` (KST YYYY-MM-DD) | 가능(`current_period_end`) |
| 다음 결제 | `Subscription.next_billing_at` (KST YYYY-MM-DD HH:MM) | 가능(`next_billing_at`) |
| (액션) | — | 불가 |

기본 정렬은 `created_at` 내림차순입니다(`app/admin/routes/subscriptions.py` L113).

행 전체 클릭 시 해당 구독의 상세 페이지로 이동합니다(`_table.html` L23).

#### 페이지네이션

한 페이지 15건(`PER_PAGE_DEFAULT = 15`, `app/admin/pagination.py` L18). `총 N건 중 start–end`, 이전/숫자/다음 버튼. htmx partial 갱신(`target='list-subs'`).

---

### 2-2. 구독 상세

**파일**: `app/admin/templates/subscriptions/detail.html` (L1–61)

상단에 외부 사용자 ID와 현재 상태 배지, 뒤로 가기 링크(`← 구독`)가 표시됩니다(L5–9).

오류가 있을 때(`?error=` 쿼리스트링)는 에러 메시지 박스가 표시됩니다(L10).

2단 그리드 레이아웃: **구독 정보 카드**(왼쪽) + **관리 카드**(오른쪽), 아래에 **결제 이력 카드**.

#### 구독 정보 카드 (`detail.html` L12–25)

| 항목 | 데이터 |
|------|--------|
| 구독 ID(KEY) | `sub.id`(UUID) — 외부 식별·문의 대응용 (요청) |
| 서비스 | `service.name` |
| 요금제 | `plan.name` · `plan.price`원 |
| 결제 주기(반복회차) | `plan.billing_cycle` + `plan.cycle_days`일(일 주기인 경우) · 지금까지 N회 결제(`paid_count`) |
| 구독 시작일 | `sub.current_period_start` (KST YYYY-MM-DD) |
| 만료일 | `sub.current_period_end` (KST YYYY-MM-DD) |
| 다음 결제 | `sub.next_billing_at` (KST YYYY-MM-DD HH:MM) |
| 재시도 | `sub.retry_count`회 |
| 정지 시각 | `sub.suspended_at` (KST) — **SUSPENDED 상태일 때만 표시** |
| 카드 | `sub.card_info.number` 또는 `-` |

**만료일 연장 이력 카드(요청)**: 이 구독의 `subscription.extended` 감사 이벤트가 있으면 상세 페이지에
"만료일 연장 이력" 카드가 표시된다(처리 시각 / 변경 전 만료일 / 변경 후 만료일 / 처리자). 라우트
`subscription_detail`이 해당 감사 이벤트를 최신순 조회해 `extensions`로 전달한다.

**카드 정보 표시 범위**: `card_info`는 토스페이먼츠에서 반환한 **마스킹된 카드번호**만 JSONB로 보관합니다(`app/models/subscription.py` L37). 실제 카드번호·CVC 등 민감 정보는 저장하지 않으며, billingKey는 AES 암호화 후 별도 컬럼에 저장되므로 화면에 노출되지 않습니다.

`paid_count`는 `Payment.status == "DONE"` 건수를 별도 집계합니다(라우트 L156–158). 결제 이력을 최대 200건만 가져오더라도 `paid_count`는 정확한 전체 횟수를 보여줍니다.

#### 관리 카드 — 결제 처리 / 강제 취소 버튼 (`detail.html` 관리 카드)

관리 카드에는 구독 상태에 따라 두 가지 작업 버튼이 표시됩니다.

**① 결제 처리(재결제) — `sub.status`가 PAST_DUE 또는 SUSPENDED일 때**

자동결제가 실패(PAST_DUE)했거나 재시도 소진으로 정지(SUSPENDED)된 구독을, **운영자가 등록된 카드로 즉시 재청구**하는 버튼입니다. 청구 예정액(상시 할인 적용 후, `charge_amount` = `plan_recurring_amount(plan)`)을 안내한 뒤 확인 다이얼로그를 거쳐 실행합니다.

- 라우트: `POST /admin/subscriptions/{id}/retry-payment` → 서비스 `subscriptions.admin_retry_payment()`
- 동작: 스코프 검사(담당 서비스만, 아니면 404) → 상태 검증(PAST_DUE/SUSPENDED만, 아니면 `ConflictError`) → 등록 빌링키로 청구. **성공 시 ACTIVE 복귀 + 결제 기준일 리셋 + `retry_count=0`**. 외부 API의 수동결제와 동일한 코어(`_perform_manual_charge`)를 공유하며, 감사 로그는 **`actor_type=USER`**(작업한 관리자)로 `subscription.manual_pay`를 남깁니다.
- 결과 표시: 성공 → "결제가 완료되었습니다" 완료 모달 / 실패(카드 거절·결제수단 없음 등) → `?error=`로 상세 페이지에 메시지. 실패해도 상태는 그대로 유지되고 결제 이력에 `FAILED`로 남습니다.
- 등록된 카드(`sub.card_info`)가 없으면 버튼이 비활성화됩니다(빌링키 없음 안내).

> 카드 자체가 만료/한도 초과인 경우 재결제는 계속 실패합니다. 이때 카드 교체는 외부 서비스의 `change-card` API 경로로 이뤄집니다(어드민 화면에는 카드 교체 기능 없음).

**② 강제 취소 — `sub.status`가 ACTIVE·PAST_DUE·EXTENDED일 때**

버튼이 표시됩니다. PAST_DUE 상태에서는 ①결제 처리와 ②강제 취소가 함께 보입니다. 연장처리(EXTENDED) 구독도 강제 취소할 수 있습니다.

**③ 만료일 연장 — `sub.status`가 EXPIRED가 아닐 때(요청)**

운영자가 만료일을 수동으로 연장하는 기능입니다. 날짜 입력(`<input type="date" name="new_end">`) 후 제출하면:
- 라우트: `POST /admin/subscriptions/{id}/extend` → 서비스 `subscriptions.extend_subscription()`. 폼의 날짜(YYYY-MM-DD)를 UTC 자정 datetime으로 변환해 전달(감사 purge 라우트와 동일 정책).
- 동작: 스코프 검사(404) → 상태 검증(열린 구독 5개만; **EXPIRED는 ConflictError**) → 미래 날짜 검증(과거면 InputValidationError) → **만료일(`current_period_end`)·다음 결제일(`next_billing_at`)을 입력일로 설정**, 상태를 **EXTENDED(연장처리)**로 전이(중앙 `transition()` 경유), `retry_count=0`·`suspended_at=None` 정리.
- 새 만료일(=다음결제일) 도래 시 갱신 배치가 **자동결제로 갱신**(성공 시 ACTIVE)합니다(`DUE_STATUSES`에 EXTENDED 포함).
- 결과: 성공 → "만료일이 연장되었습니다" 완료 모달 / 날짜 오류·과거·상태 불가 → `?error=` 메시지.
- 감사 로그(`subscription.extended`)에 상태·만료일·다음결제의 **변경 전 → 후**를 상세히 기록합니다.

#### 결제 이력 카드 (`detail.html` L41–59)

이 구독에 연결된 결제 최대 200건을 `requested_at` 내림차순으로 표시합니다.

| 컬럼 | 내용 |
|------|------|
| 주문번호 | `p.order_id` 앞 24자 + `…` (등폭 폰트) |
| 유형 | `p.payment_type` 배지 |
| 금액 | `p.amount`원 |
| 상태 | `p.status` 배지 |
| 실패 사유 | `p.failure_code` 또는 `-` |
| 요청 시각 | `p.requested_at` (KST YYYY-MM-DD HH:MM) |

---

## 3. 할 수 있는 동작

### 3-1. 목록 검색·필터

1. 검색창에 사용자 ID(외부 서비스 측 식별자)를 입력하고 검색 버튼 클릭 → 부분 일치 검색.
2. 서비스·요금제·상태 드롭다운에서 선택하면 자동 제출(onchange).
3. 날짜 범위를 선택하면 자동 제출.
4. 컬럼 헤더 클릭 → 오름차순/내림차순 전환. 현재 정렬 방향은 화살표 아이콘으로 표시.
5. 검색/필터 결과는 URL에 반영되어 북마크·공유 가능.

### 3-2. 엑셀 다운로드

"엑셀" 버튼 클릭 → 현재 필터가 그대로 적용된 전체 결과를 xlsx로 내려받습니다(페이지네이션 없이 전체 건수).

파일명: `subscriptions-{YYYYmmdd-HHMM(KST)}.xlsx`

컬럼: 서비스 / 사용자 / 요금제 / 상태 / 만료일 / 다음 결제 (`app/admin/routes/subscriptions.py` L91–96).

### 3-3. 강제 취소

**허용 상태**: ACTIVE, PAST_DUE 두 가지만 허용합니다.

**진행 순서**:
1. 구독 상세 화면 → "관리" 카드의 **강제 취소** 버튼(빨간색, Ban 아이콘) 클릭.
2. 확인 모달 표시: "구독을 강제 취소할까요?" / "이 구독을 즉시 취소합니다. 만료일까지 혜택은 유지됩니다." → **강제 취소** 버튼 클릭.
3. `POST /admin/subscriptions/{id}/force-cancel` 제출(CSRF 토큰 포함).
4. 처리 완료 후 상세 페이지로 303 리다이렉트 + "구독이 해지되었습니다" 완료 모달.

**결과**: 상태가 **CANCELED**로 변경되고, `next_billing_at`이 `None`으로 설정되어 자동 갱신이 차단됩니다. 이미 결제된 **만료일(`current_period_end`)까지는 혜택이 유지**되며, 배치가 만료일 이후에 EXPIRED로 전환합니다.

**감사 상세(요청 015)**: 강제 취소·결제 처리 동작은 감사 detail에 상세 정보를 남긴다. 강제 취소(`subscription.force_cancel`)는 `{사용자, 요금제, old_status, new_status}`로 "상태 ACTIVE → CANCELED"처럼, 결제 처리(`subscription.manual_pay`)는 `{주문번호, 금액, 사용자, 상태 전/후}`로 기록된다.

**만료일 연장 감사**: `subscription.extended`는 `{사용자, old/new_status, old/new_period_end, old/new_next_billing_at}`을 남겨 감사로그에 "만료일 2026-06-30 → 2026-09-30 · 다음 결제일 … → … · 상태 ACTIVE → EXTENDED"처럼 표시된다.

**취소 불가 상태**: TRIAL·CANCELED·SUSPENDED·EXPIRED 상태에서는 버튼이 표시되지 않으며, API로 직접 호출해도 `ConflictError`(충돌 오류)가 반환됩니다.

---

## 4. 구독 상태값 의미표

`app/models/enums.py` L67–73, `app/models/subscription.py` L4–6

| 상태 | 의미 | 서비스 접근 | 자동갱신 | 재구독 가능 |
|------|------|-----------|---------|-----------|
| `TRIAL` | 체험 기간 — 만료 시 첫 정기 결제 시도 | O | O | 불가(열린 구독) |
| `ACTIVE` | 정상 이용 중 | O | O | 불가(열린 구독) |
| `PAST_DUE` | 결제 실패/유예 — 접근은 유지, 재시도 중 | O | 재시도 중 | 불가(열린 구독) |
| `SUSPENDED` | 강제 정지 — 접근 차단, 수동 결제 대기 | X | X | 불가(열린 구독) |
| `CANCELED` | 해지 예약 — 만료일까지 혜택 유지, 이후 EXPIRED | O | X | 불가(열린 구독) |
| `EXTENDED` | **연장처리 — 운영자가 만료일을 수동 연장. 새 만료일에 자동결제 갱신** | O | O | 불가(열린 구독) |
| `EXPIRED` | 완전 종료 — 재구독 가능 | X | X | **가능** |

상태 전환 흐름: `TRIAL → ACTIVE → PAST_DUE → SUSPENDED → EXPIRED`, `→ CANCELED → EXPIRED`. 운영자가 만료일을 연장하면 열린 구독은 어디서든 `→ EXTENDED`로 전이하고, 새 만료일 갱신 성공 시 `EXTENDED → ACTIVE`로 복귀합니다.

서비스 접근 허용 상태(`ACCESS_ALLOWED_STATUSES`): TRIAL·ACTIVE·PAST_DUE·CANCELED·**EXTENDED**.

"열린 구독" 상태(`OPEN_SUBSCRIPTION_STATUSES`): EXPIRED를 제외한 **나머지 6개**(EXTENDED 포함). 서비스+사용자 당 이 상태 중 하나만 존재할 수 있고(부분 유니크 인덱스 — EXTENDED 포함하도록 마이그레이션 `c1d2e3f4a5b6`), EXPIRED가 된 뒤에만 재구독이 가능합니다.

---

## 5. 개발 참조

### 라우트 함수

모든 구독 라우트: `app/admin/routes/subscriptions.py`

| 라우트 | 함수 | 위치 |
|--------|------|------|
| `GET /subscriptions/export.xlsx` | `subscriptions_export` | L79–97 |
| `GET /subscriptions` | `subscriptions_list` | L100–131 |
| `GET /subscriptions/{sub_id}` | `subscription_detail` | L134–161 |
| `POST /subscriptions/{sub_id}/force-cancel` | `subscription_force_cancel` | L164–180 |

### 쿼리 빌더

`subscription_query(filters.py)(pp, ctx)` (`app/admin/routes/subscriptions.py` L37–76): 목록과 엑셀이 공유하는 검색/필터 쿼리. `Subscription`, `Plan`, `Service`를 항상 JOIN합니다.

정렬 가능 컬럼 맵은 `SUB_SORT` 딕셔너리(`L28–34`)에 정의됩니다.

### 스코프 검사

- 목록: `subscription_query(filters.py)` 내에서 `service_scope(ctx)`가 UUID 목록이면 `WHERE service_id IN (...)` 조건이 추가됩니다(`L56–58`).
- 상세: `subscription_detail`에서 `scope is not None and sub.service_id not in scope`이면 404(`L149–150`).
- 강제 취소: `force_cancel_subscription` 서비스 함수에서 동일하게 검사(`app/services/subscriptions.py` L470–472).

### 강제 취소 서비스 호출

`app/admin/routes/subscriptions.py` L175–178:

```python
await validate_csrf(request, ctx)
await force_cancel_subscription(db, subscription_id=sub_id,
                                service_scope=service_scope(ctx),
                                actor_user_id=ctx.user.id)
```

`force_cancel_subscription` 정의: `app/services/subscriptions.py` L460–481.

처리 내용:
1. 구독 조회 + 스코프 검사 (L470–472).
2. 상태가 ACTIVE·PAST_DUE가 아니면 `ConflictError` (L473–474).
3. `status = CANCELED`, `next_billing_at = None` 설정 (L475–476).
4. 감사 로그 `action="subscription.force_cancel"` 기록 (L477–479).
5. `db.commit()` (L480).

### 감사 로그

강제 취소 성공 시 감사 로그에 `action="subscription.force_cancel"`, `target_type="subscription"`, `target_id=str(sub.id)`가 기록됩니다(`app/services/subscriptions.py` L477–479). 감사 로그 조회는 [09-audit.md](09-audit.md)를 참고하세요.

### CSRF

폼에 `<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">` 포함(`detail.html` L33). `validate_csrf`가 폼 필드 또는 `X-CSRF-Token` 헤더와 세션 토큰을 비교합니다(`app/admin/deps.py` L105–110).

### htmx partial 갱신

목록 화면은 검색·필터·정렬·페이지네이션 모두 htmx로 `#list-subs` div만 교체합니다. `HX-Request` 헤더가 있으면 `render_list`가 `_table.html`만 렌더하고, 없으면 `list.html` 전체를 렌더합니다(`app/admin/routes/subscriptions.py` L126).

### 드롭다운 옵션 생성

- 서비스 옵션: `app/admin/filters.py` `service_options()` (L10–17) — 스코프 내, 이름순.
- 요금제 옵션: `app/admin/filters.py` `plan_name_options()` (L20–31) — 스코프 내 distinct 요금제명. 서비스 선택 시 해당 서비스 요금제만.

---

## 주의사항 / 자주 하는 실수

- **강제 취소는 되돌릴 수 없습니다.** 취소 후 만료일까지는 혜택이 유지되지만, 자동갱신이 차단되므로 만료일 이후 EXPIRED로 확정됩니다. 재활성화하려면 사용자가 재구독(외부 API)해야 합니다.
- **SUSPENDED 상태는 강제 취소 불가.** 정지 상태 구독을 취소하려면 외부 API를 통해 CANCELED 상태로 변경하거나 별도 처리가 필요합니다.
- **SERVICE_MANAGER는 담당 서비스 구독만 표시.** 담당 범위 밖 구독 URL에 직접 접근해도 404로 응답하며, 내부적으로는 존재하지만 노출되지 않습니다.
- **카드 정보(`card_info`)는 마스킹 표시용**이며 실제 billingKey와 별개입니다. 화면에 표시되는 번호로는 결제가 불가능합니다.
- **결제 이력은 최대 200건**만 표시됩니다(`subscription_detail` L153–155). `paid_count`(성공 결제 횟수)는 별도 집계이므로 200건 이상이어도 정확합니다.
- **엑셀 다운로드**는 페이지네이션을 무시하고 현재 필터의 전체 결과를 가져옵니다. 건수가 매우 많을 경우 다운로드에 시간이 걸릴 수 있습니다.
