# 부분취소가 외부 API/샘플서비스에 반영 안 되던 버그 수정 워크로그

- 날짜: 2026-06-20
- 작업자: seungjinhan

## 증상

관리자가 일반결제를 **부분취소**했는데 sample_service 결제내역에서 해당 상품의 취소금액이 반영되지 않음.

## 원인

어드민 부분취소는 `status=DONE`을 유지하고 `canceled_amount`만 누적한다. 그러나 외부
API 응답(`PaymentResponse.from_model`)은 환불액을 `status == CANCELED`로만 판정해서,
부분취소(DONE) 결제는 `cancel_refund_amount=0`을 반환 → 샘플서비스가 취소금액을 0으로 표시.

## 수정

### 결제 서버 (`app/schemas/api.py`)
- `PaymentResponse.from_model`: 환불액 판정 기준을 `status` → **`canceled_amount>0`**으로 변경.
  부분취소(DONE)·전액취소(CANCELED) 모두 실제 누적 환불액을 노출.
- 응답 필드 추가: `canceled_amount`(실제 누적 환불액), `net_amount`(= amount − canceled_amount).
- `cancelable`은 이미 부분취소된 건(`canceled_amount>0`)이면 false(외부 재취소 불가, 기존 가드 유지).

### 샘플서비스 (`sample_service/shop/`)
- `views.py history_view`: 응답의 `canceled_amount`/`net_amount`/`status`를 레코드에 부착하고
  취소 상태(취소됨/부분취소/완료)를 **서버 기준**으로 산출(로컬 canceled 플래그는 어드민 취소를 모름).
- `history.html`: 단건결제 표에 **실수령액(net_amount)** 컬럼 추가, 환불액은 서버 실제값 표시,
  상태를 서버 기준(부분취소 뱃지 포함)으로 표시, 취소 버튼은 `cancelable`일 때만 노출.

## 검증

- `tests/integration/test_payment_cancel.py`에 `test_partial_cancel_exposed_in_api_response` 추가 —
  부분취소 후 API 응답 `canceled_amount=3000`·`cancel_refund_amount=3000`·`net_amount=7000`·`cancelable=False` 확인.
- `uv run pytest tests/integration/test_payment_cancel.py tests/integration/test_api_endpoints.py` → 32 passed.
- sample 컨테이너 재빌드 + `manage.py check` 무이슈, `/login` 200.

### 문서
- `15-external-api-and-sample.md`(응답 필드·부분취소 주의) 갱신 + HTML 재빌드.
