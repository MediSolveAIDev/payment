# Centurion Suite — 프론트엔드 핸드오프

> 전 제품(Say · Bay · Charty · Watch) 공통 디자인 시스템

---

## 1. 타이포그래피

> [상세 스펙 →](typography/typography-system.md)

**Pretendard** / Regular(400) · Medium(500) · SemiBold(600) / Letter Spacing 0px

| 카테고리 | Size | LH | 웨이트 |
|----------|------|----|--------|
| Headline L | 40px | 130% | 400 · 500 · 600 |
| Headline M | 32px | 130% | 400 · 500 · 600 |
| Title L | 24px | 140% | 400 · 500 · 600 |
| Title M | 20px | 140% | 400 · 500 · 600 |
| Title S | 18px | 150% | 400 · 500 · 600 |
| Heading M | 16px | 150% | 400 · 500 · 600 |
| Heading S | 14px | 150% | 400 · 500 |
| Body | 14px | 160% | 400 · 500 · 600 |
| Caption,Meta M | 12px | 130% | 400 · 500 · 600 |
| Caption,Meta S | 10px | 130% | 400 · 500 |

> CSS Tokens, 컴포넌트별 사용처는 상세 스펙 참고

---

## 2. 색상

> [상세 스펙 →](colors/color-tokens.md)

### Gray (공통)

`gray-100(#FBFBFB)` · `gray-200(#F3F3F3)` · `gray-300(#E3E3E3)` · `gray-400(#D6D6D6)` · `gray-500(#CFCFCF)` · `gray-600(#9F9F9F)` · `gray-700(#6E6E6E)` · `gray-800(#3E3E3E)`

### Semantic (공통)

| Token | HEX | 용도 |
|-------|-----|------|
| `centurion-main-red` | #FF4E51 | 에러, 경고 |
| `centurion-sub-red-100` | #FFEFEF | 에러 연한 배경 |
| `centurion-sub-red-300` | #FFE1E1 | Warning 버튼 배경 |

### Primary (제품별 분기)

| Token | Centurion | Say | Bay | Charty | Watch |
|-------|-----------|-----|-----|--------|-------|
| `color-primary` | #476CFF | #5442FF | #23A0FF | #6F21FF | #2CD1FF |
| `color-primary-100` | #F0F4FF | #F4F5FF | #EFF8FF | #F4F3FF | #EDF9FF |
| `color-primary-300` | #DDE6FF | #E2E7FF | #D7EEFF | #E9E6FF | #D2F2FF |
| `color-primary-500` | #97B5FF | #797CFF | #9ED6FF | #A290FF | #8AE3FF |

> CSS 변수 + `data-product` 기반 분기 전략은 상세 스펙 참고

---

## 3. 공통 컴포넌트 (22개)

> [전체 목록 →](components/_index.md)

### Form Controls (7개)

| 컴포넌트 | 핵심 스펙 | 상세 |
|----------|----------|------|
| **Input** | 높이 50px, Focus → `color-primary` border | [input.md](components/input.md) |
| **Combo** | 단일/복수/검색, 외부 클릭 닫힘 | [combo.md](components/combo.md) |
| **Date Picker** | Single/Range, YYYY.MM.DD 포맷 | [calendar.md](components/calendar.md) |
| **Calendar** | 과거+현재월만, 선택 bg `color-primary` | [calendar.md](components/calendar.md) |
| **Toggle** | ON `color-primary` / OFF `gray-500(#CFCFCF)` | [toggle.md](components/toggle.md) |
| **Checkbox** | Active bg `color-primary` + ✓ 흰색 | [check.md](components/check.md) |
| **Radio** | Active bg `color-primary`, 단일 선택 | [radio.md](components/radio.md) |

### Actions (3개)

| 컴포넌트 | 핵심 스펙 | 상세 |
|----------|----------|------|
| **Button_S** | 취소/확정/취소-레드, Hug Contents | [button.md](components/button.md) |
| **Button_L** | BG_Main/BG_Sub, Progress 지원 | [button.md](components/button.md) |
| **Reset** | 필터 초기화, Active → `color-primary` | [reset.md](components/reset.md) |

### Navigation (4개)

| 컴포넌트 | 핵심 스펙 | 상세 |
|----------|----------|------|
| **Tab** | 균등 배치, Active 하단 바 | [tab.md](components/tab.md) |
| **Pagination** | Active bg `color-primary-100` | [pagination.md](components/pagination.md) |
| **Admin Header** | 좌 메뉴 + 우 정보/액션 | [header.md](components/header.md) |
| **Menu (LNB)** | Active bg `color-primary-100` | [header.md](components/header.md) |

### Feedback (3개)

| 컴포넌트 | 핵심 스펙 | 상세 |
|----------|----------|------|
| **Toast** | 상단 중앙, 2초, L/M 분기 | [toast.md](components/toast.md) |
| **Confirmation Modal** | 버튼으로만 닫힘, Warning/Complete | [modal.md](components/modal.md) |
| **Process Icon** | Normal/Critical/Warning | [process-icon.md](components/process-icon.md) |

### Display (2개)

| 컴포넌트 | 핵심 스펙 | 상세 |
|----------|----------|------|
| **Badge** | Primary/Red/Gray, 12px 500 | [badge-tag.md](components/badge-tag.md) |
| **Tag** | Default/Active, 12px 400 | [badge-tag.md](components/badge-tag.md) |

### Utility (3개)

| 컴포넌트 | 핵심 스펙 | 상세 |
|----------|----------|------|
| **More Menu** | 수정+삭제, 삭제=레드 | [more-menu.md](components/more-menu.md) |
| **Eye** | 비밀번호 마스킹 토글 | [eye.md](components/eye.md) |
| **Footer** | L/M 분기, 회사 정보 | [footer.md](components/footer.md) |

---

## 공통 인터랙션 규칙

| 인터랙션 | 동작 |
|----------|------|
| Focus | border → `color-primary` |
| Hover | bg → `color-primary-100` 또는 `gray-200(#F3F3F3)` |
| Error | border/text → `color-red(#FF4E51)` |
| Modal 닫힘 | 버튼 클릭만 (외부 클릭 불가) |
| Combo/Calendar 닫힘 | 외부 영역 클릭 시 닫힘 |

---
