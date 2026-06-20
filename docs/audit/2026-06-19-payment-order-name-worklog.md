# 2026-06-19 워크로그 — 결제정보에 상품명(order_name) 표시

## 요청
일반(단건)결제 할 때 **상품명도 결제정보에 출력**해 달라.

## 배경
- 기존: 상품명(토스 `orderName`)은 결제 시 토스로 전달만 되고 **DB에 보관되지 않아** 결제 상세에 표시할 수 없었다.
- 해결: Payment에 `order_name` 컬럼을 추가해 영구 보관하고 결제 상세에 표시.

## 변경 내용
1. **모델** `app/models/payment.py`
   - `order_name: Mapped[str | None] = mapped_column(String(255), nullable=True)` 추가(주석 포함).
2. **저장 지점**(토스로 보내는 orderName과 동일 값 저장)
   - `app/services/payments.py` 단건결제: `order_name=order_name`(클라이언트 전달값).
   - `app/services/subscriptions.py` FIRST·manual(RETRY): `order_name=plan.name`.
   - `app/services/renewals.py` RENEWAL/RETRY: `order_name=plan.name`.
3. **마이그레이션** `alembic/versions/f2a3b4c5d6e7_payment_order_name.py`
   - `payments.order_name` 컬럼 추가(nullable). down_revision = `e1f2a3b4c5d6`(직전 head).
4. **화면** `app/admin/templates/payments/detail.html`
   - "결제정보" 표 주문번호 아래에 **상품명** 행 추가(`payment.order_name or '-'`).
5. **문서(docs-sync)**
   - `docs/dev_manual/02-database.md` payments 컬럼표에 `order_name` 추가.
   - `docs/dev_manual/admin/06-payments.md` 결제 상세 필드표에 상품명 추가.
   - `docs/dev_manual/07-one-off-payment.md` order_name 설명에 "저장·표시" 명시.
   - `docs/manual/04-payments.html` 결제 상세 정보에 상품명 항목 추가.
   - `docs/dev_manual/build_html.py` 재빌드(28문서 → HTML, docs/manual/dev_manual 사본 동기화).

## 설계 결정
- **단건만이 아니라 모든 결제에 저장**: order_name 값이 모든 생성 지점에서 이미 계산되므로(구독=plan.name), 컬럼을 비워두지 않고 일관되게 채웠다. 결제 상세가 구독 결제에서도 상품명을 보여준다.
- **컬럼 길이 String(255)**: 토스 orderName은 최대 100자(스키마 `max_length=100`)지만, 향후 소스 다양화를 대비해 여유.
- **nullable**: 과거 결제 행은 값이 없으므로 NULL 허용(상세에서는 `-` 표시).
- **목록(list.html)은 미변경**: 요청 범위는 "결제정보"(상세)이고 목록은 이미 열이 많아 제외.

## 검증
- 모델: `order_name` VARCHAR(255), nullable 인식 확인.
- alembic: 단일 head `f2a3b4c5d6e7`. dev DB에 upgrade→downgrade→upgrade 왕복 성공, `\d payments`로 컬럼 확인.
- 테스트: 결제/구독/갱신/취소 73개 통과 → **전체 548개 통과**.
