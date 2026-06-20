# Centurion Suite — Typography System

> 공통 타이포그래피 정의
> Font: **Pretendard** (전 제품 공통)

---

## 기본 정보

| 항목 | 값 |
|------|-----|
| **폰트** | Pretendard (Centurion Suite 공통) |
| **웨이트** | Regular (400) · Medium (500) · SemiBold (600) |
| **사이즈 스케일** | 10 — 12 — 14 — 16 — 18 — 20 — 24 — 32 — 40 |
| **Letter Spacing** | 0px (전 스타일 동일) |

> Bold (700)는 제품 UI에서 미사용

---

## Line Height 시스템

> **사이즈 그룹별 개별 LH** 적용

| 사이즈 그룹 | Size | LH |
|-------------|------|-----|
| Headline L/M | 40 · 32 | **130%** |
| Title L/M | 24 · 20 | **140%** |
| Title S · Heading M · Heading S | 18 · 16 · 14 | **150%** |
| Body | 14 | **160%** |
| Caption,Meta M/S | 12 · 10 | **130%** |

---

## 전체 타이포 스타일 (28개: 10 카테고리 × 3 웨이트)

### Headline (6개)

| # | Style | Size | Weight | LH |
|---|-------|------|--------|-----|
| 1 | Headline L / SemiBold | 40 | 600 | 130% |
| 2 | Headline L / Medium | 40 | 500 | 130% |
| 3 | Headline L / Regular | 40 | 400 | 130% |
| 4 | Headline M / SemiBold | 32 | 600 | 130% |
| 5 | Headline M / Medium | 32 | 500 | 130% |
| 6 | Headline M / Regular | 32 | 400 | 130% |

### Title (9개)

| # | Style | Size | Weight | LH |
|---|-------|------|--------|-----|
| 7 | Title L / SemiBold | 24 | 600 | 140% |
| 8 | Title L / Medium | 24 | 500 | 140% |
| 9 | Title L / Regular | 24 | 400 | 140% |
| 10 | Title M / SemiBold | 20 | 600 | 140% |
| 11 | Title M / Medium | 20 | 500 | 140% |
| 12 | Title M / Regular | 20 | 400 | 140% |
| 13 | Title S / SemiBold | 18 | 600 | 150% |
| 14 | Title S / Medium | 18 | 500 | 150% |
| 15 | Title S / Regular | 18 | 400 | 150% |

### Heading (5개)

| # | Style | Size | Weight | LH |
|---|-------|------|--------|-----|
| 16 | Heading M / SemiBold | 16 | 600 | 150% |
| 17 | Heading M / Medium | 16 | 500 | 150% |
| 18 | Heading M / Regular | 16 | 400 | 150% |
| 19 | Heading S / Medium | 14 | 500 | 150% |
| 20 | Heading S / Regular | 14 | 400 | 150% |

> Heading S는 SemiBold(600) 없이 Medium(500)/Regular(400) 2웨이트만 존재

### Body (3개)

| # | Style | Size | Weight | LH |
|---|-------|------|--------|-----|
| 21 | Body / SemiBold | 14 | 600 | 160% |
| 22 | Body / Medium | 14 | 500 | 160% |
| 23 | Body / Regular | 14 | 400 | 160% |

### Caption · Meta (5개)

| # | Style | Size | Weight | LH |
|---|-------|------|--------|-----|
| 24 | Caption,Meta M / SemiBold | 12 | 600 | 130% |
| 25 | Caption,Meta M / Medium | 12 | 500 | 130% |
| 26 | Caption,Meta M / Regular | 12 | 400 | 130% |
| 27 | Caption,Meta S / Medium | 10 | 500 | 130% |
| 28 | Caption,Meta S / Regular | 10 | 400 | 130% |

> Caption,Meta S는 Medium(500)/Regular(400) 2웨이트만 존재

---

## CSS Tokens

```css
/* Centurion Suite 공통 — 사이즈별 LH */

/* Headline (LH 130%) */
--typo-headline-lg-600: 600 40px/1.3 'Pretendard';
--typo-headline-lg-500: 500 40px/1.3 'Pretendard';
--typo-headline-lg-400: 400 40px/1.3 'Pretendard';
--typo-headline-md-600: 600 32px/1.3 'Pretendard';
--typo-headline-md-500: 500 32px/1.3 'Pretendard';
--typo-headline-md-400: 400 32px/1.3 'Pretendard';

/* Title (LH 140%) */
--typo-title-lg-600: 600 24px/1.4 'Pretendard';
--typo-title-lg-500: 500 24px/1.4 'Pretendard';
--typo-title-lg-400: 400 24px/1.4 'Pretendard';
--typo-title-md-600: 600 20px/1.4 'Pretendard';
--typo-title-md-500: 500 20px/1.4 'Pretendard';
--typo-title-md-400: 400 20px/1.4 'Pretendard';
--typo-title-sm-600: 600 18px/1.5 'Pretendard';
--typo-title-sm-500: 500 18px/1.5 'Pretendard';
--typo-title-sm-400: 400 18px/1.5 'Pretendard';

/* Heading (LH 150%) */
--typo-heading-md-600: 600 16px/1.5 'Pretendard';
--typo-heading-md-500: 500 16px/1.5 'Pretendard';
--typo-heading-md-400: 400 16px/1.5 'Pretendard';
--typo-heading-sm-500: 500 14px/1.5 'Pretendard';
--typo-heading-sm-400: 400 14px/1.5 'Pretendard';

/* Body (LH 160%) */
--typo-body-600: 600 14px/1.6 'Pretendard';
--typo-body-500: 500 14px/1.6 'Pretendard';
--typo-body-400: 400 14px/1.6 'Pretendard';

/* Caption,Meta (LH 130%) */
--typo-caption-md-600: 600 12px/1.3 'Pretendard';
--typo-caption-md-500: 500 12px/1.3 'Pretendard';
--typo-caption-md-400: 400 12px/1.3 'Pretendard';
--typo-meta-sm-500: 500 10px/1.3 'Pretendard';
--typo-meta-sm-400: 400 10px/1.3 'Pretendard';
```

---
