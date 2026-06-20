# Centurion Suite — Color Tokens

> 공통(Gray, Semantic) + 브랜드별 분기(Primary)

---

## Gray Scale (전 제품 공통)

| Token | HEX | 용도 |
|-------|-----|------|
| `gray-100` | `#FBFBFB` | 배경 (최연한) |
| `gray-200` | `#F3F3F3` | 배경, 구분선 |
| `gray-300` | `#E3E3E3` | 보더, 비활성 구분선 |
| `gray-400` | `#D6D6D6` | 보더 (연한) |
| `gray-500` | `#CFCFCF` | Toggle OFF 배경, 비활성 |
| `gray-600` | `#9F9F9F` | 플레이스홀더, 비활성 아이콘, Read Only 텍스트 |
| `gray-700` | `#6E6E6E` | 보조 텍스트 |
| `gray-800` | `#3E3E3E` | 주요 텍스트 (미선택 메뉴 등) |

### 추가 고정 컬러

| Token | HEX | 용도 |
|-------|-----|------|
| `white` | `#FFFFFF` | 배경, 버튼 텍스트 |
| `black` | `#000000` | 주요 텍스트, 입력 텍스트 |

---

## Purple (전 제품 공통)

| Token | HEX | 용도 |
|-------|-----|------|
| `color-purple` | `#AC47FF` | 보라 포인트 |
| `color-purple-100` | `#F8F0FF` | 보라 연한 배경 |
| `color-purple-500` | `#BF71FF` | 보라 강조 (개선할 점 태그) |

---

## Red (전 제품 공통)

| Token | HEX | 용도 |
|-------|-----|------|
| `color-red` | `#FF4E51` | 에러, Critical, 삭제 |
| `color-red-100` | `#FFEFEF` | 레드 연한 배경 |
| `color-red-300` | `#FFE1E1` | Warning 버튼 배경 |
| `color-red-500` | `#FFC4C5` | 레드 보조 |

---

## Hover (전 제품 공통)

| Token | HEX | 용도 |
|-------|-----|------|
| `color-hover-dark` | `#222943` | 다크 호버 |

---

## Primary 기본값 — Centurion / Day (SSO + 메인 제품)

> SSO 로그인 화면 + Day 제품이 이 색상을 사용합니다.
> 나머지 제품(Say, Bay, Charty, Watch)은 아래에서 오버라이드합니다.

| Token | HEX | 용도 |
|-------|-----|------|
| `centurion-main-blue` | `#476CFF` | 기본 브랜드 블루 |
| `centurion-sub-blue-100` | `#F0F4FF` | 연한 배경 |
| `centurion-sub-blue-300` | `#DDE6FF` | 배경 (중간) |
| `centurion-sub-blue-500` | `#97B5FF` | 강조 보조 |

---

## Primary — Say (제품별 오버라이드)

| Token | HEX | 용도 |
|-------|-----|------|
| `say-main-blue` | `#5442FF` | Say 메인 블루 |
| `say-sub-blue-100` | `#F4F5FF` | 선택 범위 배경, Active 배경 |
| `say-sub-blue-300` | `#E2E7FF` | 중간 배경 |
| `say-sub-blue-500` | `#797CFF` | 호버, 강점 태그 |
| `say-main-orange` | `#FF8064` | 약점 태그 |
| `say-sub-orange-100` | `#FFF8E8` | 오렌지 연한 배경 |

---

## Primary — Bay (제품별 오버라이드)

| Token | HEX | 용도 |
|-------|-----|------|
| `bay-main-blue` | `#23A0FF` | Bay 메인 블루 |
| `bay-sub-blue-50` | `#FBFDFF` | 최연한 배경 |
| `bay-sub-blue-100` | `#EFF8FF` | 연한 배경 |
| `bay-sub-blue-300` | `#D7EEFF` | 중간 배경 |
| `bay-sub-blue-500` | `#9ED6FF` | 강조 보조 |

---

## Primary — Charty (제품별 오버라이드)

| Token | HEX | 용도 |
|-------|-----|------|
| `charty-main` | `#6F21FF` | Charty 메인 Blue Violet |
| `charty-sub-100` | `#F4F3FF` | 연한 배경, Active 배경 |
| `charty-sub-300` | `#E9E6FF` | 중간 배경 |
| `charty-sub-500` | `#A290FF` | 호버, 강조 보조 |

---

## Primary — Watch (제품별 오버라이드)

| Token | HEX | 용도 |
|-------|-----|------|
| `watch-main` | `#2CD1FF` | Watch 메인 시안 |
| `watch-sub-100` | `#EDF9FF` | 연한 배경, Active 배경 |
| `watch-sub-300` | `#D2F2FF` | 중간 배경 |
| `watch-sub-500` | `#8AE3FF` | 호버, 강조 보조 |

---

## Semantic 컬러 매핑 (제품 공통)

| Semantic Token | 참조 | 용도 |
|---------------|------|------|
| `color-primary` | 기본값 `centurion-main-blue`, 제품별 오버라이드 | 메인 액션, Active 상태 |
| `color-primary-100` | 기본값 `centurion-sub-blue-100`, 제품별 오버라이드 | Active 배경, 선택 범위 |
| `color-primary-300` | 기본값 `centurion-sub-blue-300`, 제품별 오버라이드 | 중간 배경 |
| `color-primary-500` | 기본값 `centurion-sub-blue-500`, 제품별 오버라이드 | 호버 상태 |
| `color-red` | `#FF4E51` | 에러, Critical, 삭제 |
| `color-red-100` | `#FFEFEF` | 레드 연한 배경 |
| `color-red-300` | `#FFE1E1` | 레드 중간 배경 |
| `color-red-500` | `#FFC4C5` | 레드 보조 |
| `color-success` | 제품별 `main-blue` | 성공, Complete |

| `color-text-primary` | `#000000` | 주요 텍스트 |
| `color-text-secondary` | `gray-800(#3E3E3E)` | 보조 텍스트 |
| `color-text-disabled` | `gray-600(#9F9F9F)` | 비활성 텍스트 |
| `color-text-placeholder` | `gray-600(#9F9F9F)` | 플레이스홀더 |
| `color-border-default` | `gray-300(#E3E3E3)` | 기본 보더 |
| `color-border-focus` | `color-primary` | 포커스 보더 |
| `color-bg-page` | `#FFFFFF` | 페이지 배경 |
| `color-bg-subtle` | `gray-100(#FBFBFB)` | 약한 배경 |
| `color-bg-muted` | `gray-200(#F3F3F3)` | 중간 배경 |

---

## 브랜드 분기 구현 전략

```css
/* 기본값 — Centurion / Day (SSO + 메인 제품) */
:root {
  --color-primary: #476CFF;
  --color-primary-100: #F0F4FF;
  --color-primary-300: #DDE6FF;
  --color-primary-500: #97B5FF;
}

/* 제품별 오버라이드 — data-product 속성으로 전환 */

[data-product="say"] {
  --color-primary: #5442FF;
  --color-primary-100: #F4F5FF;
  --color-primary-300: #E2E7FF;
  --color-primary-500: #797CFF;
}

[data-product="bay"] {
  --color-primary: #23A0FF;
  --color-primary-100: #EFF8FF;
  --color-primary-300: #D7EEFF;
  --color-primary-500: #9ED6FF;
}

[data-product="charty"] {
  --color-primary: #6F21FF;
  --color-primary-100: #F4F3FF;
  --color-primary-300: #E9E6FF;
  --color-primary-500: #A290FF;
}

[data-product="watch"] {
  --color-primary: #2CD1FF;
  --color-primary-100: #EDF9FF;
  --color-primary-300: #D2F2FF;
  --color-primary-500: #8AE3FF;
}
```

