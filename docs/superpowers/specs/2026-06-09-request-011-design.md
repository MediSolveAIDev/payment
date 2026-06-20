# 요청 011 설계 — 요금제 필터·정산 분리/상세·결제상세·대시보드 만료/색상

날짜: 2026-06-09
상태: 승인됨
요청: docs/requests/011.md

## 결정 사항
- 요금제 필터: **서비스 연동 드롭다운**(선택 서비스의 요금제만, 미선택 시 스코프 전체). 요금제 선택 시 **구독결제만**(요금제 없는 ONE_OFF는 제외).
- 일반결제 상세: **공용 `/admin/payments/{id}` 페이지**(구독·일반 공통). 결제목록·정산에서 연결.
- 대시보드 도넛: **전체 상태(만료 포함)**. 중앙 합계 = 전체 구독수.
- 종류(구독/일반)·결제유형(FIRST/RENEWAL/RETRY/ONE_OFF)에 **색 배지** 추가, 도넛 만료/취소 색 분리.
- 모델/마이그레이션 없음(Payment에 plan_id 미추가 — 구독 경유 Plan 조인).

## 1. 요금제 필터 (구독·결제·정산)
기존 `plans_list` 패턴 재사용: 서비스 select `onchange="this.form.requestSubmit()"` → 서버가 그 서비스의 요금제명(distinct)으로 `plan_options` 재계산. 필터 키 `plan_name`, 서비스 select 바로 옆.

### 1-1. 구독 목록 (`subscriptions.py` `_build_subscriptions_query` + `subscriptions_list`)
- filter_keys에 `plan_name` 추가. Plan join 이미 있음 → `if pp.filters.get("plan_name"): base = base.where(Plan.name == pp.filters["plan_name"])`.
- `subscriptions_list`: `plan_options` 빌드(스코프 내 distinct `Plan.name`, 선택 서비스면 그 서비스로 제한) → render. 템플릿 toolbar extra_selects에 `('plan_name', plan_options, plan_filter)`를 service_id 다음에.

### 1-2. 결제이력 (`payments_list` `_build_payments_query`)
- filter_keys에 `plan_name`. base에 `outerjoin(Plan, Subscription.plan_id == Plan.id)` 추가.
- `if pp.filters.get("plan_name"): base = base.where(Plan.name == pp.filters["plan_name"])` → ONE_OFF(Plan NULL)는 자동 제외.
- `plan_options` 빌드(서비스 연동) → render. 템플릿 toolbar에 service_id 다음 `plan_name` select 추가(종류·상태 앞).

### 1-3. 정산 (`settlement.py` + `services/settlement.py`)
- `settlement_summary(db, scope, start, end, plan_name=None)`: plan_name이면 `join(Subscription).join(Plan)` + `Plan.name == plan_name` (Payment.service_id 집계는 유지, 구독결제만 포함). 건별 쿼리도 동일 조건.
- 정산 폼(`settlement/index.html`)에 service select 다음 요금제 select 추가(onchange 재요청). `plan_options`/`plan_filter`를 `settlement_view`가 전달. filter_keys에 `plan_name`.

## 2. 정산 분리 금액(횟수) + 일반결제 상세
- `SettlementRow`에 `sub_count`·`one_off_count` 추가(kind별 case count). `settlement_summary` 반환 합계에 구독/일반 건수 포함(또는 행 합산).
- `settlement/index.html` 전체정산금액(`total_amount`) 아래에:
  `구독 정산금액 {sub_total:,}원 ({sub_count}건)` / `일반결제 정산금액 {one_off_total:,}원 ({one_off_count}건)`.
  (rows 합산 또는 summary 확장으로 계산 — 스코프/기간 반영.)
- 서비스별 모드 건별 표: 행이 `(p, sub)`. **상세보기** 셀을 분기:
  - sub 있으면(구독): 기존 `/admin/subscriptions/{sub.id}`
  - sub 없으면(일반): `/admin/payments/{p.id}` (이전엔 `-`였던 자리)

## 3. 공용 결제 상세 `/admin/payments/{id}` (신설)
- 라우트: `app/admin/routes/subscriptions.py`(결제 라우트가 있는 곳)에 `GET /payments/{payment_id}` — `require_any`. Payment 조회 후:
  - 매니저 스코프: `ctx.service_ids`가 있고 `payment.service_id` 미포함이면 `NotFoundError`.
  - 미존재 `NotFoundError`.
  - 연결 구독(`subscription_id`)·서비스 로드.
- 템플릿 `payments/detail.html`: 주문번호·종류(배지)·서비스·사용자·결제유형(배지)·금액·상태(배지)·실패코드/메시지·요청/승인 시각(KST)·토스 결제키·연결 구독(있으면 `/admin/subscriptions/{id}` 링크)·원본응답(raw_response 있으면 `<pre>` 요약). 브레드크럼/뒤로가기.
- 라우트 충돌: `/payments/export.xlsx`(정적)가 `/payments/{payment_id}`(동적)보다 먼저 등록되어야 함. `{payment_id}`는 UUID 타입 → `export.xlsx`는 UUID 파싱 실패로 매칭 안 되지만, 안전하게 정적 라우트를 위에 둔다.
- 결제이력 목록 행(주문번호)도 이 상세로 연결(자연 진입점).

## 4. 대시보드
### 4-1. 구독상태 도넛에 만료 추가 (`dashboard.py` `build_dashboard`)
- `status_breakdown` 집계를 `_open_subs_cond` 제한 없이 **전체 상태 카운트**로: `select(Subscription.status, func.count()).group_by(status)`(스코프 적용). `_STATUS_ORDER` 전체(EXPIRED 포함, count>0인 것).
- 도넛 중앙 합계 = 전체 구독수. `dashboard.html` 도넛 부제 "현재 이용 중(상태별)…" → "전체 상태(상태별 비율) · 이번 달 흐름 · 클릭 시 상세".
- sub_flow(신규/취소/만료/미결제) 옆 리스트 유지.

### 4-2. enum 색상 구분 (`app/static/admin.css` + 템플릿)
- 종류(PaymentKind) 배지: `.badge-SUBSCRIPTION`(예: 인디고/블루 계열), `.badge-ONE_OFF`(예: 청록/민트 계열) 추가. 종류 표시처를 `<span class="badge badge-{{ p.kind }}">{{ '구독' if p.kind=='SUBSCRIPTION' else '일반' }}</span>`로.
- 결제유형(payment_type) 배지: `.badge-FIRST`·`.badge-RENEWAL`·`.badge-RETRY`·`.badge-ONE_OFF`(서로 다른 색) 추가. 유형 표시처를 배지로.
- 도넛 `_STATUS_COLOR`: CANCELED·EXPIRED가 둘 다 회색 계열 → 서로 구별되게 분리(예: CANCELED=중간 회색, EXPIRED=연회색/다른 톤). 6개 상태 색이 모두 구별되게 확인.
- 적용 위치: 결제이력·정산 건별·결제상세·서비스상세 일반결제 표 등 종류/유형이 보이는 모든 표. (엑셀 export는 텍스트 라벨 유지 — 변경 없음.)

## 5. 테스트
- 통합: 구독/결제 plan_name 필터(구독결제만, ONE_OFF 제외), 정산 분리 건수, 도넛 전체상태(만료 포함) 카운트.
- e2e: 세 화면 요금제 select 노출·서비스 연동(선택 서비스 요금제만), 정산 구독/일반 금액·건수 표시, 일반결제 상세보기 버튼·결제상세 페이지(스코프 404), 결제상세 종류/유형 배지, 대시보드 만료 세그먼트.
- 색상: badge 클래스가 종류/유형에 렌더되는지(HTML에 `badge-SUBSCRIPTION`/`badge-ONE_OFF`/`badge-RENEWAL` 등 존재).

## 변경하지 않는 것
- 도메인/모델/마이그레이션, 외부 API, 엑셀 export 라벨.
