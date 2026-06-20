# 서비스 알림(아웃고잉 웹훅) 구현 워크로그 — 요청 016

- 날짜: 2026-06-20
- 작업자: seungjinhan
- 설계: `docs/superpowers/specs/2026-06-20-service-notifications-design.md`
- 요청: `docs/requests/016_서비스에알림.md`

## 요약

구독·결제·카드·요금제 상태 변화 시 서비스가 등록한 URL로 JSON 알림을 POST한다.
fire-and-forget(best-effort) + HMAC 서명(기존 서비스 시크릿 재사용) + EVENT 식별 필드.
sample_service에 수신 데모(`/notify` + `/notifications`) 추가.

## 변경 내용 (결제 서버)

- **모델**: `services.notification_url`(nullable) 추가 — migration `d3e4f5a6b7c8`. 서명은 기존 `hmac_secret_encrypted` 재사용.
- **발송기** `app/notifications/service_notify.py`: `ServiceNotifier`(Protocol) / `HttpServiceNotifier`(서명 + `asyncio.create_task` 백그라운드 POST, 전 구간 best-effort try) / `RecordingServiceNotifier`(테스트) + EVENT 상수 + `build_payload`.
- **주입**: `app.state.notifier`(main.py), `get_notifier` dep(core/api deps), 스케줄러 `process_due(notifier=...)`, conftest `RecordingServiceNotifier` 픽스처.
- **이벤트 발송**(서비스 함수가 `notifier=...`를 받아 커밋 후 발송, 라우트/스케줄러가 전달):
  - 구독: 생성/상태변화(취소·재개·수동결제복구)/자동결제/강제취소/만료일연장 + 스케줄러(PAST_DUE·SUSPENDED·EXPIRED).
  - 카드: 등록/교체/삭제/활성·비활성(`cards.py` 내부 발송 — 등록vs교체 구분 가능).
  - 일반결제: 결제/사용자취소/관리자취소(전액·부분).
  - 요금제: 활성화/비활성화/삭제/사용일추가.
- **어드민 UI**: 서비스 상세 ‘서비스 알림 URL’ 카드(취소정책 옆) + ‘상세’ 모달(`_notify_help_modal.html`) + 저장 라우트 `POST /admin/services/{id}/notification-url`(감사 `service.notification_url_updated`, 라벨 추가).

## 변경 내용 (sample_service)

- `NotificationRecord` 모델 + Django 마이그레이션 `0004`.
- `POST /notify` 수신(HMAC 검증) `notify_receive_view` + `_verify_notify_signature`, `/notifications` 목록 화면 + 내비.

## 테스트

- `tests/integration/test_service_notifications.py`(9): payload 구조·URL 미등록 미발송·이벤트별 디스패치(구독생성/일반결제/카드등록/요금제비활성/관리자취소)·HMAC 서명·스케줄러 자동결제.
- `tests/e2e/test_service_notification_url.py`(3): 알림 카드 노출·URL 저장/끔/형식거부.
- sample `shop/tests.py`: `/notify` 서명 검증 통과/실패 2건. (history 테스트 2건은 직전 부분취소 변경에 맞춰 갱신)

## 검증

- 결제 서버 `uv run pytest` → **602 passed**.
- sample `manage.py test shop` → **80 passed**.
- 마이그레이션 head 체이닝 정상(`d3e4f5a6b7c8`).

## 문서

- `docs/dev_manual/17-service-notifications.md`(신규) + `admin/03-services.md` 갱신, `build_html.py` DOCS에 16·17 추가 후 HTML 재빌드(16-card-vault 누락도 함께 수정).
- sample README ‘받은 알림’ 섹션 추가.

## 배포 주의

- 결제 서버: `alembic upgrade head` 필요(`d3e4f5a6b7c8`). sample: `manage.py migrate`(0004).
- 알림 동작: 어드민 서비스 상세에서 알림 URL 등록 필요(비우면 미발송).
