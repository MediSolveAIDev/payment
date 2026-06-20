# 서비스 화면 개선 (요청 004) — 설계

날짜: 2026-06-07
상태: 승인됨
요청: docs/requests/004.md

## 목표

1. 요금제 추가/수정 폼에 금액정보(첫 결제액 / 다음 회차부터 결제액) 실시간 표시
2. 요금제 리스트 2곳에 첫 결제액 컬럼 추가
3. 허용 IP를 라인단위로 입력
4. 서비스 상세 개요 영역에 키/상태관리·관리자 할당·허용IP 배치
5. 서비스 상세 하단에 해당 서비스의 구독 리스트(필터+페이징)

## 1. 요금제 폼 — 실시간 금액 미리보기

- `app/admin/templates/plans/form.html` 저장 버튼 위에 금액정보 박스:
  - "첫 결제 금액: N원 · 다음 회차부터: M원"
- 인라인 `<script>`가 `price`, `first_payment_type/value`, `recurring_discount_type/value`
  입력의 `input`/`change` 이벤트마다 재계산.
- 계산식은 `app/services/billing_math.py` 미러 (표시 전용 — 실제 결제액은 항상 서버 계산):
  - 다음 회차 = NONE: price / DISCOUNT_AMOUNT: `max(0, price - v)` /
    DISCOUNT_PERCENT: `price - Math.floor(price * v / 100)`
  - 첫 결제 = 다음 회차 금액에 첫구독 할인 중첩 (FREE → 0)
- price가 비어 있거나 0 이하면 "—" 표시. 퍼센트 값이 0~100 밖이면 "—".
- 천 단위 콤마 포맷.

## 2. 요금제 리스트 — 첫 결제액 컬럼

- 대상: `/admin/plans` 리스트(`plans/list.html`), 서비스 상세 요금제 테이블(`services/detail.html`)
- 라우트에서 표시용 속성 부여 (기존 `recurring_amount` 패턴 동일):
  - `p.first_amount = plan_first_amount(p)`
  - 대상 라우트: `plans_list`(plans.py), `services_detail`(services.py)
- 컬럼 구성: 정가 | **첫 결제액**(신규) | **정기 결제액**(기존 "실제 결제금액" 명칭 변경) | ...
- 첫 결제액 == 정기 결제액이면 muted 스타일(할인 없음), 다르면 강조.

## 3. 허용 IP — 라인단위 입력

- `services/new.html`, `services/detail.html`의 IP `<input>` → `<textarea rows="4">`
  (값 표시는 `allowed_ips | join('\n')`)
- `app/admin/routes/services.py`의 `_parse_ips`: 줄바꿈과 콤마 모두 구분자로 처리
  (기존 콤마 입력 데이터·테스트 호환). 빈 항목 제거, 공백 trim.
- IP 형식 검증은 기존 `registry._validate_ips` 그대로.

## 4. 서비스 상세 레이아웃 재배치

`services/detail.html` 구조 (사용자 선택 레이아웃):

```
┌─ 개요 ──────────────┬─ 허용 IP ─────────────┐
│ 담당자/요금제/구독   │ (라인단위 textarea)   │
│ [비활성화][키재발급]│                       │
│ [삭제]              ├─ 관리자 할당 ─────────┤
│                     │ mgr@x.com    [해제]   │
└─────────────────────┴───────────────────────┘
┌─ 요금제 관리 ───────────────────────────────┐
┌─ 구독 (필터+페이징) ────────────────────────┐
```

- 기존 하단 "키 / 상태 관리" 카드는 개요 카드 안으로 흡수(비활성화/활성화·키 재발급·삭제
  버튼, data-confirm 모달 속성 유지).
- 우측 열에 허용 IP 카드와 관리자 할당 카드를 세로로 배치.

## 5. 서비스 상세 — 구독 리스트

- `services_detail` 라우트(services.py)에 `/admin/subscriptions`와 동일 패턴 추가:
  - `PageParams.from_request(request, sortable=..., default_sort="created_at", filter_keys=("status",))`
  - sortable: `external_user_id`, `status`, `current_period_end`, `next_billing_at`, `created_at`
  - 검색(q): `external_user_id` ilike
  - base: `select(Subscription, Plan).join(Plan).where(Subscription.service_id == service_id)`
    (서비스 컬럼 불필요 — 이미 해당 서비스)
  - `paginate()`로 페이지 구성, 템플릿에 `page`, `pp`, `status_filter` 전달
- 템플릿: `_list.html` 매크로(toolbar/sort_th/pager)를 base path
  `/admin/services/{{ service.id }}`로 재사용. 행 클릭 시 `/admin/subscriptions/{id}` 이동.
- 개요 카드의 구독 건수(sub_count)는 유지(전체 건수).

## 에러 처리

- 기존 `?error=` 쿼리파람 표시 경로 유지. 구독 리스트의 잘못된 sort/filter 값은
  기존 `PageParams` 방어 로직(화이트리스트)이 처리.

## 테스트

- 단위: `_parse_ips` — 줄바꿈, 콤마, 혼합, 공백/빈 줄 케이스
- e2e:
  - `/admin/plans` 와 서비스 상세에 첫 결제액 컬럼 표시(할인 적용 값 검증)
  - IP textarea 라인단위 저장 → DB 반영, 콤마 입력도 동작(호환)
  - 서비스 상세 레이아웃: 키/상태 버튼이 페이지에 존재(동작 회귀 — 비활성화/재발급/삭제 기존 테스트 유지)
  - 서비스 상세 구독 리스트: 해당 서비스 구독만 표시, status 필터, 사용자 검색, 페이징(2페이지), 타 서비스 구독 미표시
  - 요금제 폼: 금액 미리보기 박스 존재(`id` 셀렉터). JS 계산 로직 자체는 서버 billing_math 단위 테스트가 담당

## 변경하지 않는 것

- 결제 금액 계산 주체는 서버(billing_math) — JS는 표시 전용
- htmx 미도입(기존 PRG 폼 유지), `/admin/subscriptions` 전역 리스트 무변경
- Plan/Service 모델·API 스키마 무변경 (표시 전용 속성만 라우트에서 부여)
