# Calendar & Date Picker

---

## Date Picker

날짜를 선택하기 위한 인풋 컴포넌트. **Single** / **Range** 두 가지 타입.

### 상태별 스타일

| 상태 | bg | border | text |
|------|-----|--------|------|
| Default | `white` | `gray-300(#E3E3E3)` | `black` |
| Focus | `white` | `color-primary` | `black` |

- 우측 캘린더 아이콘: `gray-600(#9F9F9F)`
- 날짜 포맷: `YYYY.MM.DD`

### Single 모드
- 단일 날짜 선택
- 날짜 텍스트 영역: **w74 고정**, 좌측 정렬
- w74 초과 시 최대 **w116**까지 확장, 우측 아이콘과 **6px** 간격 유지

### Range 모드
- 기간(시작일 ~ 종료일) 선택
- 표시: `YYYY.MM.DD ~ YYYY.MM.DD`
- 기본값: 최근 1주 자동 입력
- 플레이스홀더: `시작일 선택 ~ 종료일 선택` (`gray-600(#9F9F9F)`)
- 세부 영역 너비: 시작일 **w74** / 물결(~) **w12** / 종료일 **w74**

---

## Calendar (달력 모달)

Date Picker 클릭 시 노출되는 날짜 선택 모달.

### 기본 구성
- bg: `white`
- 1개월 달력 표시
- 초기 표시 월: 선택된 날짜(Single) 또는 종료일(Range)이 속한 월
- 월 타이틀: `gray-800(#3E3E3E)` — Heading S / Medium (14px, 500)
- 요일 라벨: `gray-600(#9F9F9F)` — Caption,Meta S / Medium (10px, 500)

### 월 이동 Chevron

| 상태 | icon |
|------|------|
| 활성 | `color-primary` |
| 비활성 | `gray-600(#9F9F9F)` |

- **과거 월 + 현재 월**만 이동 가능

### 날짜 상태별 스타일

| 상태 | bg | text |
|------|-----|------|
| 선택 가능 | — | `gray-800(#3E3E3E)` |
| 오늘 | — | `color-primary` |
| 선택됨 | `color-primary` | `white` |
| Hover | `gray-200(#F3F3F3)` | — |
| 미래 날짜 | — | `gray-300(#E3E3E3)` (선택 불가) |
| 이전달 일자 | — | `gray-600(#9F9F9F)` (선택 가능, 클릭 시 이전달 화면으로 이동) |
| Range 사이 | `color-primary-100` | `gray-800(#3E3E3E)` |

### 시간 선택 (time variant)

| 상태 | bg | border | text |
|------|-----|--------|------|
| Default | `white` | `gray-300(#E3E3E3)` | `black` |
| Selected | `color-primary` | — | `white` |
| Disabled | `white` | `gray-300(#E3E3E3)` | `gray-500(#CFCFCF)` |
| Hover | `gray-200(#F3F3F3)` | — | `black` |

### 모달 닫힘
- 외부 영역 클릭 → 모달 닫힘, 변경 미적용
- Range: 시작일만 선택 후 닫기 → 이전 기간 값 복귀

### Range 선택 플로우
1. 첫 번째 클릭 → 시작일 지정, 필드 텍스트: `YYYY.MM.DD ~ 종료일 선택`
2. 두 번째 클릭 → 종료일 지정, 모달 닫힘
3. 시작일~종료일 사이: `color-primary-100` 연속 표시
4. **예외**: 종료일이 시작일보다 이전 → 시작일 재설정, 모달 유지
