# 어드민 결제 목록 매출전표 링크 — 설계

- 날짜: 2026-06-23
- 작성자: seungjinhan (oasis@medisolveai.com)
- 상태: 승인 대기

## 목적

어드민 결제 목록의 각 행 옆에 **매출전표(영수증) 링크**를 추가해, 운영자가 해당 결제의 토스 매출전표를 새 탭에서 바로 열람·인쇄할 수 있게 한다.

매출전표 = 신용·체크 카드 결제에 발급되는 영수증(법정 증빙). 토스 Payment 객체의 `receipt.url`이 그 링크다(토스 호스팅 페이지, 인쇄 가능). 참고: https://docs.tosspayments.com/resources/glossary/sales-statement

## 확정 결정

1. **URL 출처**: 승인 시 이미 저장한 `Payment.raw_response["receipt"]["url"]`. **추가 토스 호출·새 엔드포인트·스키마 변경 없음.**
2. **범위**: 결제 **목록**(`payments/list.html`)만. 상세 페이지는 제외(이미 raw_response 원문을 노출).
3. **표시 조건**: receipt URL이 있으면 링크, 없으면 `-`. (실패·대기·과거 receipt 미보유 건은 자연히 `-`)
4. 링크는 새 탭(`target="_blank" rel="noopener"`)으로 토스 매출전표 페이지를 연다.

## 비범위 (YAGNI)

- 클릭 시 토스 조회 API 호출(최신 URL) / 서버 리다이렉트 엔드포인트 — 불필요(저장값으로 충분).
- 상세 페이지 매출전표 버튼, 메일 발송 등.

## 변경 대상

### 1. 헬퍼 — `receipt_url(payment) -> str | None`
- `payment.raw_response`가 dict이고 `receipt.url`이 있으면 그 문자열, 아니면 None. 안전 접근(raw_response None / `receipt` 없음 / `url` 없음 → None).
- 위치: 어드민 결제 관련 모듈(예: `app/admin/routes/payments.py`의 모듈 함수, 또는 기존 결제 표시 헬퍼가 모인 곳). `payment_status_ko`/`payment_error_meaning`이 템플릿 전역으로 등록된 곳과 동일하게 **Jinja 템플릿 전역으로 등록**해 템플릿에서 `receipt_url(p)`로 호출.

### 2. UI — `app/admin/templates/payments/list.html`
- 헤더에 `<th>매출전표</th>` 추가(요청·시각 열 부근, 행 끝).
- 행에 셀 추가:
  ```jinja
  <td class="muted">{% set rurl = receipt_url(p) %}{% if rurl %}<a href="{{ rurl }}" target="_blank" rel="noopener">매출전표</a>{% else %}-{% endif %}</td>
  ```
- 빈 목록 행 `colspan` 9 → 10.

## 테스트

- `receipt_url`: raw_response에 receipt.url 있음→URL, receipt 없음→None, raw_response None→None, url 키 없음→None.
- 목록 렌더(e2e, 기존 `tests/e2e/test_admin_operations.py` 등 결제목록 렌더 패턴 활용): receipt 보유 결제 행에 매출전표 `<a>`가 보이고, 미보유 결제 행엔 `-`가 보인다.

## 문서

- 어드민 콘솔 매뉴얼(결제 목록)에 매출전표 링크 설명 추가 + 재빌드.
- `docs/audit/` 워크로그.

## 주의

- 토스 **테스트 환경**에선 receipt URL은 생성되나 실제 매출전표는 발행되지 않는다(문서 명시) — 링크 동작 자체는 정상.
- `raw_response`는 내부 필드라 외부 API 응답엔 노출하지 않는다(기존 정책 유지). 이번 변경은 **어드민 화면**에서만 사용.
