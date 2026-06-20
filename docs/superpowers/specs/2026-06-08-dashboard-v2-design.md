# 대시보드 재구성 v2 + 결제이력 필터 순서 + 서비스 상세 일반결제 (요청 010)

날짜: 2026-06-08
상태: 승인됨
요청: docs/requests/010.md

## 결정 사항

- 매출/구독 지표는 **이번 달 기준**(추세는 12개월/30일 차트). 서비스별 표는 SYSTEM_ADMIN 전용, 스코프 유지.
- **환불금액 = `Payment.status == CANCELED` 결제 금액 합, `requested_at` 기준**(별도 환불 시각 컬럼 없음 — 근사). 모델 변경 없음.
- **구독만료수 = 감사로그 `subscription.expired` 건수**(취소수가 `subscription.cancel`/`force_cancel` 감사 기반인 것과 동일 패턴). 스코프는 target 구독의 service_id.
- 12개월 차트: **구독수 2시리즈(전체구독 월말 스냅샷 / 신규구독)** + **일반매출 area** (수·금액 혼합이라 2그래프 분리).
- 30일 추이: **일별** — 전체구독수(일말 스냅샷) + 신규/취소/만료 건수.
- 구독 카드 6개: 전체구독·신규구독·구독취소(합)·미결제 / 사용자취소·구독만료.
- 마이그레이션 없음.

## 1. dashboard.py 집계 (서비스 계층)

### DashboardData 필드(교체/추가)
```python
@dataclass
class DashboardData:
    # 매출 섹션
    revenue_cards: list[StatCard]      # 총매출/구독매출/일반매출/환불금액
    service_revenue: list[dict]        # [{id,name,total,sub,one_off,refund}] admin 전용
    # 12개월 차트
    subs_months: list[dict]            # [{label, total, new}]  전체구독수/신규구독수
    one_off_months: list[dict]         # [{label, value}]       일반매출
    # 구독 섹션
    sub_cards: list[StatCard]          # 6개(전체/신규/취소/미결제/사용자취소/만료)
    status_breakdown: list[dict]       # 도넛(기존)
    daily_trend: list[dict]            # [{label, total, new, canceled, expired}] 최근 30일
    service_subs: list[dict]           # [{id,name,open,new,canceled,expired,revenue}] admin 전용
    # 우측 레일(기존 유지)
    recent, past_due, expiring
```

### 신규/변경 헬퍼
- `_revenue_between(db, scope, start, end, *, kind=None)` — 기존 유지(DONE·approved_at).
- `_refund_between(db, scope, start, end, *, kind=None)` — `status==CANCELED`, `requested_at` 범위, `Payment.service_id` 스코프, 금액 합.
- `_expired_count(db, scope, start, end)` — 감사 `subscription.expired` 건수(취소수 `_cancel_counts`와 동일하게 target 구독 service_id로 스코프).
- 월별 신규/취소/만료 건수: 기존 `_count`(created), `_cancel_counts`, 새 `_expired_count`.
- `_daily_trend(db, scope, now)` — 최근 30일 각 일에 대해 전체구독 스냅샷(`created_at<=일말 AND _open_subs_cond(일말)`), 신규/취소/만료 건수.
- `_service_revenue(db, now, month_start)` — admin: 서비스별 이번달 총/구독/일반 매출(case 합) + 환불.
- `_service_subs(db, scope?, now, month_start)` — admin: 서비스별 현재구독수(_open_subs_cond) + 이번달 신규/취소/만료/구독매출.

### 카드 구성
- `revenue_cards`: 총매출(`_revenue_between` 전체), 구독매출(kind=SUBSCRIPTION), 일반매출(kind=ONE_OFF), 환불금액(`_refund_between`). href는 결제리스트 해당 필터.
- `sub_cards`: 전체구독(open), 신규구독(this month), 구독취소(uc+pe 합), 미결제(FAILED 건수), 사용자취소(uc), 구독만료(expired). (기존 ARPU/체험/성공률 카드는 제거 — 요청 구조에 없음.)

> 기존 `_month_cards`(8카드)·`_grand_totals`·`_service_totals`·`revenue_months`/`trend_months`는
> 위 구조로 대체/재배치. `_series_12m`은 `subs_months`(total/new) + `one_off_months`로 재작성.

## 2. 화면 (dashboard.html) — 3섹션

```
[매출]   카드4(총/구독/일반/환불)  +  서비스별 매출표(admin)
[차트]   12개월 구독수(bars: 전체/신규)  +  12개월 일반매출(area)
[구독]   카드6  +  상태 도넛  +  30일 일별 추이  +  서비스별 구독정보표(admin)
[레일]   최근결제 / 미수 / 만료임박 (기존 유지)
```
- 카드 그리드는 기존 `.stats` 재사용. 표는 기존 table 스타일. 차트는 `_charts.html` 매크로:
  - 구독수 2시리즈 → `bars(series, label_a='전체구독', label_b='신규', ...)`(series item에 done=total, failed=new 매핑 또는 매크로 일반화).
  - 일반매출 → `area`.
  - 30일 추이: 전체구독 `area`(daily) + 신규/취소/만료는 작은 bars 또는 숫자 요약(매크로 한계상 area 1개 + 범례 수치). 구현 시 `bars`로 신규/취소/만료 3시리즈가 어려우면 area(전체) + 표/범례로 신규·취소·만료 합계 표기.

## 3. 결제이력 필터 순서 (`/admin/payments`)
- `payments/list.html`의 toolbar extra_selects 순서를 **서비스 → 종류 → 상태**로, date_inputs(기간)는 그 뒤. 라우트 `filter_keys`/로직 변경 없음(순서만). 라벨/옵션 동일.

## 4. 서비스 상세 일반결제 (`/admin/services/{id}`)
- 라우트 `services_detail`: 그 서비스의 ONE_OFF 결제 페이지 추가 조회
  (`Payment where service_id==id AND kind==ONE_OFF`, requested_at desc, paginate). htmx 타깃
  `list-svc-oneoff` 추가(기존 plans/subs partial 패턴).
- 템플릿 `services/detail.html`: 구독 표(`_subs_table.html`) 아래 일반결제 표 추가
  (`services/_oneoff_table.html` 신설: 승인시각·사용자·주문번호·금액·상태). 부분 갱신 지원.

## 5. 테스트
- 통합(`test_dashboard.py`): 환불 집계(CANCELED·requested_at), 만료수(감사 기반), revenue_cards 4종,
  subs_months(total/new)·one_off_months, daily_trend 길이 30·스냅샷, service_revenue/service_subs(admin),
  스코프(매니저는 서비스별 표 없음).
- e2e(`test_dashboard_page.py`): 3섹션 헤더/카드 라벨/표 헤더 렌더, 매니저 스코프.
- e2e: 결제이력 필터 순서(서비스 select가 종류/상태보다 먼저), 서비스 상세 일반결제 표 + 데이터.

## 변경하지 않는 것
- 결제/구독 도메인 로직, 외부 API, 감사 기록 방식, 모델/마이그레이션.
- 우측 레일(최근결제/미수/만료임박), 도넛.
