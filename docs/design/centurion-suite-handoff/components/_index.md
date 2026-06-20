# Centurion Suite — Component Index

> 공통 컴포넌트 22개

---

## 컴포넌트 전체 목록

### Form Controls (입력)

| # | 컴포넌트 | 상태 수 | 문서 |
|---|----------|---------|------|
| 1 | **Input** | Default / Focus / Error / ReadOnly | [input.md](input.md) |
| 2 | **Combo** | Default / Select / Search / Multi | [combo.md](combo.md) |
| 3 | **Date Picker** | Single / Range / Focus | [calendar.md](calendar.md) |
| 4 | **Calendar** | Default / Hover / Selected / Range | [calendar.md](calendar.md) |
| 5 | **Toggle** | ON(노출) / OFF(미노출) | [toggle.md](toggle.md) |
| 6 | **Checkbox** | Default / Active / Disable | [check.md](check.md) |
| 7 | **Radio** | Default / Active | [radio.md](radio.md) |

### Actions (액션)

| # | 컴포넌트 | 상태 수 | 문서 |
|---|----------|---------|------|
| 8 | **Button_S** | 취소 / 확정 / 취소-레드 × Disable, Active, Hover | [button.md](button.md) |
| 9 | **Button_L** | Default / Hover / Disabled / Progress / OnlyIcon | [button.md](button.md) |
| 10 | **Reset** | Default / Hover / Active | [reset.md](reset.md) |

### Navigation (네비게이션)

| # | 컴포넌트 | 상태 수 | 문서 |
|---|----------|---------|------|
| 11 | **Tab** | Active / Inactive | [tab.md](tab.md) |
| 12 | **Pagination** | Active Page / Normal / Ellipsis | [pagination.md](pagination.md) |
| 13 | **Admin Header** | 좌 메뉴 탭 + 우 정보/액션 단일 행 | [header.md](header.md) |
| 14 | **Menu (LNB)** | 펼침 / 접힘 / 사이드바 축소 | [header.md](header.md) |

### Feedback (피드백)

| # | 컴포넌트 | 상태 수 | 문서 |
|---|----------|---------|------|
| 15 | **Toast** | Complete / Error × PC / MO | [toast.md](toast.md) |
| 16 | **Confirmation Modal** | Warning / Complete | [modal.md](modal.md) |
| 17 | **Process Icon** | Normal / Critical / Warning | [process-icon.md](process-icon.md) |

### Display (표시)

| # | 컴포넌트 | 상태 수 | 문서 |
|---|----------|---------|------|
| 18 | **Badge** | Gray / Pink / Purple | [badge-tag.md](badge-tag.md) |
| 19 | **Tag** | Primary / Purple / Red / Gray | [badge-tag.md](badge-tag.md) |

### Utility (유틸리티)

| # | 컴포넌트 | 상태 수 | 문서 |
|---|----------|---------|------|
| 20 | **More Menu** | Menu / Hover / Menu_단일 | [more-menu.md](more-menu.md) |
| 21 | **Eye (비밀번호)** | Open / Close | [eye.md](eye.md) |
| 22 | **Footer** | PC / MO | [footer.md](footer.md) |

---

## 공통 디자인 규칙

### 컬러 참조
- **Active/Focus 보더**: `color-primary`
- **Default 보더**: `gray-300(#E3E3E3)`
- **Active 배경**: `color-primary-100`
- **Hover**: `color-primary-500`
- **비활성 텍스트**: `gray-600(#9F9F9F)`
- **에러/삭제**: `color-red(#FF4E51)`

### 공통 인터랙션
- **닫힘 방식 (Modal)**: 버튼 클릭으로만 닫힘, 외부 클릭 불가
- **닫힘 방식 (Combo/Calendar)**: 외부 영역 클릭 시 닫힘
- **Focus 표시**: 보더 컬러 → `color-primary` 전환
- **Hover 표시**: 배경 → `color-primary-100` 또는 `gray-200(#F3F3F3)`

