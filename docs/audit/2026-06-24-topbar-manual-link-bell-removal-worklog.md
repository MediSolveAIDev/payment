# 어드민 상단바 — 도움말→매뉴얼 링크 교체 + 종모양 아이콘 제거 워크로그

- 날짜: 2026-06-24
- 작업자: seungjinhan
- 요청: 상단 오른쪽 도움말 링크(/admin/guide/...)를 매뉴얼 링크로 교체(새 창), 기능 없는 종모양(알림) 아이콘 제거.

## 변경
- `app/admin/templates/base.html`
  - 도움말 아이콘 링크(`/admin/guide/{current_guide}`, help-circle) + 이를 위한 `guide_map`/`current_guide`
    Jinja 블록 제거 → **매뉴얼 링크**로 교체: `<a href="/user-manual/" target="_blank" rel="noopener">`(book-open 아이콘, 새 창).
  - 알림(bell) 아이콘 버튼 제거(기능 없음).
- `docs/user_manual/01-admin-console.md` §1.4 — 상단 '매뉴얼 열기(새 창)' 안내 추가. build.py 재빌드.

## 검증
- 실행 중 앱에서 `GET /user-manual/` → HTTP 200(매뉴얼 인덱스 서빙) 확인 — 링크 대상 유효.
- base.html에 guide_map/current_guide/help-circle/bell 잔존 참조 0건, `path`(nav 매크로용) 유지 확인.
- Playwright로 상단바 렌더 확인: 책 아이콘(매뉴얼)·테마 토글·컴팩트 뷰만 노출, 종모양 없음.

## 비고
- `/admin/guide/*` 인앱 가이드 라우트 자체는 보존(상단 링크만 매뉴얼로 변경).
- 정적/템플릿 변경은 실행 중 컨테이너 재배포 후 반영.
