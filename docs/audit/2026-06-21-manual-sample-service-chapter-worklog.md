# 매뉴얼에 샘플 서비스 사용법(17장) 추가 워크로그

작성일: 2026-06-21
요청: 매뉴얼에 `sample_service` 사용법을 추가하되, 개념적으로 이해되도록 잘 설명.

## 변경
- **신규 문서**: `docs/user_manual/17-sample-service.md` (개발자 매뉴얼 그룹, 섹션 17 — 기존 0~16에 이어 번호 체계 유지).
- **build.py**: `DOCS` 목록에 `("17-sample-service.md", "개발자 매뉴얼")` 추가.
- HTML 재빌드(18개 문서). 네비 배지 0~17 연속 확인.

## 17장 구성(개념 중심)
- 17.1 이게 무엇이고 왜 있나 — "외부 서비스 역할의 동작하는 참조 구현", 배우기/가져다쓰기 두 목적.
- 17.2 큰 그림 — 세 주체(사용자·sample_service·결제서버·토스) 다이어그램과 책임 경계(카드번호는 토스만).
- 17.3 화면이 곧 문서 — 「개발자 노트」 + 화면↔호출 API↔`payment_client.py` 함수 매핑 표.
- 17.4 코드 구조 — payment_client.py(핵심·복사 대상)·views.py·templates·urls.py.
- 17.5 실행 방법 — 사전(서비스 등록·키 발급) / `.env` / 로컬 2서버 / docker(8001).
- 17.6 데모 시나리오 — 권장 순서(로그인→서비스→카드→구독/결제→내역→알림), 카드 선등록 강조.
- 17.7 가져다 쓰기 + 연동 4규칙(503≠실패, 금액 서버보관, order_id 멱등, access_allowed) + 알림 URL `host.docker.internal:8001` 주의.

## 출처(사실 정확성)
- `sample_service/README.md`, `shop/urls.py`, `shop/payment_client.py`(함수·엔드포인트), `.env.example`, `docker-compose.yml`을 대조해 경로·함수·실행법·포트를 그대로 반영.
- API 사실은 검증된 `11-service-api.md`와 동일(Card Vault: 카드 선등록, auth_key/customer_key는 카드 등록에만).

## 후속: 내용 보강 + 화면 캡처
- **17.6에 로그인 스크린샷 삽입**: 실행 중인 샘플(`http://localhost:8001/login`)을 macOS Chrome 헤드리스(`--headless --screenshot`, 1280×720)로 직접 캡처 → `docs/user_manual/assets/img/sample-01-login.png`. 본문에 `![](assets/img/sample-01-login.png)`로 표시.
- **캡처 안내**: 로그인 이후(데이터·세션 필요) 화면은 파일명 규칙(`sample-02-services` … `sample-08-notifications`)으로 드롭하면 표시되도록 목록 callout 추가.
- **17.8 핵심 호출 예시 신설**: 카드 등록·구독 생성의 요청/응답 JSON(11-service-api 기준, billingKey 미노출·금액 미포함 강조).
- 제약: 샘플의 `PAYMENT_API_BASE=http://127.0.0.1`가 301 반환(로컬 백엔드 미연결)이라 로그인 이후 화면은 데이터가 안 떠 자동 캡처 보류. 백엔드 가동+데모데이터 시 헤드리스 스크립트로 일괄 캡처 가능.

## 검증
- 재빌드 OK(18개), `17-sample-service.html` 생성, 네비 `0..17` 연속, 본문 내부 링크(.md→.html) 정상 변환, 잔여 `.md` 링크 0.
- 로그인 이미지 `<img src="assets/img/sample-01-login.png">` 렌더 확인, 17.8 예시 렌더 확인.

## 후속2: 샘플 로그인 이후 화면 7장 반영
사용자가 한글 파일명으로 캡처 제공(서비스/카드/요금제/일반결제/내구독/결제내역/받은알림). 웹 서빙 안정성을 위해 ASCII로 복사 정규화 후 17.6에 워크스루(②~⑧)로 삽입.
- 매핑: 서비스→`sample-02-services` · 카드→`sample-03-card` · 요금제→`sample-04-plans` · 일반결제→`sample-05-oneoff` · 내구독→`sample-06-my` · 결제내역→`sample-07-history` · 받은알림→`sample-08-notifications`. (한글 원본은 보존 — 삭제 가능)
- 17.6의 '참고(캡처)' 안내 callout을 실제 이미지 8장(로그인 포함) 워크스루로 교체.
- 검증: sample-01~08 전부 존재·HTML 렌더 확인, 재빌드 18개.

## 후속3: 전체 프로세스를 다이어그램(그림)으로
- **상단 「전체 프로세스 한눈에」에 8단계 흐름 다이어그램**(`.flow` 번호 박스 1~8: 로그인→서비스→카드→구독→내구독→일반결제→내역→알림) 추가 — 기존 텍스트 흐름 callout을 그림으로 대체. 화면 스크린샷은 그 아래 그대로.
- **17.2 큰 그림을 ASCII → SVG 구조도**로 교체: 4박스(사용자·sample_service·결제서버·토스) + 호출 화살표. 파란색=내 서비스가 작성하는 코드(② HMAC API·③ 웹훅), 회색=토스/브라우저. 인라인 스타일 SVG(클래스 의존 X)로 작성.
- 검증: 재빌드 18개, `.flow`·`<svg>` HTML 반영, 헤드리스 렌더 캡처로 두 다이어그램 정상 표시 확인(상단 8박스, 17.2 4박스+화살표).
