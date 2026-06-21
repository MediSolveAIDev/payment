# sample_service 개발자 노트 최신화 워크로그

작성일: 2026-06-21
요청: `sample_service` 각 페이지의 "개발자 노트"(결제서버 API 소개·사용법)를 현재 API에 맞게 최신화.

## 점검 결과 — 대부분 이미 최신
12개 템플릿(`sample_service/shop/templates/shop/*.html`)의 개발자 노트를 현재 API와 전수 대조했다. 대조 기준: `docs/user_manual/11-service-api.md`(검증된 레퍼런스) + `app/api/v1/*.py`(라우트) + `app/schemas/api.py`(요청/응답 필드) + `sample_service/shop/payment_client.py`(실제 호출).

- **card / subscribe / plans / services / oneoff / history / my / result / fail / login / base — 모두 OK(최신).**
  - Card Vault 전환 반영 완료: 구독·단건결제 본문에서 `auth_key`/`customer_key` 제거(카드 선등록), `auth_key`/`customer_key`는 카드 등록(`POST /api/v1/cards`)에만 유지.
  - change-card 엔드포인트 제거, 사용 중 카드 삭제 409, 카드 미등록 404, 멱등 `order_id`, `CANCEL_DISABLED`, `503 PAYMENT_UNRESOLVED`(결과 불명=실패 아님) 등 현행과 일치.
  - HMAC 헤더(`x-service-key/x-timestamp/x-nonce/x-signature`)·서명식 일치.

## 보강 — notifications.html (가장 최근 기능: 서비스 알림)
`notifications.html`의 개발자 노트는 정확했지만 최근 확장된 **수신 규약**이 빠져 있어 보강(소스 대조: `app/notifications/service_notify.py`):

- **헤더 `X-Event`** 추가(서버가 이벤트명을 헤더로도 보냄, `service_notify.py:104`).
- **전달 규약(중요)** 신설: 재시도 없음(at-most-once, 단발 POST) → 수신 실패 시 영구 유실 → 중요 상태는 조회 API로 보완 / 2xx 빠른 응답·본문 무시·발송 타임아웃 5초 / 멱등키(`order_id`, `subscribe_id+EVENT+STATUS+date`, `X-Nonce`는 중복키 금지).
- **서명 거부 운영 주의**: 불일치 시 거부해야 함(샘플은 데모라 기록만).
- **payload 키 & 이벤트 목록**: `EVENT/STATUS/PRE_STATUS/DESC/service_name/date/subscribe_id/order_id/email` + 함정(`subscribe_id`≠subscription_id, `email`=external_user_id) + 이벤트 16종+테스트 카테고리.

## 동기화
- 추가한 수신 규약은 이미 `docs/user_manual/15-feature-notifications.md`(15.7) 및 `docs/dev_manual/17-service-notifications.md`와 일치 → docs/manual 별도 변경 불필요.
- 코어 코드(app/) 변경 없음 — sample_service 템플릿 텍스트만 수정.

## 비고
- HTML 텍스트 보강만이라 Django 템플릿 태그 변경 없음(렌더 안전).
