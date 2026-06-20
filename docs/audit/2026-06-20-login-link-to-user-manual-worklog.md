# 로그인 페이지 매뉴얼 링크를 새 매뉴얼로 교체 워크로그

- 날짜: 2026-06-20
- 작업자: seungjinhan

## 요청

로그인 페이지의 '전체 매뉴얼 보기' 링크를 **새로 만든 매뉴얼(`docs/user_manual`)** 링크로 교체.

## 변경

- **`app/admin/templates/login.html`** — 링크 `href="/manual/"` → **`/user-manual/`**, 라벨 "📚 전체 매뉴얼 보기 (사용자·개발자)"로 변경. (옛 `docs/manual` → 새 `docs/user_manual`)
- **`app/main.py`** — 새 매뉴얼을 `/user-manual` 로 공개 정적 서빙하는 마운트 추가(`StaticFiles(docs/user_manual, html=True, check_dir=False)`). 기존 `/manual` 마운트는 그대로 유지(하위 호환).
- **`Dockerfile`** — `COPY docs/user_manual ./docs/user_manual` 추가(운영 이미지에 포함).
- **`.dockerignore`** — `!docs/user_manual`, `!docs/user_manual/**` 추가(빌드 컨텍스트에 포함).

## 검증

- `app/main.py` 구문 OK.
- StaticFiles 격리 테스트: `/user-manual/`(index)·`/user-manual/assets/manual.css`·`/user-manual/10-install-deploy.html` 모두 **HTTP 200**.
- 문서 동기화: 로그인 매뉴얼 링크/정적 마운트를 설명한 dev_manual·user_manual 문서가 없어 별도 갱신 불필요(변경은 템플릿+마운트로 자기완결).

## 비고

- 로그인 화면의 '전체 매뉴얼 보기'를 누르면 새 탭으로 **사용·개발 통합 매뉴얼**(`/user-manual/`)이 열린다.
- 옛 `/manual`(docs/manual)도 계속 서빙되지만, 진입 링크는 새 매뉴얼로 교체됨.
