# Centurion Suite 디자인 적용 구현 계획 (어드민 콘솔)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 어드민 콘솔(app/static/admin.css + 일부 템플릿)을 Centurion Suite 디자인 시스템(Pretendard·#476CFF·gray 스케일·red #FF4E51·가이드 컴포넌트 스펙)으로 전면 reskin하되 구조·기능·클래스명은 유지한다.

**Architecture:** admin.css `:root` 토큰을 Centurion 값으로 전면 교체(레거시 토큰은 값만 remap → 전 컴포넌트가 새 색 상속) + 핵심 컴포넌트(입력 50px·버튼·배지·토글/체크/라디오·LNB/페이지네이션/탭·모달/토스트)를 가이드 스펙에 맞춰 정렬. 클래스명·DOM 보존으로 템플릿/테스트 무영향.

**Tech Stack:** CSS(admin.css), Jinja2(base.html), Pretendard CDN.

**스펙:** `docs/superpowers/specs/2026-06-10-centurion-design-apply-design.md`. 가이드: `docs/design/centurion-suite-handoff/`.

## 파일 구조
- `app/static/admin.css` — 토큰 + 컴포넌트(주 변경).
- `app/admin/templates/base.html` — Pretendard 폰트 링크.
- (필요 시) 배지/특수 입력 클래스가 인라인 색을 쓰는 템플릿 소수 정렬.

---

### Task 1: 토큰 레이어 — Pretendard + Centurion 색/타이포

**Files:**
- Modify: `app/static/admin.css`(`:root` + 폰트 import/var), `app/admin/templates/base.html`(폰트 링크)

- [ ] **Step 1: Pretendard 로드** — `base.html` `<head>`의 admin.css `<link>` **앞**에 추가:
```html
  <link rel="stylesheet" as="style" crossorigin
        href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
```
admin.css 상단의 `@import url('...Inter...')` 줄은 제거(Pretendard로 대체).

- [ ] **Step 2: `:root` 토큰 교체** — admin.css `:root`를 아래로 갱신(신규 Centurion 토큰 + 레거시 remap). 변수명은 유지(컴포넌트가 참조):
```css
:root {
  --font-sans: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;

  /* Centurion — Primary(#476CFF) / Gray / Red */
  --color-primary: #476CFF; --color-primary-100: #F0F4FF; --color-primary-300: #DDE6FF; --color-primary-500: #97B5FF;
  --gray-100: #FBFBFB; --gray-200: #F3F3F3; --gray-300: #E3E3E3; --gray-400: #D6D6D6;
  --gray-500: #CFCFCF; --gray-600: #9F9F9F; --gray-700: #6E6E6E; --gray-800: #3E3E3E;
  --color-red: #FF4E51; --color-red-100: #FFEFEF; --color-red-300: #FFE1E1; --color-red-500: #FFC4C5;
  --color-purple: #AC47FF; --color-purple-100: #F8F0FF;
  --color-hover-dark: #222943;
  --color-text-primary: #000000; --color-text-secondary: #3E3E3E;
  --color-text-disabled: #9F9F9F; --color-text-placeholder: #9F9F9F;
  --color-border-default: #E3E3E3; --color-border-focus: var(--color-primary);
  --color-bg-page: #FFFFFF; --color-bg-subtle: #FBFBFB; --color-bg-muted: #F3F3F3;

  /* 레거시 토큰 remap — 기존 컴포넌트가 새 색 상속 */
  --black: #000000; --black-80: #3E3E3E; --black-40: #9F9F9F;
  --black-20: #D6D6D6; --black-10: #E3E3E3; --black-5: #F3F3F3; --black-4: #FBFBFB; --white: #FFFFFF;
  --bg-page: #FFFFFF; --surface-1: #FBFBFB; --surface-2: #FBFBFB; --surface-3: #F3F3F3;
  --brand: #476CFF; --brand-logo: #476CFF;
  --accent-purple: #AC47FF; --accent-indigo: #476CFF; --accent-blue: #97B5FF;
  --accent-cyan: #DDE6FF; --accent-mint: #476CFF; --accent-green: #476CFF;
  --accent-yellow: #FFC4C5; --accent-orange: #FF8064; --accent-red: #FF4E51;
  --card-lavender: #F0F4FF; --card-blue: #F0F4FF; --card-mint: #F0F4FF; --card-cyan: #EDF9FF;
  --status-online: #476CFF; --status-warning: #FF8064; --status-error: #FF4E51; --status-info: #476CFF;
  --border: #E3E3E3; --border-strong: #D6D6D6;

  --radius-sm: 8px; --radius-md: 12px; --radius-lg: 16px; --radius-xl: 20px; --radius-pill: 9999px;
  --shadow-sm: 0 2px 8px rgba(0,0,0,.06); --shadow-pop: 0 8px 24px rgba(0,0,0,.12);

  /* Typo — Pretendard, 사이즈별 LH (가이드) */
  --t-display: 600 24px/1.4 var(--font-sans);
  --t-h1: 600 18px/1.5 var(--font-sans);
  --t-h2: 600 16px/1.5 var(--font-sans);
  --t-title: 600 14px/1.6 var(--font-sans);
  --t-body: 400 14px/1.6 var(--font-sans);
  --t-small: 400 12px/1.3 var(--font-sans);
}
```
(주석: "Centurion Suite 적용 — docs/design 가이드 토큰".)

- [ ] **Step 3: import 정상 확인** — `uv run python -c "import app.main"`(템플릿 영향 없음, CSS만). e2e 렌더 확인: `uv run pytest tests/e2e -q` → 통과(클래스/구조 불변).

- [ ] **Step 4: 커밋**
```bash
git add app/static/admin.css app/admin/templates/base.html
git commit -m "style(centurion): 토큰 전면 교체(Pretendard·#476CFF·gray·red) — 디자인 가이드 적용 1/2

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 핵심 컴포넌트 정렬

**Files:**
- Modify: `app/static/admin.css`

- [ ] **Step 1: 입력(Input/Select/Textarea)** — 가이드 input.md: height 50px, Focus→primary.
```css
input, select, textarea {
  width: 100%; max-width: 440px; height: 50px; padding: 0 14px; font: var(--t-body);
  border: 1px solid var(--color-border-default); border-radius: var(--radius-sm);
  background: var(--white); color: var(--color-text-primary);
}
textarea { height: auto; min-height: 96px; padding: 12px 14px; }
input::placeholder, textarea::placeholder { color: var(--color-text-placeholder); }
input:focus, select:focus, textarea:focus { outline: none; border-color: var(--color-primary); }
input.is-error { border-color: var(--color-red); }
```
특수 입력은 높이 강제 해제 유지: `.ip-oct`(56px·중앙), `.check-row input[type=checkbox]`(16px), `.toolbar select`(36px) 등 기존 override 그대로 두되 height가 50px를 덮는지 확인(필요 시 height override 추가).

- [ ] **Step 2: 버튼** — 가이드 button.md:
```css
.btn-primary { background: var(--color-primary); color: var(--white); }
.btn-primary:hover { background: var(--color-hover-dark); }
.btn-primary:disabled { background: var(--gray-400); cursor: not-allowed; }
.btn-sub { background: var(--gray-200); color: var(--color-text-primary); }
.btn-sub:hover { background: var(--gray-300); }
.btn-ghost { background: var(--white); border-color: var(--color-border-default); color: var(--color-text-primary); }
.btn-ghost:hover { background: var(--color-primary-100); }
.btn-danger { background: var(--color-red-100); color: var(--color-red); }
.btn-danger:hover { background: var(--color-red-300); }
.btn-text { color: var(--gray-700); } .btn-text:hover { color: var(--color-text-primary); background: var(--gray-200); }
```

- [ ] **Step 3: LNB·Pagination·Tab active = primary** — 가이드 header/pagination/tab.md:
  - `.lnb a.active { background: var(--color-primary-100); color: var(--color-primary); }` 및 `.lnb a.active [data-lucide] { color: var(--color-primary); }`, active 좌측 바 색 `--color-primary`.
  - `.lnb a:hover { background: var(--color-primary-100); }`
  - Pagination active: 해당 클래스 bg `var(--color-primary-100)`, 텍스트 `var(--color-primary)`(현재 페이지네이션 active 셀렉터 확인 후 적용).
  - Tab active: 하단 바/텍스트 `--color-primary`(탭 컴포넌트 셀렉터 확인 후).

- [ ] **Step 4: Toggle/Checkbox/Radio = primary** — 체크박스 accent: `.check-row input[type="checkbox"] { accent-color: var(--color-primary); }`. 토글/라디오 클래스가 있으면 ON/Active bg `--color-primary`, OFF `--gray-500`.

- [ ] **Step 5: 배지 팔레트 정돈** — 의미별 다색은 유지하되 가이드 팔레트로:
  - 에러/실패/정지/취소불가류(SUSPENDED/FAILED) → 텍스트 `var(--color-red)`, bg `var(--color-red-100)`, dot `var(--color-red)`.
  - 중립(CANCELED/INACTIVE/ARCHIVED) → 텍스트 `var(--gray-700)`, bg `var(--gray-200)`, dot `var(--gray-500)`.
  - 정상/성공(ACTIVE/DONE/FIRST) 및 정보(TRIAL/SUBSCRIPTION) → primary 계열(텍스트 `var(--color-primary)`, bg `var(--color-primary-100)`, dot `var(--color-primary)`).
  - 나머지(EXPIRED/ONE_OFF/RENEWAL/RETRY/PAST_DUE/PENDING)는 구분을 위해 purple/gray/red-soft 등 가이드 팔레트 내에서 최소 다색 유지(가독 우선). 하드코딩 hex는 가이드 값으로 교체.
  - `.badge { font: var(--typo-caption-md-500, 500 12px/1.3 var(--font-sans)); }` 12px/500.

- [ ] **Step 6: 모달/토스트/인터랙션** — modal.md/toast.md:
  - `.modal--warning` 아이콘/확인 버튼 red, `.modal--complete` primary. (admin.js가 modal--warning/complete 클래스 토글 — CSS만 정렬.)
  - `.toast--complete` primary 계열, `.toast--error` red.
  - 공통: `a`/포커스 가능한 요소 focus 시 primary, error 텍스트/보더 red.
  - `.notice`(성공 안내) bg `--color-primary-100`, 텍스트 `--color-primary`. `.error` bg `--color-red-100`, 텍스트 `--color-red`.

- [ ] **Step 7: 통과 확인** — `uv run pytest tests/e2e -q` → PASS(렌더/클래스 불변). `grep -n "var(--" app/static/admin.css | grep -c "undefined"` 식으로 미정의 토큰 참조 없는지 점검(정의되지 않은 var 사용 시 fallback 확인).

- [ ] **Step 8: 커밋**
```bash
git add app/static/admin.css
git commit -m "style(centurion): 핵심 컴포넌트 정렬(입력 50px·버튼·배지·LNB/탭·모달/토스트) — 2/2

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 검증 + 육안 점검 보정
- [ ] **Step 1: 전체 테스트** — `uv run pytest -q` → 전체 PASS(백엔드/e2e 영향 없음 확인).
- [ ] **Step 2: 토큰 일관성 점검** — admin.css에서 `Inter` 잔재 0, 미정의 `var(--...)` 참조 0, 주요 셀렉터(.btn-primary/.lnb a.active/.badge-*/input:focus/.modal/.toast)가 새 토큰 사용. 대시보드/목록/폼/모달/토스트 핵심 화면 셀렉터가 깨지지 않았는지 grep.
- [ ] **Step 3: 최종 리뷰** — 가이드 핵심 스펙(primary #476CFF, 입력 50px·focus primary, LNB/pagination active primary-100, badge 12/500, modal 버튼-only, red #FF4E51) 충족 + 클래스/구조 보존 확인.

## 변경하지 않는 것
- DOM/템플릿 구조·클래스명(값 정렬 외), 외부 API/샘플 shop/백엔드, JS 동작(모달/토스트 로직).
