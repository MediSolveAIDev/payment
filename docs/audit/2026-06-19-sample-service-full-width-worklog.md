# sample_service 화면 전체 가로 폭 표시 워크로그

- 날짜: 2026-06-19
- 작업자: seungjinhan

## 요청

sample_service 화면을 가로로 전체 사이즈(full width)로 나오게 한다.

## 원인

`sample_service/shop/templates/shop/base.html`의 공통 레이아웃이 `max-width:960px`로
가로폭을 제한하고 있었다(헤더 `.site-head-in`, 본문 `.wrap`, 푸터 `.site-foot` 3곳).

## 변경 내용

- 1차: `base.html`의 3곳 `max-width:960px` → `max-width:100%`(전체 폭).
- 2차(후속 요청 "중간 내용은 페이지 중간으로 나오면 된다"): **본문 `.wrap`만 `max-width:960px`로 되돌려 가운데 정렬**.
  최종 상태:
  - 헤더 `.site-head-in`, 푸터 `.site-foot` → `max-width:100%` (전체 폭 바).
  - 본문 `.wrap` → `max-width:960px; margin:0 auto` (중간 콘텐츠는 페이지 중앙 배치).
  - 로그인·카드·결제 등 **개별 폼 카드**(`max-width:480~560px` 가운데 정렬)는 가독성을 위해 그대로 유지.

## 검증

- `docker compose up -d --build sample`로 재빌드(이미지에 템플릿 COPY 구조라 재빌드 필요 — DB 볼륨만 마운트).
- `curl http://localhost:8001/login` 응답 CSS에서 `.wrap { max-width:100%`·`.site-head-in { max-width:100%` 확인.

## 참고

- 코드 변경은 `sample_service`(별도 git 저장소)에 있으며, payment_system의 `docs/dev_manual`은 결제 서버 기능 문서라 이 레이아웃 변경과 직접 관련 없음.
