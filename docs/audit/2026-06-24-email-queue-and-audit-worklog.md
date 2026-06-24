# 이메일 메모리 큐(순차 발송) + 전송 감사로그 + 재발송 UI 변경 워크로그

- 날짜: 2026-06-24
- 작업자: seungjinhan
- 요청:
  1. 이메일 전송은 **메모리에 우선 담고 순서대로** 보낼 것.
  2. **비밀번호 재발송 UI 변경**.
  3. **감사 로그에 이메일 전송 관련 로그도 반드시** 남길 것.

## 설계 결정(사용자 확인)
- 큐 적용 범위: **모든 이메일**(계정 설정·비밀번호 재설정·관리자 알림)을 하나의 메모리 큐로 통합.
- 재발송 UI: **대기열 등록 토스트** — 전체화면 차단 오버레이 제거, 클릭 즉시 큐 적재 후 "발송을 요청했습니다(대기열)" 토스트.

## 변경 사항

### 1) 메모리 이메일 큐 + 순차 워커 + 감사로그
- `app/notifications/email_queue.py` (신규)
  - `EmailQueue` — `asyncio.Queue`(무제한) + **단일 워커**가 FIFO로 한 건씩 발송(동시성 없음 → 순서 보장).
    발송 직후 **감사 로그** 기록: 성공 `email.sent` / 실패 `email.failed`
    (`actor_type=SYSTEM`, `target_type=email`, `target_id=<수신>`, `detail={to,subject,ok}`).
    워커는 새 DB 세션(session_factory)으로 감사 기록, 어떤 예외에도 멈추지 않음.
    `stop()`은 센티넬로 적재분을 비우고 종료(타임아웃 시 취소).
  - `QueuedEmailSender` — `EmailSender` 어댑터. `send()`가 실제 발송 대신 큐 적재 후 즉시 True 반환.
- `app/main.py` — lifespan에서 운영은 `EmailQueue(실발송기, session_factory)` 기동 →
  `app.state.email_sender = QueuedEmailSender(...)`. 테스트가 sender를 주입하면 큐 미사용(직접).
  shutdown에서 `email_queue.stop()`.
- `app/notifications/admin_notify.py` — `EmailAdminNotifier`의 fire-and-forget(create_task) 제거.
  `send()`가 큐 적재로 즉시 반환하므로 수신자별로 그냥 `await`(순서대로 적재).

### 2) 비밀번호 재발송 UI → 대기열 토스트
- `app/static/admin.css`·`admin.js` — 직전 추가했던 전체화면 차단 오버레이(`#loading-block`,
  `showLoadingBlock`, `data-loading-overlay`) 전부 제거.
- `app/admin/templates/users/detail.html` — 재발송 폼의 오버레이 속성 제거(일반 POST → 즉시 토스트).
- `app/admin/routes/users.py` — flash 문구를 큐 의미로 변경:
  "계정 설정 메일 발송을 요청했습니다(대기열)", "비밀번호 재설정 메일 발송을 요청했습니다(대기열)".

## 문서/매뉴얼
- `docs/user_manual/17-feature-notifications.md` — §17.10 발송 성질을 큐 기반으로 수정,
  §17.11 "이메일 발송 큐(메모리·순차)·감사 로그" 신설.
- `docs/user_manual/07-admin-accounts.md` — §7.8 재발송 단계 문구를 대기열·순차·감사로그로 갱신.
- `build.py`로 HTML 재빌드.

## 검증
- 단위 `tests/unit/test_email_queue.py`(3) — FIFO 순서 발송, 발송별 `email.sent`/`email.failed` 감사 기록,
  `QueuedEmailSender` 적재·즉시 True·html 전달.
- 통합 `tests/integration/test_email_queue_db.py`(1) — 실 DB에 `email.sent` 감사 행 영속 + 순서.
- `tests/unit/test_admin_notify.py` — fire-and-forget 제거 반영(`_drain` 삭제, 직접 await).
- `tests/integration/test_admin_notify_flows.py` — `_tasks` 참조 제거.
- `tests/e2e/test_email_flash.py` — 재발송/계정 flash 문구를 "(대기열)"로 갱신.
- **전체 스위트 660 passed**(Postgres 5432 + 임시 Redis 6380).
- `admin.js` `node --check` 통과, 오버레이 잔존 참조 0건 확인.

## 비고
- 정적 파일/템플릿 변경은 실행 중 컨테이너 재배포 후 화면 반영.
