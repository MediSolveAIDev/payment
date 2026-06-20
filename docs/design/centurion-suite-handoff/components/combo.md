# Combo

---

## 개요

단일 선택 / 복수 선택 / 검색 포함 세 가지 모드.

### 사이즈
- **기본 높이**: 50px

---

## 인풋 상태별 스타일

| 상태 | bg | border | text |
|------|-----|--------|------|
| Default | `white` | `gray-300(#E3E3E3)` | `gray-600(#9F9F9F)` (placeholder) |
| Active | `white` | `color-primary` | `black` |
| Focus | `white` | `color-primary` | `black` |

### 아이콘

| 아이콘 | 위치 | 색상 | 비고 |
|--------|------|------|------|
| Chevron (▾) | 인풋 우측 | `gray-600(#9F9F9F)` | 열림 시 180° 회전 |

---

## 드롭다운 패널

| 요소 | bg | text/icon |
|------|-----|-----------|
| 패널 외곽 | `color-primary-100` | — |

### 아이템 상태

| 상태 | bg | text | icon |
|------|-----|------|------|
| Default | `white` | `black` | — |
| Hover | `gray-100(#FBFBFB)` | `black` | — |
| Selected | `color-primary-100` | `color-primary` | ✓ (`color-primary`) |

---

## 검색 모드 (combo)

드롭다운 상단에 검색 인풋 노출.

| 요소 | bg | border | text/icon |
|------|-----|--------|-----------|
| 검색 인풋 bg | `white` | — | — |
| 검색 placeholder | — | — | `gray-600(#9F9F9F)` |
| Search 아이콘 (🔍) | — | — | `gray-600(#9F9F9F)` |
| 검색 인풋 하단 구분선 | — | `color-primary-100` | — |

- 타이핑 중 검색 아이콘이 `color-primary`로 변경
- 검색어 입력 시 아이템 실시간 필터링

---

## 모드

### 단일 선택 (select)
- 하나의 항목만 선택 가능
- 선택 시 인풋에 항목명 표시

### 복수 선택 (select)
- 리스트에서 복수 선택 가능
- 인풋에 선택 항목명 콤마(,) 구분 표시
- 선택된 아이템에 Check 아이콘 표시

### 검색 포함 (combo)
- 드롭다운 상단에 검색 인풋 + Search 아이콘 노출
- 검색어 입력 시 실시간 필터링

---

## 인터랙션
- 외부 영역 클릭 시 닫힘

## 타이포
- 인풋/아이템 텍스트: Heading S / Regular (14px, 400, LH 150%)
- 선택된 아이템 텍스트: Heading S / Medium (14px, 500, LH 150%)
