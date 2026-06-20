# 설계 — Centurion Suite 디자인 가이드 적용 (어드민 콘솔)

날짜: 2026-06-10
상태: 승인됨
가이드: docs/design/centurion-suite-handoff/ (README·colors/color-tokens·typography/typography-system·components/*)

## 결정 사항
- 브랜드 메인 색: **Centurion 기본 블루 `#476CFF`**(PAY 전용 색 없음 → 공통 기본). 추후 `data-product`로 교체 가능하게 토큰만 준비.
- 적용 범위: **어드민 콘솔만**(`app/static/admin.css` + 일부 템플릿 클래스 정렬). 외부 API(UI 없음)·샘플 shop·백엔드 제외.
- 적용 깊이: **토큰 전면 교체 + 핵심 컴포넌트 정렬**. 구조·기능·**클래스명 유지**(템플릿/테스트 무영향).

## 1. 폰트
- Pretendard CDN 로드(예: jsDelivr `pretendard` dynamic-subset CSS). `--font-sans`를 `'Pretendard', -apple-system, ... , sans-serif` 우선으로 변경. Inter `@import`는 제거하거나 폴백. 웨이트 400/500/600 사용(700 미사용).

## 2. 색 토큰 (`app/static/admin.css` `:root`)
### 신규 Centurion 토큰(추가)
- Primary: `--color-primary:#476CFF`, `--color-primary-100:#F0F4FF`, `--color-primary-300:#DDE6FF`, `--color-primary-500:#97B5FF`.
- Gray: `--gray-100:#FBFBFB`, `-200:#F3F3F3`, `-300:#E3E3E3`, `-400:#D6D6D6`, `-500:#CFCFCF`, `-600:#9F9F9F`, `-700:#6E6E6E`, `-800:#3E3E3E`.
- Red: `--color-red:#FF4E51`, `-100:#FFEFEF`, `-300:#FFE1E1`, `-500:#FFC4C5`.
- 텍스트/보더/배경: `--color-text-primary:#000000`, `--color-text-secondary:#3E3E3E`, `--color-text-disabled:#9F9F9F`, `--color-text-placeholder:#9F9F9F`, `--color-border-default:#E3E3E3`, `--color-border-focus:var(--color-primary)`, `--color-bg-page:#FFFFFF`, `--color-bg-subtle:#FBFBFB`, `--color-bg-muted:#F3F3F3`, `--color-hover-dark:#222943`.
- Purple(가이드 공통, 선택): `--color-purple:#AC47FF` 등은 필요 시.

### 레거시 토큰 리매핑(값만 교체 → 기존 컴포넌트 CSS가 새 색 상속)
- `--brand`, `--brand-logo`, `--status-info` → `#476CFF`.
- `--accent-red`, `--status-error` → `#FF4E51`.
- `--status-online`(성공/Complete) → `#476CFF`(가이드: color-success = 제품 main-blue).
- `--status-warning` → `#FF4E51`(또는 red-300 배경). 
- `--bg-page` → `#FFFFFF`, `--surface-1/2/3` → gray-100/100/200, `--border` → gray-300(#E3E3E3), `--border-strong` → gray-400.
- `--black`(주요 텍스트) → `#000000`; `--black-80` → `#3E3E3E`(gray-800); `--black-40` → `#9F9F9F`(gray-600, placeholder/secondary); `--black-20` → `#D6D6D6`; `--black-10` → `#E3E3E3`; `--black-5`/`--black-4` → `#F3F3F3`/`#FBFBFB`. (불투명 그레이로 매핑 — 가이드는 solid gray 사용.)
- 배지용 파스텔 액센트(`--accent-purple/indigo/...`, `--card-*`)는 가이드 팔레트(primary-100, gray-200, purple-100, red-100 등)로 정돈하되, 대시보드 차트의 다색 표현은 가독성 유지선에서 최소 보존.

## 3. 타이포 토큰
- 가이드 `--typo-*` 28종 추가(Pretendard, 사이즈별 LH 130/140/150/160%).
- 기존 `--t-*` 매핑: `--t-display`→Title L(24/1.4/600), `--t-h1`→Title S(18/1.5/600), `--t-h2`→Heading M(16/1.5/600), `--t-title`→Body 600(14/1.6), `--t-body`→Body 400(14/1.6), `--t-small`→Caption M(12/1.3).

## 4. 핵심 컴포넌트 정렬 (가이드 components/*)
- **Input/Select/Textarea**(input.md): height **50px**(textarea는 min-height), 보더 `--color-border-default`, **Focus → `--color-primary` 보더**, placeholder `--gray-600`. (옥텟 `.ip-oct`·체크박스 등 특수 입력은 기존 크기 유지.)
- **Button**(button.md): `.btn-primary` bg `--color-primary`·흰 텍스트·hover `--color-hover-dark`; `.btn-danger` red(#FF4E51, 텍스트/보더); `.btn-sub`/`.btn-ghost` gray; `.btn-text` gray 텍스트. 비활성 gray-400.
- **Badge/Tag**(badge-tag.md): 12px/500. 상태 배지 매핑 — ACTIVE/정상=primary 계열, INACTIVE/중립=gray, 에러/취소=red. (badge-ACTIVE/INACTIVE/EXPIRED/SUBSCRIPTION/ONE_OFF/FIRST/RENEWAL/RETRY 등 기존 클래스 값만 새 팔레트로.)
- **Toggle/Checkbox/Radio**(toggle/check/radio.md): ON/Active = `--color-primary`, 체크 ✓ 흰색, OFF gray-500.
- **Tab**(tab.md): active 하단 바 `--color-primary`. **Pagination**(pagination.md): active bg `--color-primary-100`. **LNB(Menu)**(header.md): active bg `--color-primary-100`(현재 `--black-5`).
- **Modal**(modal.md): 버튼으로만 닫힘(유지). Warning=red(아이콘/확인 버튼), Complete=primary. **Toast**(toast.md): 상단 중앙 2초, complete=primary/error=red.
- **공통 인터랙션**: Focus=primary 보더 / Hover=primary-100 또는 gray-200 / Error=red 보더·텍스트.

## 5. 검증·리스크
- **클래스명·DOM 구조 보존** → 템플릿/e2e 무영향(e2e는 텍스트·상태 검증). 전체 테스트(서버 472 + 어드민 e2e) 그대로 통과 확인.
- CSS만 변경(JS 토스트/모달 동작 유지). 캐시버스팅(`?v=mtime`)으로 브라우저 즉시 반영.
- 리스크: 토큰 리매핑으로 일부 화면 대비/가독성 변동 가능 → 핵심 화면(대시보드/목록/폼/모달) 육안 점검 후 보정.

## 적용하지 않는 것
- 외부 API, 샘플 shop, 백엔드 로직, DOM/템플릿 구조 변경(클래스 값 정렬 외).
