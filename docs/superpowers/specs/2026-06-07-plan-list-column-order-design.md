# 요금제 리스트 컬럼 순서 변경 — 설계

날짜: 2026-06-07
상태: 승인됨

## 목표

요금제 리스트 2곳의 컬럼을 다음 순서로 통일:
**이름 | 정가 | 체험 | 첫구독 할인 | 첫 결제액 | 상시할인 | 정기 결제액 | 주기(반복회차) | 상태 | (액션)**

## 1. 서비스 상세 요금제 테이블 (`app/admin/templates/services/_plans_table.html`)

- thead/tbody 컬럼을 위 순서로 재배치 — 셀 마크업(title 툴팁, muted/bold 분기, 배지) 변경 없이 이동만.
- 빈 행 colspan 10 유지 (컬럼 수 불변).

## 2. 전역 요금제 리스트 (`app/admin/templates/plans/_table.html`)

- 서비스 컬럼 다음에 동일 순서: **서비스 | 이름 | 정가 | 체험 | 첫구독 할인 | 첫 결제액 | 상시할인 | 정기 결제액 | 주기 | 상태 | (액션)**
- **체험 컬럼 신규** — 상세 테이블과 동일 표기: `{% if plan.trial_enabled %}{{ plan.trial_days }}일{% else %}-{% endif %}`
- **첫구독 할인 한글화** — 기존 `{{ plan.first_payment_type }} {{ plan.first_payment_value }}`(영문 enum) 셀을 상세 테이블과 동일한 한글 분기(없음/무료/N원 할인/N% 할인)로 교체.
- 빈 행 colspan 10 → 11.
- 정렬 가능 컬럼(이름/정가/상태 sort_th)은 위치만 이동, target 인자 유지.

## 테스트

- e2e (`tests/e2e/test_service_detail_page.py`): thead 컬럼 순서 검증 — 상세 테이블에서
  `정가 < 체험 < 첫구독 할인 < 첫 결제액 < 상시할인 < 정기 결제액` index 순서 확인.
- e2e: 전역 리스트에 체험 표기(`14일`)와 한글 첫구독 할인(`1,000원 할인`) 표시 확인.
- 기존 테스트는 문자열 존재만 검사하므로 영향 없음 (htmx partial 테스트 포함).

## 변경하지 않는 것

- 라우트/모델/금액 계산 무변경 — 순수 템플릿 재배치 + 표기 보강.
