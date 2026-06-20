# 서비스 알림(아웃고잉 웹훅) — 요청 016 설계

- 날짜: 2026-06-20
- 상태: 승인됨(전송방식·EVENT필드·서명·sample데모·추가이벤트 확정)

## 목표

구독/결제/카드/요금제 상태 변화 시 서비스가 등록한 알림 URL로 JSON을 POST한다.

## 확정 결정 (사용자)

- 전송: **fire-and-forget best-effort**(실패해도 본 처리 영향 없음, 로그 기록).
- payload에 **`EVENT` 필드 추가**(요청 구조 + 식별자).
- **HMAC 서명 포함** — 서비스의 **기존 hmac 시크릿 재사용**(헤더 `X-Signature/X-Timestamp/X-Nonce`).
- **sample_service에 수신 데모**(엔드포인트 + 목록 화면) 추가.

## 데이터 모델

- `services.notification_url: str | None`(String(512), nullable) 추가 + 알렘빅 마이그레이션. 비어 있으면 미발송.
- 서명 시크릿은 기존 `hmac_secret_encrypted` 재사용(새 컬럼 없음).

## 발송기 (`app/notifications/service_notify.py`)

- `ServiceNotifier` 프로토콜: `async send(service, cipher, *, event, subscribe_id="", order_id="", pre_status="", status="", email="", desc="") -> None`.
- `HttpServiceNotifier`: `notification_url` 없으면 no-op. payload 구성 → JSON 직렬화 → 기존 `sign_request("POST", path, ts, nonce, body)`로 서명 → `asyncio.create_task`로 비동기 POST(짧은 타임아웃). 예외는 모두 잡아 로그(best-effort). 백그라운드 태스크 참조 보관(GC 방지).
- `RecordingServiceNotifier`: 테스트용 — `sent` 리스트에 기록(URL 없으면 미기록).
- 주입: `app.state.notifier`(main.py lifespan), 라우트 `get_notifier` dep, 스케줄러 `process_due(..., notifier=...)`.

### payload
```json
{"EVENT":"payment.one_off","subscribe_id":"","order_id":"oo-1","PRE_STATUS":"","STATUS":"DONE",
 "service_name":"My Svc","email":"u@x.com","date":"2026-06-20 14:30:00","DESC":"일반결제 10,000원"}
```
date는 KST `YYYY-MM-DD HH:MM:SS`. 없는 값은 빈 문자열.

## 이벤트 훅 (엣지·스케줄러에서 발송 — 서비스 함수는 순수 유지)

| EVENT | 발송 위치 | 비고 |
|-------|-----------|------|
| `subscription.created` | api/v1 구독 생성 후 | STATUS=TRIAL/ACTIVE, email, subscribe_id |
| `subscription.status_changed` | 취소(api)·재개(api)·수동결제복구(admin) + 스케줄러(PAST_DUE/SUSPENDED/EXPIRED) | PRE/STATUS |
| `subscription.renewed` | renewals `_renew_one` 성공 | order_id, STATUS=ACTIVE, DESC=금액 |
| `subscription.force_canceled` | admin 강제취소 라우트 | subscribe_id, email |
| `subscription.extended` | admin 만료일 연장 라우트 | DESC=새 만료일 |
| `card.registered`/`card.replaced` | api/v1 cards 등록 | email |
| `card.deleted` | api/v1 cards 삭제 | email |
| `card.activated`/`card.deactivated` | admin 카드 토글 | email |
| `payment.one_off` | api/v1 payments | order_id, STATUS=DONE, DESC=금액 |
| `payment.one_off_canceled` | api/v1 cancel | DESC=환불액 |
| `payment.one_off_admin_canceled` | admin 취소(부분/전액) | DESC=환불액/잔여 |
| `plan.activated`/`plan.archived`/`plan.deleted` | admin 요금제 라우트 | email 빈값, DESC=요금제명 |
| `plan.bonus_days` | admin 사용일추가 라우트 | DESC=요금제명·추가일수·적용 구독수 |

## 어드민 UI (서비스 상세)

- 취소정책 카드 옆에 **"서비스 알림" 카드**: 알림 URL 입력 + 저장(`POST /admin/services/{id}/notification-url`). 빈값 저장 시 알림 끔. 감사로그 `service.notification_url_updated`.
- **"상세" 버튼 → 모달**: 16개 이벤트와 설명·발송시점·payload 예시 안내(정적 모달, JS 토글).

## sample_service 수신 데모

- `POST /notify`: 본문 + HMAC 헤더 검증(기존 서비스 시크릿) → `NotificationRecord`(event, payload JSON, received_at) 저장 → 200.
- `/notifications` 화면: 받은 알림 목록(EVENT·STATUS·email·DESC·시각). 내비 추가, 개발자 노트.
- 등록 안내: 어드민 서비스 상세에 sample의 `/notify` URL 입력(매뉴얼).

## 테스트

- 결제서버: 각 이벤트가 `RecordingServiceNotifier`로 발송되는지(EVENT·필드), URL 미등록 시 미발송, 서명 헤더 생성, 수신 실패해도 본 처리 성공(best-effort).
- sample_service: `/notify` HMAC 검증·기록, 잘못된 서명 거부.

## 문서/마이그레이션

- dev_manual: `17-service-notifications.md`(신규) + `admin/03-services.md`(알림 카드) + sample README. HTML 재빌드. 워크로그.
- 배포 시 `alembic upgrade head` 필요.

## 비범위 / 단순화

- 재시도·아웃박스 없음(best-effort). 알림 발송 이력 DB 저장 없음(감사로그/로그로 충분).
- 요금제 생성/수정(`plan.create`/`plan.update`)은 요청에 없으므로 알림 제외.
