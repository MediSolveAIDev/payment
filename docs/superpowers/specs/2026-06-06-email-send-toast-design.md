# 이메일 발송 결과 토스트 알림 — 설계

날짜: 2026-06-06
상태: 승인됨

## 목표

관리자 화면에서 메일을 발송하는 버튼을 눌렀을 때, 발송 성공/실패 여부를 토스트 알림으로 표시한다.

적용 대상 (3곳):

1. 서비스 등록 — 서비스 키 안내 메일 (`POST /admin/services`)
2. 계정 생성 — 비밀번호 설정 메일 (`POST /admin/users`)
3. 사용자 상세 — 비밀번호 재설정 메일 (`POST /admin/users/{id}/reset-password`)

## 배경

- 토스트 인프라는 이미 존재: `admin.js`의 `showToast()` + `base.html`의 `body[data-flash]` 속성을 읽어 페이지 로드 시 토스트 표시. 단, 현재 `flash`를 채워주는 라우트가 없어 미사용 상태.
- `GmailEmailSender.send()`는 예외를 로깅만 하고 삼키므로(발송 실패가 결제/계정 흐름을 깨면 안 됨) 호출부가 성공/실패를 알 수 없음.

## 설계

### 1. `EmailSender.send() -> bool`

- `app/notifications/email.py`의 Protocol 및 구현 3종 반환 타입을 `None → bool`로 변경.
- `GmailEmailSender`: 성공 `True`, 예외 시 로깅 후 `False` (예외를 삼키는 동작은 유지 — 스케줄러 등 기존 호출부는 반환값을 무시하므로 영향 없음).
- `ConsoleEmailSender`, `RecordingEmailSender`: 항상 `True`.

### 2. 서비스 계층이 발송 결과를 전달

- `registry.create_service`, `accounts.create_account`, `auth.issue_password_reset`이 메일 발송 결과(`bool`)를 반환값에 포함한다. 기존 반환값이 있는 함수는 튜플 또는 기존 반환 구조에 맞춰 추가.

### 3. admin 라우트 3곳 — flash 쿼리파람으로 리다이렉트

- 기존 `?error=` 쿼리파람 패턴과 동일하게, 발송 결과에 따라:
  - 성공: `?flash=...메일을 발송했습니다` (complete 토스트)
  - 실패: `?flash=메일 발송에 실패했습니다. SMTP 설정을 확인하세요&flash_type=error` (error 토스트)
- 메시지는 URL 인코딩하여 리다이렉트 URL에 부착.

### 4. `render()`가 flash 쿼리파람을 자동 주입

- `app/admin/__init__.py`의 `render()`에서 `request.query_params`의 `flash`/`flash_type`을 읽어 템플릿 컨텍스트에 넣는다 (명시적 kwargs가 있으면 그것이 우선).
- `base.html`의 기존 `data-flash` 렌더링과 `admin.js`의 토스트 표시(2초 자동 사라짐)는 변경 없음.

## 에러 처리

- 메일 발송 실패는 본 작업(서비스 생성/계정 생성/재설정 토큰 발급)을 실패시키지 않는다 — 작업은 성공하고 토스트만 실패를 알린다.
- 본 작업 자체가 실패하는 경우는 기존 `error` 처리 경로 그대로.

## 테스트

- `EmailSender` 구현들의 반환값 검증 (Gmail 실패 시 `False`).
- admin 라우트 3곳: 발송 성공 시 redirect URL에 `flash=` 포함, 실패 시(`send`가 `False`를 반환하는 fake sender 주입) `flash_type=error` 포함.
- `render()` flash 주입: 쿼리파람이 템플릿 컨텍스트로 전달되는지.

## 변경하지 않는 것

- htmx 전환 없음 — 기존 PRG(POST-Redirect-GET) 폼 구조 유지.
- 스케줄러/웹훅 등 백그라운드 메일 발송 경로는 동작 변경 없음 (반환값 무시).
