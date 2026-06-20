# 서비스 알림 — 테스트 전송 버튼 + 샘플 수신 URL 노출 워크로그

- 날짜: 2026-06-20
- 작업자: seungjinhan
- 후속: 요청 016(서비스 알림) 보강

## 요청

1. sample 서버가 받을 수신 URL을 화면에서 제공.
2. 결제 서버 어드민에 **테스트 알림 전송 버튼** 추가(누르면 저장된 URL로 샘플 알림 전송).

## 변경 내용

### 결제 서버
- `app/notifications/service_notify.py`: `EVENT_TEST="notification.test"` + 발송기에 **`send_test(service) -> (ok, detail)`** 추가.
  - `HttpServiceNotifier.send_test`: 서명된 테스트 알림을 **동기 POST**하고 수신 응답(HTTP 코드/네트워크 오류)을 반환.
  - `RecordingServiceNotifier.send_test`: 기록 + (True, "기록됨"), URL 미등록이면 (False, ...).
- `app/admin/routes/services.py`: `POST /admin/services/{id}/notification-test` 라우트 — `send_test` 호출, 성공/실패를 토스트(`?error=`)로 표시.
- `services/detail.html`: 알림 카드에 **‘테스트 알림 전송’ 버튼**(URL 미저장이면 disabled).

### 샘플 서버
- `notifications_view`: 등록용 수신 URL(`request.build_absolute_uri('/notify')`)과 도커 내부 URL(`http://sample:8000/notify`)을 템플릿에 전달.
- `notifications.html`: 상단에 **수신 URL 안내 카드 + 복사 버튼**.

## 테스트

- `tests/integration/test_service_notifications.py`: `send_test` 기록/미등록 실패 2건.
- `tests/e2e/test_service_notification_url.py`: 테스트 전송 라우트(URL 등록 시 성공 발송 / 미등록 시 오류) 2건.
- 샘플 `shop/tests.py`: `/notifications`에 수신 URL 노출 1건.

## 검증

- 결제 서버 `uv run pytest` → **606 passed**.
- 샘플 `manage.py test shop` → **81 passed**.
- 샘플 재빌드 후 `/notifications`에 수신 URL 노출 확인.

### 문서
- `17-service-notifications.md`(notification.test·테스트 버튼·수신 URL), `admin/03-services.md`, sample README 갱신 + HTML 재빌드. 스키마/마이그레이션 변경 없음.
