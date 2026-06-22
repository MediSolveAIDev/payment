# sample_service 외부 접근 설정 워크로그

- 날짜: 2026-06-22
- 작업자: seungjinhan (oasis@medisolveai.com)

## 배경

직전 작업으로 호스트 nginx에 8001 통과 프록시(외부 8001 → `127.0.0.1:8001`)를 추가했다([2026-06-22-nginx-8001-passthrough-worklog.md](2026-06-22-nginx-8001-passthrough-worklog.md)). 그 8001 뒤 백엔드인 `sample_service`(Django 6 데모 상점, docker `8001:8000` publish)를 **외부 도메인에서도 접근 가능**하게 만들었다.

네트워크는 이미 열려 있었고(docker가 `0.0.0.0:8001` 바인딩) 실제 차단 요인은 Django 두 가지였다:
- `ALLOWED_HOSTS` — 외부 Host 헤더 거부(400 DisallowedHost)
- `CSRF_TRUSTED_ORIGINS` — 구독·결제·카드 POST 폼(`{% csrf_token %}`)이 외부 origin에서 403

요구 확인(AskUserQuestion): 외부 접속 경로 = **nginx 도메인:8001 경유** → origin `http://api-stg-pay.medisolveai.com:8001`.

## 변경

### 1) `sample_service/config/settings.py`
- `ALLOWED_HOSTS` 주석 보강 — env로 외부 도메인 추가, `*` 전체 허용(데모) 안내.
- **`CSRF_TRUSTED_ORIGINS` 추가** — 환경변수(콤마 구분)로 폼 제출 origin 주입. 외부 POST 403 방지.
- **`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` 추가** — https 프록시 뒤 원 스킴 인식(현재 8001은 평문이라 무영향, 추후 TLS 경유 대비).

### 2) `sample_service/docker-compose.yml`
- `environment`에 기본값 주입(셸/.env로 override 가능한 `${VAR:-default}` 형태):
  - `ALLOWED_HOSTS: ${ALLOWED_HOSTS:-api-stg-pay.medisolveai.com}`
  - `CSRF_TRUSTED_ORIGINS: ${CSRF_TRUSTED_ORIGINS:-http://api-stg-pay.medisolveai.com:8001}`

### 3) `sample_service/.env.example`
- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` 항목 + 설명 추가.

### 4) `sample_service/README.md`
- "외부에서 접속하기 (nginx 8001 경유)" 서브섹션 추가 — nginx 8001 블록·방화벽·env 표·접속 URL·평문 주의.

## 검증

- `manage.py check` → 0 issues.
- settings 로드 확인:
  - `ALLOWED_HOSTS = [..., 'api-stg-pay.medisolveai.com']`
  - `CSRF_TRUSTED_ORIGINS = ['http://api-stg-pay.medisolveai.com:8001']`
  - `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`

## 주의/후속

- **방화벽/보안그룹**: 8001 인바운드를 별도로 열어야 외부 접근(직전 nginx 워크로그와 동일).
- **다른 도메인/IP로 접속**: `ALLOWED_HOSTS`·`CSRF_TRUSTED_ORIGINS`를 그 주소로 교체(.env 또는 셸 env).
- **평문(http)**: 8001은 TLS 미적용. https 필요 시 별도 도메인·인증서 구성.
- 정식 매뉴얼: 8001 nginx 블록은 직전 작업에서 `docs/user_manual/10-install-deploy.md`에 반영·재빌드 완료. 샘플 외부 접근은 샘플 자체 README가 정식 문서라 별도 재빌드 없음.
