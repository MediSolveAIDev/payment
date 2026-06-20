# 17. 서비스 알림(아웃고잉 웹훅) — 요청 016

구독·결제·카드·요금제 상태가 바뀌면 서비스가 등록한 URL로 JSON 알림을 POST한다.

## 1. 개요

- 서비스 상세에서 **알림 URL**을 등록하면(비우면 끔), 아래 이벤트 발생 시 그 URL로 알림을 보낸다.
- **best-effort(fire-and-forget)**: 알림 전송은 백그라운드로 처리되며, 실패해도 결제·구독 등 본 처리에는 영향이 없다(로그만 남김).
- **서명**: 서비스의 기존 HMAC 시크릿으로 서명해 `X-Signature`/`X-Timestamp`/`X-Nonce` 헤더로 보낸다(수신 측이 진위 검증 가능).

## 2. 발송 이벤트(EVENT)

| 상황 | EVENT |
|------|-------|
| 새로운 구독자 발생 | `subscription.created` |
| 구독 상태 변화(취소·재개·미수·정지·만료·수동결제복구) | `subscription.status_changed` |
| 구독 자동결제 발생 | `subscription.renewed` |
| 관리자 강제 구독취소 | `subscription.force_canceled` |
| 만료일 연장 | `subscription.extended` |
| 사용자 카드 등록 / 변경 / 삭제 | `card.registered` / `card.replaced` / `card.deleted` |
| 관리자 카드 활성화 / 비활성화 | `card.activated` / `card.deactivated` |
| 사용자 일반결제 | `payment.one_off` |
| 사용자 일반결제 취소 | `payment.one_off_canceled` |
| 관리자 일반결제 취소(전액/부분) | `payment.one_off_admin_canceled` |
| 요금제 활성화 / 비활성화 / 삭제 | `plan.activated` / `plan.archived` / `plan.deleted` |
| 요금제 사용일 추가 | `plan.bonus_days` |
| **테스트 알림(어드민 버튼)** | `notification.test` |

상수는 `app/notifications/service_notify.py`에 정의되어 있다.

### 테스트 알림 전송(어드민 버튼)

서비스 상세 ‘서비스 알림 URL’ 카드의 **‘테스트 알림 전송’** 버튼은 저장된 URL로 샘플
알림(`EVENT=notification.test`)을 **동기**로 보내고 수신 측 응답(HTTP 코드/오류)을 토스트로
보여준다(`POST /admin/services/{id}/notification-test` → `notifier.send_test`). 일반 발송과
달리 백그라운드가 아니라 즉시 전송해 설정이 올바른지 바로 확인할 수 있다.

## 3. payload 구조

```json
{
  "EVENT": "payment.one_off",
  "subscribe_id": "",        // 구독 ID(구독 이벤트만)
  "order_id": "...",         // 결제 주문번호(결제 이벤트만)
  "PRE_STATUS": "",          // 이전 상태(상태 변화 시)
  "STATUS": "DONE",          // 새 상태
  "service_name": "...",
  "email": "...",            // 관련 사용자(external_user_id)
  "date": "YYYY-MM-DD HH:MM:SS",  // KST
  "DESC": "금액 등 상세 설명"
}
```
없는 값은 빈 문자열이다. 요금제 이벤트는 사용자 비귀속이라 `subscribe_id`/`order_id`/`email`이 빈값이고 `DESC`에 요금제명·상세를 담는다.

## 4. 서명 검증(수신 측)

```
canonical = "POST\n{path}\n{X-Timestamp}\n{X-Nonce}\n{sha256_hex(body)}"
X-Signature == HMAC_SHA256(service_hmac_secret, canonical)
```
- `path`는 알림 URL의 경로 부분(예: `https://svc/hooks/notify` → `/hooks/notify`).
- API 호출 서명(`sign_request`)과 **완전히 동일한 방식**이다. (`app/core/security.py`)

## 5. 구현

| 구성 | 위치 |
|------|------|
| 발송기(Protocol·Http·Recording) | `app/notifications/service_notify.py` |
| 모델 컬럼 | `services.notification_url`(nullable, migration `d3e4f5a6b7c8`) |
| 주입 | `app.state.notifier`(main.py), `get_notifier` dep, `process_due(notifier=...)` |
| 발송 지점 | 각 서비스 함수가 `notifier=...`를 받아 커밋 후 발송(서비스 함수는 순수 유지, 라우트/스케줄러가 notifier 전달) |
| 어드민 UI | 서비스 상세 ‘서비스 알림 URL’ 카드 + ‘상세’ 모달(`services/_notify_help_modal.html`), 저장 `POST /admin/services/{id}/notification-url` |

- 발송기는 `HttpServiceNotifier`(실 전송, 백그라운드 POST). 테스트는 `RecordingServiceNotifier`로 발송 내역을 검사한다.
- best-effort: `HttpServiceNotifier.send`는 payload 구성·서명·스케줄 전 구간을 try로 감싸 어떤 예외도 본 처리를 막지 않는다.

## 6. 수신 데모(sample_service)

샘플 서비스에 수신 엔드포인트와 화면이 있다.
- `POST /notify` — HMAC 서명 검증 후 `NotificationRecord` 저장(`shop/views.py:notify_receive_view`).
- `/notifications` — 받은 알림 목록 + **등록용 수신 URL 안내(복사 버튼)**.
- 등록: `/notifications`의 수신 URL을 복사해 결제 서버 어드민 → 서비스 상세 → **서비스 알림 URL**에 붙여넣고, ‘테스트 알림 전송’으로 연결을 확인한다.

> **샘플은 결제 서버와 별도 docker다(다른 compose/네트워크).** 결제 서버 app 컨테이너에서
> 샘플(호스트 8001 공개)에 닿으려면 **`http://host.docker.internal:8001/notify`**를 등록해야 한다.
> `localhost:8001`(app 컨테이너 자기 자신)·`sample:8000`(다른 네트워크)은 닿지 않는다.
> 또한 Django `ALLOWED_HOSTS`에 `host.docker.internal`이 있어야 한다(없으면 400 DisallowedHost로 거부 — 샘플 `config/settings.py`에 추가됨).

## 7. 비범위

- 재시도·아웃박스 없음(best-effort). 알림 발송 이력을 결제 서버 DB에 저장하지 않는다(감사로그/로그로 충분).
- 요금제 생성/수정 알림은 제외(요청에 없음).
