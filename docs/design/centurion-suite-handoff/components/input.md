# Input

---

## 개요

공통 텍스트 인풋 박스 컴포넌트.

### 사이즈
- **기본 높이**: 50px (한 줄 입력용)
- **긴 글 입력**: 가로·세로 사용처에 맞게 자유 조정

---

## 상태별 스타일

| 상태 | bg | border | text |
|------|-----|--------|------|
| Default | `white` | `gray-300(#E3E3E3)` | `gray-600(#9F9F9F)` (placeholder) |
| Focus | `white` | `color-primary` | `black` |
| Read Only | `white` | `gray-300(#E3E3E3)` | `gray-600(#9F9F9F)` |
| Error | `white` | `color-red(#FF4E51)` | `black` |

- 유효한 값으로 재입력 시 Error 상태가 해제되고, 테두리가 Focus 컬러(`color-primary`)로 복귀

---

## 우측 아이콘

| 아이콘 | 색상 | 조건 | 동작 |
|--------|------|------|------|
| **삭제 (X)** | bg `gray-300(#E3E3E3)`, icon `white` | 텍스트가 입력된 상태에서만 노출 | 클릭 시 전체 삭제, 비어있으면 미노출 |
| **비밀번호 (Eye)** | `gray-600(#9F9F9F)` | 비밀번호 인풋에서만 노출 | 클릭 시 마스킹/표시 토글, 기본: 마스킹(●●●●●●) |

---

## 타이포
- 입력 텍스트: Heading S / Regular (14px, 400, LH 150%)
