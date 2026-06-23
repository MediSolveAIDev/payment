# 워크로그 — user_manual/13-service-api.md 번호 정정 + 누락 엔드포인트 보강

- 날짜: 2026-06-23
- 작성자: oasis@medisolveai.com (Claude)
- 대상 파일: `docs/user_manual/13-service-api.md`

## 배경
문서 번호가 파일명(13)과 불일치(`11.`)했고, 실제 API 대조 시 일부 조회 엔드포인트가
문서에 빠져 있었다.

## 변경 내역

### 1. 번호 재정렬 (11 → 13)
- H1: `# 11. 서비스 연동 API` → `# 13. 서비스 연동 API`
- 모든 `## 11.x` → `## 13.x`, `### 11.x.x` → `### 13.x.x` 재번호.
- 본문 내 옛 섹션번호 언급 정정: `11.2.2/11.2.3/11.3.1/11.4/11.5/11.5.4/11.6.3` →
  `13.2.2/13.2.3/13.4.1/13.5/13.6/13.6.4/13.7.3` 등.
- 코드블록 안의 `#`(주석)·URL fragment는 헤더가 아니므로 건드리지 않음.

### 2. 누락 엔드포인트 보강 (실제 API 대조)
- **신규 13.3 "조회 API — 서비스·요금제 목록"** 추가:
  - `GET /api/v1/services` (무인증, `app/api/v1/services.py`) — 문서에 전혀 없었음.
    `ServiceListResponse`(id·name·status), `public_service_list_enabled=false`면 404.
  - `GET /api/v1/plans` (HMAC, `app/api/v1/plans.py`) — 문서에 전혀 없었음.
    `PlanResponse` 전체 필드(price/amount/billing_cycle/cycle_days/cycle_minutes/
    first_payment_type·value/trial_enabled·trial_days/auto_renew/extra_info) 표·예시 추가.
- **신규 13.7.4 "토스 웹훅 수신 — POST /api/v1/webhooks/toss"** 추가:
  토스 → 서버 방향, 무인증(IP 검증 선택), transmission-id 중복 방지, `WebhookAck` 응답.
  연동 서비스가 직접 호출하지 않음을 명시.

### 3. 섹션 시프트(13.3 삽입에 따른 후속 번호)
- 카드 13.3 → 13.4, 구독 13.4 → 13.5, 결제 13.5 → 13.6,
  알림 13.6 → 13.7, 오류 13.7 → 13.8, 예제 13.8 → 13.9.
- 카드 표의 라우트 라인 번호 GET 80→83, DELETE 111→114로 정정.

## 검증
- 코드와 대조: HMAC 4헤더·canonical string(`METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(BODY)`),
  타임스탬프 ±300s, nonce 600s TTL, 일반 120/분·결제 20/분 — `app/core/config.py`/
  `app/api/deps.py`/`app/core/security.py`와 일치 확인.
- 모든 헤더가 13.x로 순차 정렬됨(grep 확인). 잔여 `11.x` 참조 없음.

## 비고
- API 문서라 스크린샷 미삽입(요청 사양).
- 다른 문서 링크·파일명·build.py 변경 없음.
