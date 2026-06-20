# sample_service 여러 줄 `{# #}` 주석이 화면에 노출되던 버그 수정 워크로그

- 날짜: 2026-06-19
- 작업자: seungjinhan

## 증상

결제 내역 화면 상단에 다음 주석 텍스트가 그대로 출력됨:
`{# ===... 결제 내역 화면 ① 구독 결제: ... #}`

## 원인

Django 템플릿의 `{# ... #}` 인라인 주석은 **한 줄만** 지원한다. 여러 줄에 걸친
`{# ... #}`는 주석으로 처리되지 않고 본문에 그대로 렌더된다.

## 변경 내용

- `shop/templates/shop/history.html`: 상단 여러 줄 `{# #}` → `{% comment %}...{% endcomment %}`로 변경.
- `shop/templates/shop/card.html`: 수동 authKey 폴백 설명의 여러 줄 `{# #}`도 동일하게 `{% comment %}`로 변경(같은 버그 예방).
- 전체 템플릿을 스캔해 여러 줄 `{# #}`는 이 2건뿐임을 확인.

## 검증

- `docker compose up -d --build sample` 재빌드 후 `curl http://localhost:8001/history`에
  주석 텍스트("결제 내역 화면") 미노출(grep 0건) 확인.
