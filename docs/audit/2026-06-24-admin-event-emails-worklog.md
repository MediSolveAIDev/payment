# 시스템 관리자 이벤트 알림 메일 + 비밀번호 재발송 로딩 처리 워크로그

- 날짜: 2026-06-24
- 작업자: seungjinhan
- 요청:
  1. 계정/서비스/구독 생성 시 시스템 관리자에게 상세 내용을 메일로 전송.
  2. 비밀번호 재설정(재발송) 버튼 클릭 시 메일 전송 완료 전까지 화면을 비활성화하고 진행 상황 표시.

## 1) 관리자 이벤트 알림 메일

### 설계 결정(사용자 확인)
- 수신자: **활성(ACTIVE) SYSTEM_ADMIN 전원**(DB 조회).
- 구독 알림 범위: **모든 구독 생성**(외부 API 포함).
- 본문 형식: **HTML 서식**(상세 표) + 평문 대체 본문.
- 서비스 웹훅(`ServiceNotifier`)과 동일한 패턴의 `AdminNotifier` 추상화로 구현(테스트 페이크·fire-and-forget).

### 변경 파일
- `app/notifications/email.py` — `EmailSender.send(to, subject, body, html=None)`로 확장.
  Gmail 구현은 `html`이 있으면 `set_content`(평문)+`add_alternative(html, subtype="html")` 멀티파트 발송. Console/Recording 반영(하위호환).
- `app/notifications/admin_notify.py` (신규) — `_active_admin_emails`(역할·상태 필터),
  `AdminNotifier` 프로토콜, `EmailAdminNotifier`(수신자 조회→HTML 템플릿→fire-and-forget),
  `RecordingAdminNotifier`(테스트), 3개 이벤트별 상세 HTML/plain 템플릿.
- `app/core/deps.py` — `get_admin_notifier` 의존성.
- `app/main.py` — `app.state.admin_notifier`(기본 `EmailAdminNotifier`) + `create_app(admin_notifier=...)` 주입구.
- 트리거(각 서비스 함수의 **커밋 후** best-effort 호출, 라우트에서 `get_admin_notifier` 주입):
  - `app/services/accounts.py` `create_account` ← `app/admin/routes/users.py`
  - `app/services/registry.py` `register_service` ← `app/admin/routes/services.py`
  - `app/services/subscriptions.py` `create_subscription`(웹훅 알림 직후) ← `app/api/v1/subscriptions.py`

### 메일 내용
- 계정: 이메일·역할(한글)·담당 서비스·상태·생성자·생성시각(KST)
- 서비스: 서비스명·ID·대표 담당자·담당자 전체·허용 IP·취소 정책·토스키 설정여부·생성자·생성시각
- 구독: 서비스명·요금제·구독자(이메일)·상태·첫 구독·체험·청구 금액·주문번호·구독 기간·다음 결제일·생성시각

### 성질
- 전 구간 best-effort: 본 처리가 커밋된 뒤 호출되므로 메일 실패가 트랜잭션을 깨지 않음.
- 수신자 조회만 호출자 DB 세션으로 즉시 수행, 실제 SMTP는 백그라운드(응답 지연 방지).

## 2) 비밀번호 재발송 진행중 화면 비활성화

- `app/admin/templates/users/detail.html` — 재설정 메일 폼에
  `data-loading data-loading-overlay data-loading-text="비밀번호 재설정 메일 전송 중…"` 부여.
- `app/static/admin.js` — 기존 `data-loading` 처리에 **전체 화면 차단 오버레이**(`#loading-block`) 추가
  (`showLoadingBlock`/`hideLoadingBlock`, `data-loading-overlay` 폼만 적용). bfcache 복원 시 해제.
- `app/static/admin.css` — `#loading-block` 오버레이 + 스피너 스타일.
- 동작: 클릭 → 화면 전체 비활성화 + "전송 중…" 표시 → 메일 발송 완료 후 303 리다이렉트로 화면 자동 복귀(중복 제출 차단). 재사용 가능한 일반 메커니즘.

## 문서/매뉴얼
- `docs/user_manual/17-feature-notifications.md` — §17.10 관리자 이벤트 알림 메일 신설.
- `docs/user_manual/07-admin-accounts.md` — §7.8 재발송 단계에 화면 비활성화 안내 추가.
- `build.py`로 HTML 재빌드(20개 문서).

## 검증
- 단위 테스트 `tests/unit/test_admin_notify.py`(8) — EmailSender HTML 멀티파트, `_render` 이스케이프,
  `EmailAdminNotifier` 디스패치(수신자 monkeypatch), RecordingAdminNotifier.
- 통합 테스트 `tests/integration/test_admin_notify_flows.py`(5) — `_active_admin_emails` 필터,
  EmailAdminNotifier 실 발송(실 DB 수신자), 3개 흐름 트리거(RecordingAdminNotifier).
- 기존 `tests/integration/test_audit.py::test_recording_email_sender`의 정확 dict 단언에 `html` 키 반영.
- **전체 스위트 656 passed**(Postgres 5432 + 임시 Redis 6380으로 통합 포함 실행). admin.js `node --check` 통과.
- 비밀번호 재발송 UI는 코드 검증(구문·기존 data-loading 확장) 완료 — 실제 화면 확인은 앱 재배포 후 권장.
