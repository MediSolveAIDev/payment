# Admin Header & Menu (LNB)

---

## Admin Header

```
┌──────────────────────────────────────────────────────────────────┐
│ [메뉴1] [메뉴2*] [메뉴3] ...          HH:MM:SS  병원명  사용자명  로그아웃 │
└──────────────────────────────────────────────────────────────────┘
```

- bg: `white`
- 하단 구분선 (콘텐츠와): `gray-200(#F3F3F3)`

---

### 메뉴 탭 (좌측)

| 상태 | text | weight |
|------|------|--------|
| Default | `gray-800(#3E3E3E)` | Regular (400) |
| Active | `color-primary` | Medium (500) |
| Hover | `color-primary-500` | Regular (400) |

- 타이포: 16px

---

### 우측 — 정보 & 액션

| 요소 | 타이포 | text |
|------|--------|------|
| 현재 시간 | Heading S / Regular (14px, 400) | `black` |
| 병원명 | Heading S / Regular (14px, 400) | `gray-700(#6E6E6E)` |
| 사용자명 | Heading S / Regular (14px, 400) | `color-primary` |
| 로그아웃 | Heading S / Regular (14px, 400) | Default: `gray-700(#6E6E6E)`, Hover: `color-primary-500` |

---

## Menu (LNB)

### 변형

| 변형 | 설명 |
|------|------|
| 펼침 (Expanded) | 카테고리명 + 메뉴 아이템 텍스트 표시 |
| 접힘 (Collapsed) | 카테고리명 + 아이콘만 표시 |
| 사이드바 축소 (Sidebar Collapsed) | 아이콘만 표시 (텍스트 없음) |

---

### 카테고리 헤더

| 요소 | text |
|------|------|
| 카테고리명 | `gray-600(#9F9F9F)` |

- 타이포: Heading S / Regular (14px, 400)

### 메뉴 아이템 상태별 스타일

| 상태 | bg | text | icon |
|------|-----|------|------|
| Default | `white` | `gray-700(#6E6E6E)` | `gray-600(#9F9F9F)` |
| Active | `color-primary-100` | `color-primary` | `color-primary` |
| Hover | `color-primary-100` | `color-primary` | `color-primary` |

- 타이포: Heading M (16px) — Default Regular (400), Active Medium (500)
- 사이드바 bg: `white`
- Chevron (열림): `color-primary`
- Chevron (닫힘): `gray-300(#E3E3E3)`

### 사이드바 축소 (Sidebar Collapsed)

아이콘만 표시. 텍스트 미노출.

| 상태 | bg | icon |
|------|-----|------|
| Default | `white` | `gray-600(#9F9F9F)` |
| Active | `color-primary-100` | `color-primary` |

#### 호버 툴팁

| 요소 | 값 |
|------|-----|
| bg | `color-hover-dark(#222943)` |
| text | `white` |
| 타이포 | Caption,Meta M / Regular (12px, 400) |
