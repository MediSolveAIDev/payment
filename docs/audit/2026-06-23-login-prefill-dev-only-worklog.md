# 2026-06-23 — 로그인 자동입력을 로컬 개발(dev)에서만 노출하도록 제한

## 배경 / 요청
- 스테이징(stg) 모드 로그인 화면에 아이디·비밀번호가 자동 입력되어 노출되는 문제.
- 기존 조건이 `environment != "prod"` 라서 **stg도 dev로 취급**되어 자격증명이 화면에 채워졌다.

## 원인
- `app/admin/routes/auth.py` 의 `login_page()` 에서 `dev = settings.environment != "prod"` 로 판정.
- stg(`environment == "stg"`)에서 `dev=True` → `dev_login_email` / `dev_login_password` 가 폼에 prefill.

## 변경
- `app/admin/routes/auth.py:login_page` — 판정 조건을 `settings.environment == "dev"` 로 변경(로컬 개발에서만 자동입력). docstring도 갱신.
- `app/core/config.py` — `dev_login_email` / `dev_login_password` 주석을 "dev에서만 노출, stg·prod 제외"로 수정.

## 문서 동기화
- `docs/manual/dev_manual/admin/01-login-and-access.md` — 개발 모드 배너·이메일 입력·4-6 자동입력 절을 `environment == "dev"` 기준으로 수정.
- `docs/manual/dev_manual/build_html.py` 재실행 → `admin--01-login-and-access.html` 등 30개 페이지 재빌드(반영 확인).
- `docs/manual/00-setup.html` — `DEV_LOGIN_EMAIL`·`DEV_LOGIN_PASSWORD` 설명을 "dev에서만 적용, stg·prod 무시"로 수정(정식 매뉴얼, md 소스 없는 직접 HTML).

## 검증
- 전체에서 자동입력 맥락의 `environment != "prod"` 잔재 0건(쿠키 `secure` 판정은 별개라 유지).
- 재빌드 HTML에 `environment == "dev"` / "로컬 개발에서만" 문구 반영 확인.

## 후속(배포 반영)
- 코드 변경이므로 stg 컨테이너(`payment_system-app-1`)에 반영하려면 재시작/재빌드 필요:
  `docker compose -f docker-compose.stg.yml up -d --build`.
