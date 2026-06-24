# 어드민 콘솔 다크/라이트 모드 워크로그

- 날짜: 2026-06-24
- 작업자: seungjinhan
- 요청: 상단에 다크/라이트 모드를 선택하게 하고, 선택할 때마다 화면 모드를 즉시 전환.

## 구현

어드민 콘솔(htmx)의 CSS가 전부 `var(--*)` 토큰을 참조하므로(컴포넌트 규칙에 하드코딩 색상 사실상 0),
**토큰 오버라이드만으로 화면 전체를 전환**한다.

- `app/static/admin.css` — `:root[data-theme="dark"]` 블록 추가: 표면(어두움)·텍스트(밝음)·보더·그레이
  스케일 반전 + 브랜드/상태 틴트·그림자 다크 매핑 + `color-scheme: dark`. 토글 아이콘 표시 CSS(.ic-sun/.ic-moon).
- `app/admin/templates/base.html`
  - `<head>`에 **FOUC 방지 인라인 스크립트** — CSS 적용 전에 `localStorage('admin-theme')`(없으면 OS
    `prefers-color-scheme`)로 `data-theme`를 미리 설정.
  - 상단(topbar)에 토글 버튼(`#theme-toggle`) — 라이트면 달, 다크면 해 아이콘(CSS가 테마에 따라 노출).
- `app/static/admin.js` — 토글 클릭 시 `data-theme` 전환 + `localStorage` 저장(선택 즉시 전환·영구 기억).
  아이콘 전환은 CSS가 담당하므로 lucide 재렌더 불필요(아이콘 span을 래퍼로 감싸 안전).

## 검증
- `admin.js` `node --check` 통과.
- **Playwright 스크린샷으로 라이트/다크 육안 검증**(scratchpad): 다크 — 어두운 페이지+떠 보이는 카드,
  밝고 가독성 좋은 텍스트, primary 틴트 활성 메뉴, 버튼/배지/표 정상. 라이트 — 기존 디자인 그대로.
  토글 아이콘이 테마에 맞게(달↔해) 정확히 전환됨.
- 동작: 클릭 즉시 전환, 새로고침/재접속 시 선택 유지, 초기값은 OS 선호 반영.

## 비고
- 정적 파일 변경은 실행 중 컨테이너 재배포 후 화면 반영(현재 실행 컨테이너는 이전 빌드본).
- 향후 필요 시 sample_service에도 동일 토큰 방식으로 확장 가능(이번 범위는 어드민 콘솔).
