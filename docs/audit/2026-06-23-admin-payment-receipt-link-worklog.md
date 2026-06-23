# 어드민 결제 목록 매출전표 링크 워크로그

- 날짜: 2026-06-23
- 작업자: seungjinhan (oasis@medisolveai.com)

## 목적

어드민 결제 목록의 각 행에 토스 **매출전표(영수증)** 링크를 추가해, 운영자가 해당 결제의 매출전표를 새 탭에서 바로 열람·인쇄할 수 있게 한다. 매출전표 = 카드결제 영수증(법정 증빙), 토스 Payment 객체의 `receipt.url`. (참고: https://docs.tosspayments.com/resources/glossary/sales-statement)

## 결정

- **URL 출처**: 승인 시 이미 저장한 `Payment.raw_response["receipt"]["url"]`. 추가 토스 호출·새 엔드포인트·스키마 변경 없음.
- **범위**: 결제 목록만(상세 제외).
- **표시 조건**: receipt URL 있으면 링크, 없으면 `-`(실패·대기·과거 미보유 graceful).
- 링크는 새 탭(`target="_blank" rel="noopener"`).

## 변경

- `app/admin/__init__.py` — `receipt_url(payment) -> str|None` 헬퍼 추가(raw_response가 dict이고 `receipt.url`이 비어있지 않은 문자열이면 반환, 아니면 None). Jinja 템플릿 전역 등록(`templates.env.globals["receipt_url"]`, `payment_status_ko`와 동일 방식).
- `app/admin/templates/payments/list.html` — `<th>매출전표</th>` 열 추가, 행에 `{% set rurl = receipt_url(p) %}` 링크/`-` 셀 추가, 빈 목록 `colspan` 9→10.
- `tests/unit/test_admin_helpers.py` — `receipt_url` 단위 테스트 5종(있음/receipt없음/url없음/raw None/비문자열).
- `tests/e2e/test_admin_operations.py` — `test_payments_list_sales_statement_link`: receipt 보유 결제엔 링크, 미보유엔 `-`(링크 anchor 정확히 1개).
- `docs/user_manual/05-admin-payment-refund.md`, `docs/manual/dev_manual/admin/06-payments.md` — 매출전표 열 설명 추가 후 양쪽 매뉴얼 재빌드.

## 검증

- `uv run pytest tests/unit/test_admin_helpers.py` → 17 passed.
- `uv run pytest tests/e2e/test_admin_operations.py` → 신규 포함 통과(결제 목록 렌더 회귀 없음).
- 재빌드 HTML(`05-admin-payment-refund.html`, `admin--06-payments.html`)에 "매출전표" 반영 확인.

## 주의

- 토스 **테스트 환경**에선 receipt URL은 생성되나 실제 매출전표는 발행되지 않는다(문서 명시) — 링크 동작 자체는 정상.
- `raw_response`는 어드민 화면 전용(외부 API 미노출 — 기존 정책 유지).

## 참고 문서

- 설계: docs/superpowers/specs/2026-06-23-admin-payment-receipt-link-design.md
- 계획: docs/superpowers/plans/2026-06-23-admin-payment-receipt-link.md
