# Confirmation Modal

---

## 닫힘 규칙

**버튼 클릭으로만 닫힘** — 외부 영역 클릭 시 닫히지 않음

## 구조

```
┌─────────────────────────┐
│      [상태 아이콘]       │
│                         │
│     타이틀 텍스트        │
│     설명 텍스트          │
│                         │
│ ██████████████████████  │  ← 버튼 영역 (배경 스트립)
│ ██ [취소]   [확인] ████ │
│ ██████████████████████  │
└─────────────────────────┘
```

- 모달 bg: `white`

---

## 타입별 스타일

### Warning

| 요소 | bg | border | text/icon |
|------|-----|--------|-----------|
| 상태 아이콘 bg | `color-red-300(#FFE1E1)` | — | `color-red(#FF4E51)` (!) |
| 버튼 영역 배경 | `color-red-300(#FFE1E1)` | — | — |
| 취소 버튼 | `white` | `gray-300(#E3E3E3)` | `black` |
| 취소 버튼 Hover | `gray-200(#F3F3F3)` | `gray-300(#E3E3E3)` | `black` |
| 확인 버튼 | — | — | `color-red(#FF4E51)` |
| 확인 버튼 Hover | `color-red-300(#FFE1E1)` | — | `color-red(#FF4E51)` |

### Complete

| 요소 | bg | border | text/icon |
|------|-----|--------|-----------|
| 상태 아이콘 | `color-primary` | — | `white` (✓) |
| 버튼 영역 배경 | `color-primary-100` | — | — |
| 취소 버튼 | `white` | `gray-300(#E3E3E3)` | `black` |
| 취소 버튼 Hover | `gray-200(#F3F3F3)` | `gray-300(#E3E3E3)` | `black` |
| 확인 버튼 | — | — | `color-primary` |
| 확인 버튼 Hover | `color-primary-300` | — | `color-primary` |

---

## 타이포
- 타이틀: Title S / SemiBold (18px, 600)
- 설명: Heading M / Regular (16px, 400)
- 버튼 텍스트: Heading M / Medium (16px, 500)

## 참고
- 버튼 텍스트는 상황에 따라 변경 가능
- 상태 아이콘은 `Process Icon` 컴포넌트 사용
- 버튼 영역은 개별 버튼 bg가 아닌, 영역 전체에 색상 배경이 깔림
