# Button

---

## Button_S (소형 버튼)

텍스트 내부 버튼. 가로는 **Hug Contents** (텍스트 길이에 따라 유동), 상황에 따라 고정 가로 가능.

> Progress 적용 시 가로 길이가 줄어들 수 있어, 필요 시 width를 고정하는 방식으로 대응 필요.

### 타입 (3종)

**취소 / 확정 / 레드**

### 상태별 스타일

#### 취소

| 상태 | bg | border | text |
|------|-----|--------|------|
| Disabled | `white` | `gray-300(#E3E3E3)` | `gray-600(#9F9F9F)` |
| Active | `white` | `gray-500(#CFCFCF)` | `black` |
| Hover | `gray-100(#FBFBFB)` | `gray-500(#CFCFCF)` | `black` |

#### 확정

| 상태 | bg | border | text |
|------|-----|--------|------|
| Disabled | `white` | `gray-300(#E3E3E3)` | `gray-600(#9F9F9F)` |
| Active | `white` | `color-primary-500` | `color-primary` |
| Hover | `color-primary-100` | `color-primary-500` | `color-primary` |

#### 레드

| 상태 | bg | border | text |
|------|-----|--------|------|
| Disabled | `white` | `gray-300(#E3E3E3)` | `gray-600(#9F9F9F)` |
| Active | `white` | `color-red(#FF4E51)` | `color-red(#FF4E51)` |
| Hover | `color-red-100(#FFEFEF)` | `color-red(#FF4E51)` | `color-red(#FF4E51)` |

### 타이포
- 텍스트: Heading S / Regular (14px, 400, LH 150%)

---

## Button_L (대형 버튼)

- **높이**: 50px 고정
- 좌측/우측 아이콘은 각각 독립적으로 표시하거나 숨길 수 있음

### 배경 타입 (5종)

**Active / BG_Sub / Error_btn / border_btn / Main border_btn**

### 공통 상태

Default · Hover · Disabled · Progress · Progress+Text · Only Icon

- Disabled: 클릭 불가. 필수 조건 충족 시 Default로 전환

---

#### Active (메인 채움)

| 상태 | bg | text |
|------|-----|------|
| Default | `color-primary` | `white` |
| Hover | `color-primary-500` | `white` |
| Disabled | `gray-300(#E3E3E3)` | `white` |
| Progress | `color-primary` | — (스피너) |
| Progress+Text | `color-primary` | `white` |
| Only Icon | `color-primary` | — |

#### BG_Sub (서브 채움)

| 상태 | bg | text |
|------|-----|------|
| Default | `color-primary-100` | `color-primary` |
| Hover | `color-primary-300` | `color-primary` |
| Disabled | `gray-300(#E3E3E3)` | `white` |
| Progress | `color-primary-100` | — (스피너) |
| Progress+Text | `color-primary-100` | `color-primary` |
| Only Icon | `color-primary-100` | — |

#### Error_btn (에러/경고)

| 상태 | bg | text |
|------|-----|------|
| Default | `color-red-100(#FFEFEF)` | `color-red(#FF4E51)` |
| Hover | `color-red-300(#FFE1E1)` | `color-red(#FF4E51)` |

#### border_btn (기본 보더)

| 상태 | bg | border | text |
|------|-----|--------|------|
| Default | `white` | `gray-300(#E3E3E3)` | `black` |
| Hover | `gray-200(#F3F3F3)` | `gray-300(#E3E3E3)` | `black` |
| Disabled | `gray-300(#E3E3E3)` | — | `white` |
| Progress | `white` | `gray-300(#E3E3E3)` | — (스피너) |
| Progress+Text | `white` | `gray-300(#E3E3E3)` | `black` |
| Only Icon | `white` | `gray-300(#E3E3E3)` | — |

#### Main border_btn (프라이머리 보더)

| 상태 | bg | border | text |
|------|-----|--------|------|
| Default | `white` | `color-primary-500` | `color-primary` |
| Hover | `color-primary-100` | `color-primary-500` | `color-primary` |
| Disabled | `gray-300(#E3E3E3)` | — | `white` |
| Progress | `white` | `color-primary-500` | — (스피너) |
| Progress+Text | `white` | `color-primary-500` | `color-primary` |
| Only Icon | `white` | `color-primary-500` | — |

### 아이콘 색상 규칙
- 아이콘 색상은 해당 상태의 **text 색상과 동일**하게 적용
- Only Icon 상태: 텍스트 없이 아이콘만 표시

### 타이포
- 텍스트: Heading M / Medium (16px, 500, LH 150%)

---

*버튼 내부 텍스트는 사용 맥락에 따라 가변적으로 변경*
