# 매출전표(영수증) 링크 — 서비스단 노출 워크로그

- 날짜: 2026-06-24
- 작업자: seungjinhan
- 요청: 매출전표를 서비스단에서도 클릭해 볼 수 있게 API를 만들고, 샘플 서비스에 매출전표 보기 기능 추가.

## 배경

매출전표(토스 영수증) 링크는 결제 승인 시 토스 응답 원문(`Payment.raw_response`)의
`receipt.url`에 저장된다. 기존에는 **어드민 결제목록에서만** `receipt_url(p)`
헬퍼(`app/admin/__init__.py`)로 링크를 노출했고, 외부 서비스 결제조회 API
(`GET /api/v1/payments/{external_user_id}`)의 응답에는 포함되지 않아 서비스단에서
영수증을 볼 수 없었다.

## 설계 결정

- 별도 엔드포인트를 만들지 않고, 기존 `PaymentResponse`에 `receipt_url` 필드를 추가해
  결제조회 API 응답에 실어 보낸다(취소 수수료 필드 `cancel_*`를 함께 싣는 기존 패턴과 동일).
  `get_payments()`가 raw dict를 그대로 반환하므로 샘플 서비스는 별도 호출 없이 필드를 받는다.
- 매출전표 URL은 토스가 호스팅하는 공개 링크이므로 서비스가 직접 새 탭으로 연다(프록시 불필요).
- 안전 추출 로직을 한 곳으로 통일 — `app/models/payment.py`에 순수함수 `receipt_url_from_raw`와
  `Payment.receipt_url` 프로퍼티를 두고, 어드민 헬퍼도 이를 재사용(기존 3중 중복 제거).

## 변경 파일

- `app/models/payment.py` — `receipt_url_from_raw()` 함수 + `Payment.receipt_url` 프로퍼티 추가.
- `app/admin/__init__.py` — `receipt_url()` 헬퍼를 공용 함수 위임으로 변경(동작·템플릿 글로벌 동일).
- `app/schemas/api.py` — `PaymentResponse`에 `receipt_url` 필드 추가 + `from_model` 반영, docstring 보정.
- `sample_service/shop/views.py` — `history_view()`에서 단건 레코드에 `receipt_url` 부착.
- `sample_service/shop/templates/shop/history.html` — 구독/단건 표에 "매출전표" 열·링크 추가, 개발자 노트 보강.
- `sample_service/shop/payment_client.py` — `get_payments()` docstring에 `receipt_url` 설명 추가.
- `tests/unit/test_receipt_url.py` — 신규: 공용 함수·모델 프로퍼티·`from_model` 노출 검증(5케이스).
- `docs/user_manual/13-service-api.md`·`16-feature-payment.md`·`19-sample-service.md`(+ 대응 `.html`) 갱신, `build.py`로 재빌드.

## 검증

- 단위 테스트: `pytest tests/unit` → 118 passed(신규 5 포함). 기존 어드민 헬퍼 테스트 회귀 없음.
- 통합 테스트는 로컬 Redis/Postgres 미기동으로 연결 에러(환경 의존) — 테스트 로직 자체는 통과
  (`test_partial_cancel_exposed_in_api_response`가 "1 passed"). 매출전표 노출은 단위 테스트로 직접 커버.
- 수동(권장): 샘플 서비스 기동 → `/history`에서 카드결제(DONE) 건의 "매출전표" 링크 클릭 →
  토스 영수증 페이지 열림. 영수증 없는 건은 `-` 표시.
- API: `GET /api/v1/payments/{email}` 응답 JSON에 `receipt_url` 포함 확인(Swagger/curl).

## 후속(매뉴얼 보강)

- `docs/user_manual/19-sample-service.md` — ⑦ 결제 내역 화면에 **매출전표 보기** 설명과
  전용 이미지 영역(`assets/img/sample-07-receipt.png`) 추가, 흐름도 스텝7 라벨에 '매출전표' 표기.
- `docs/user_manual/assets/img/sample-07-receipt.png` — 플레이스홀더 이미지 생성(실제 캡처로 교체 예정),
  `assets/img/README.md` 매핑표에 항목 추가. `build.py`로 재빌드 완료.

## 후속(별개 버그 수정 — 샘플 서비스 연동)

- `sample_service/docker-compose-dev.yml` — `PAYMENT_API_BASE`가 `http://localhost:8000`이라
  컨테이너 내부에서 sample(Django) 자신으로 루프백 → `/api/v1/services`가 Django 404를 반환.
  `http://app:8000`(같은 네트워크의 app 컨테이너 이름)으로 수정 + 경고 주석 추가. 컨테이너 재생성 후
  `payment_client.list_services()` 정상(`[{'name':'SNS',...}]`) 확인.

## 영향/리스크

- 응답에 필드 추가만 하므로 기존 클라이언트 하위호환(미사용 필드 무시).
- 민감정보 노출 아님 — `receipt.url`만 추출하며 `raw_response` 전체는 계속 비노출.
