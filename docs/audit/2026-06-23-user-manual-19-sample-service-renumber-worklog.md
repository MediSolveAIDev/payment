# 워크로그 — user_manual 19-sample-service 번호 정정 + 정확성 보강

- 날짜: 2026-06-23
- 대상: `docs/user_manual/19-sample-service.md`

## 한 일

### 1. 번호 정정 (17 → 19)
- H1: `# 17. ...` → `# 19. 샘플 서비스(sample_service) 사용법`
- 도입 무번호 섹션 「전체 프로세스 한눈에」을 `## 19.1`로 승격
- 기존 `## 17.1~17.8` → `## 19.2~19.9`로 재번호(개념→코드→실행→규칙 순서 유지)
- 본문 내 옛 번호 언급 정정: "아래 17.1~17.8" → "아래 19.2~19.9"
- 코드블록 안 `#`(쉘 주석, 파이썬 주석)은 건드리지 않음
- 다른 문서 링크(`13-service-api.md`, `14-feature-card.md`, `17-feature-notifications.md`)·"11.8" 교차참조는 그대로 유지

### 2. 정확성 보강(코드 대조)
대조 파일: `sample_service/README.md`, `shop/payment_client.py`, `shop/urls.py`, `shop/views.py`
- 화면↔API 매핑 표의 결제 내역 행 보강: 취소 경로를 `POST .../{order_id}/cancel`로 명시,
  `payment_client.py` 칸에 `get_payments`/`cancel_one_off_payment` 채움
- 19.9에 **③ 웹훅 수신(`POST /notify`)** 예시 추가 — 수신 측 서명 검증
  (`X-Signature`/`X-Timestamp`/`X-Nonce`, `_verify_notify_signature` 미러) 코드 스니펫 포함
- 부가 클라이언트 함수 목록 추가: `get_subscription`/`cancel`/`resume`/`manual_pay`,
  `get_card`/`delete_card`, `get_payments`/`cancel_one_off_payment`, `add_usage_days`

### 3. 이미지
- 기존 sample-01~08 참조 그대로 유지(번호만 흐름과 일치 확인). 신규 파일 생성 없음.

## 참조 이미지(8개, 모두 기존 파일)
- assets/img/sample-01-login.png
- assets/img/sample-02-services.png
- assets/img/sample-03-card.png
- assets/img/sample-04-plans.png
- assets/img/sample-05-oneoff.png
- assets/img/sample-06-my.png
- assets/img/sample-07-history.png
- assets/img/sample-08-notifications.png
