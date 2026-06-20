# Pagination

---

## 구성 요소

| 요소 | 설명 |
|------|------|
| **< 이전** | 이전 페이지 이동. 첫 페이지에서 Disabled |
| **페이지 번호** | Active 페이지 강조, 일반 페이지 숫자 |
| **…** | 중간 페이지 생략 표시 |
| **다음 >** | 다음 페이지 이동. 마지막 페이지에서 Disabled |

---

## 상태별 스타일

| 요소 | 상태 | bg | text |
|------|------|-----|------|
| 화살표 | Active | — | `color-primary` |
| 화살표 | Disabled | — | `gray-300(#E3E3E3)` |
| 현재 페이지 | Active | `color-primary-100` | `color-primary` |
| 일반 페이지 | Default | `white` | `gray-600(#9F9F9F)` |

---

## 타이포
- 페이지 번호: Heading M / Medium (16px, 500, LH 150%)
