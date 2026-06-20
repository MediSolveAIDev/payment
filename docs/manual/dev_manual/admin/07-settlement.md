# 07. 정산 화면

> 관련 기능 내부 처리 흐름 → [../10-settlement.md](../10-settlement.md)

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

**정산 화면**은 기간·서비스·요금제 필터로 **DONE 상태 결제(승인 완료)** 만 서비스별로 합산하고, 구독 매출과 일반 매출을 분리 표시하는 **조회 전용** 화면입니다. DB에 아무것도 쓰지 않습니다.

| 항목 | 내용 |
|------|------|
| 접근 경로 | 좌측 사이드바 → **정산** 메뉴 (`/admin/settlement`) |
| 엔드포인트 | `GET /admin/settlement` (화면), `GET /admin/settlement/export.xlsx` (엑셀) |
| 필요 권한 | **SYSTEM_ADMIN** 또는 **SERVICE_MANAGER** 로그인 (`require_any`, `app/admin/deps.py:102`) |
| SYSTEM_ADMIN | 등록된 모든 서비스 정산 조회 가능 |
| SERVICE_MANAGER | `ctx.service_ids`(담당 서비스 목록)에 속한 서비스만 조회 가능. 타 서비스는 목록에 노출되지 않으며 직접 URL 지정 시 **404** 반환 |

---

## 2. 화면 구성 — 무엇이 보이나

화면은 크게 **필터 툴바 → 정산 요약(`.settle-summary`) → 목록 테이블** 세 영역으로 나뉩니다.

### 2-1. 필터 툴바 (`settlement/index.html:9-39`)

| 컨트롤 | 파라미터 | 설명 |
|--------|---------|------|
| 월 선택 피커 `<input type="month">` | (없음—서버 미전송) | 달을 고르면 JS가 `from`/`to`를 1일~말일로 채우고 폼을 자동 제출. `name` 속성이 없으므로 서버로는 전송되지 않음 (`index.html:11-17`) |
| 시작일 `<input type="date" name="from">` | `from` | 기간 시작. 변경 시 `requestSubmit()` 자동 조회 |
| 종료일 `<input type="date" name="to">` | `to` | 기간 종료. 변경 시 자동 조회 |
| 서비스 선택 `<select name="service_id">` | `service_id` | 서비스를 고르면 **서비스별 모드**로 전환(건별 목록 표시). 서비스 변경 시 요금제 필터를 초기화(`plan_name.value=''`)하고 자동 조회 (`index.html:22`) |
| 요금제 선택 `<select name="plan_name">` | `plan_name` | 특정 요금제 이름으로 결제 범위 한정. 서비스 선택 시 해당 서비스의 요금제만 옵션에 표시 |
| **조회** 버튼 | — | 폼 제출 |
| **초기화** 링크 | — | `/admin/settlement`로 이동(파라미터 전부 제거, 당월 기본값으로 복귀) |
| **엑셀** 버튼 | — | `/admin/settlement/export.xlsx?from=...&to=...&service_id=...&plan_name=...` 다운로드 |

> **기간 기본값**: `from`/`to` 파라미터가 모두 없으면 **당월 1일~오늘**이 자동으로 설정됩니다 (`app/admin/routes/settlement.py:76-79`).

### 2-2. 정산 요약 (`.settle-summary`, `index.html:42-62`)

카드 형식 대신 **정보 전달 중심** 레이아웃으로, 강조 합계와 구독/일반 분해 소계를 함께 표시합니다.

| 영역 | 표시 내용 | 값 출처 |
|------|-----------|---------|
| `.settle-total` | **순매출**(강조) + 총 건수 | `net_total`(= `total_amount − refund_total`), `total_count` |
| `.settle-breakdown` 총매출 행 | 총 건수 + 총매출(승인 원금 합) | `total_amount` |
| `.settle-breakdown` 환불 행 | 취소 환불 합(−빨강) | `refund_total` — rows의 `refund_amount` 합산 |
| `.settle-breakdown` 구독 매출 행 | 구독 건수(흐리게) + 구독 금액 | `sub_count`, `sub_total` |
| `.settle-breakdown` 일반결제 매출 행 | 일반 건수(흐리게) + 일반 금액 | `one_off_count`, `one_off_total` |

- 서비스를 선택한 경우: "**{서비스명} 순매출**"으로 레이블 변경
- 서비스 미선택(전체 모드): "**전체 순매출**"
- 이 요약 블록은 **전체 모드/서비스별 모드 모두에서 항상 표시**됩니다

### 2-3. 전체 모드 테이블 (`index.html`)

서비스(`service_id` 미지정)일 때 표시되는 **서비스별 합계 테이블**입니다.

| 컬럼 | 내용 |
|------|------|
| 서비스 | 서비스 이름 |
| 건수 | 정산 대상 건수(DONE + CANCELED) |
| 구독 | 구독 결제 합계 금액 (흐리게 표시) |
| 일반 | 일반 결제 합계 금액 (흐리게 표시) |
| 총매출 | 승인 원금 합 |
| 환불 | 취소 환불 합(−빨강) |
| 순매출 | 총매출 − 환불 (굵게) |
| (액션) | **상세보기** 버튼 → `?from=...&to=...&service_id={id}` 링크로 서비스별 모드 진입 |

행 순서: **금액 내림차순**, 같으면 서비스 이름 알파벳순 (`app/services/settlement.py:45`).

### 2-4. 서비스별 모드 테이블 (`index.html:87-113`)

서비스를 선택하면 해당 서비스의 **결제 건별 페이지네이션 목록**이 표시됩니다.

| 컬럼 | 내용 |
|------|------|
| 승인시각 | `approved_at` KST 변환 표시 (정렬 가능 ▲▼) |
| 사용자 | `external_user_id` (없으면 `-`) |
| 주문번호 | `order_id` (고정폭 폰트) |
| 유형 | `payment_type` (뱃지) |
| 종류 | `SUBSCRIPTION`→"구독", `ONE_OFF`→"일반" (뱃지) |
| 금액 | `amount` 원 (굵게, 정렬 가능 ▲▼) |
| (액션) | 구독 결제(`sub` 있음) → `/admin/subscriptions/{id}` 상세, 일반 결제 → `/admin/payments/{id}` 상세 |

- 페이지당 15건 기본 (`app/admin/pagination.py:18`)
- 빈 결과 시: "기간 내 정산 대상이 없습니다" (`index.html:108`)

---

## 3. 할 수 있는 동작

### 3-1. 기간 필터링

1. **월 선택 피커**에서 월을 선택하면 `from`=해당 월 1일, `to`=말일이 자동 입력되고 즉시 조회됩니다.
2. **시작일/종료일**을 직접 입력해도 됩니다 (날짜 선택 즉시 자동 조회).
3. 한쪽만 입력하면 단방향 무제한 범위가 됩니다.

> **주의**: `to` 날짜는 **반개구간**으로 처리됩니다. `to=2026-05-31`이면 실제 조건은 `approved_at < 2026-06-01 00:00:00 UTC`입니다 (`app/admin/pagination.py:144`).

### 3-2. 서비스 선택 (전체 모드 ↔ 서비스별 모드)

- **서비스 선택 안함**: 전체 모드 — 스코프 내 모든 서비스의 요약 테이블 표시
- **서비스 선택**: 서비스별 모드 — 해당 서비스의 결제 건별 목록 표시
- 전체 모드 테이블의 **상세보기** 버튼으로도 서비스별 모드 진입 가능

### 3-3. 요금제 필터링

- 요금제를 선택하면 해당 요금제명의 구독에 연결된 결제만 집계됩니다.
- plan_name 필터 적용 시 단건(일반) 결제는 자동으로 제외됩니다(Plan이 없으므로 JOIN에서 탈락, `app/admin/routes/settlement.py:49-51`).
- 서비스를 선택한 상태에서는 그 서비스의 요금제만 드롭다운에 표시됩니다 (`app/admin/filters.py:27-29`).

### 3-4. 정렬 (서비스별 모드)

- **승인시각** 컬럼 헤더 클릭: 승인 시각 기준 오름차순/내림차순 정렬
- **금액** 컬럼 헤더 클릭: 금액 기준 오름차순/내림차순 정렬
- 정렬 상태는 `sort`, `dir` 파라미터로 유지됩니다 (`index.html:33`)

### 3-5. 엑셀 내보내기

툴바의 **엑셀** 버튼을 클릭하면 현재 필터 조건(기간·서비스·요금제) 그대로 `.xlsx` 파일을 다운로드합니다.

| 모드 | 파일명 | 컬럼 |
|------|--------|------|
| 전체 모드 | `settlement-{날짜시간}.xlsx` | 서비스 / 건수 / 구독매출 / 일반매출 / 총매출 / 환불 / 순매출 |
| 서비스별 모드 | `settlement-{서비스명}-{날짜시간}.xlsx` | 승인시각 / 사용자 / 주문번호 / 유형 / 종류 / 상태 / 총매출 / 환불 / 순매출 |

- 전체 모드 엑셀은 페이지네이션 없이 **전체 행** 출력
- 시각은 KST 문자열로 포맷 (`app/admin/routes/settlement.py:187`)
- 파일명 날짜시간은 KST 기준 (`app/admin/export.py:36`)

---

## 4. 개발 참조

### 4-1. 라우트 함수

| 함수 | 파일:줄 | HTTP | 설명 |
|------|---------|------|------|
| `settlement_view` | `app/admin/routes/settlement.py:98-157` | `GET /admin/settlement` | 화면 조회 |
| `settlement_export` | `app/admin/routes/settlement.py:160-200` | `GET /admin/settlement/export.xlsx` | 엑셀 다운로드 |

### 4-2. 내부 헬퍼 함수

| 함수 | 파일:줄 | 역할 |
|------|---------|------|
| `_settlement_context` | `app/admin/routes/settlement.py:59-95` | 기간·스코프·선택 서비스 판정. 두 엔드포인트 공용 |
| `_settlement_payment_query` | `app/admin/routes/settlement.py:35-56` | 서비스별 모드 결제 건별 base 쿼리 (정렬 미적용). 화면/엑셀 공용 |
| `settlement_summary` | `app/services/settlement.py:25-58` | 서비스별 GROUP BY 집계 — `(총 건수, 총 금액, List[SettlementRow])` 반환 |

### 4-3. 집계 서비스: `settlement_summary`

`app/services/settlement.py:25-58`

- **정산 대상**: `Payment.status == PaymentStatus.DONE` + `approved_at` 반개구간 필터 + 스코프 필터
- 단일 SQL 쿼리에서 `CASE WHEN kind=SUBSCRIPTION`으로 구독/일반을 동시에 집계 (`settlement.py:31-38`)
- 결과 `SettlementRow` 필드: `service_id`, `service_name`, `count`, `amount`, `sub_amount`, `one_off_amount`, `sub_count`, `one_off_count` (`settlement.py:13-22`)
- 반환 후 `settlement_view`에서 `sum(r.sub_amount for r in rows)` 등으로 4개 소계를 파이썬 레벨에서 재집계 (`settlement.py:130-133`)

### 4-4. 스코프 처리

- `ctx.service_ids` (`app/admin/deps.py:57`): SYSTEM_ADMIN이면 `None`(전체), SERVICE_MANAGER이면 담당 서비스 UUID 목록
- `settlement_summary`에서 `scope is not None`이면 `Payment.service_id.in_(scope)` 조건 추가 (`services/settlement.py:54-55`)
- SERVICE_MANAGER가 담당하지 않는 `service_id`를 직접 지정하면 **404** (403 아님 — 서비스 존재 여부 미노출, `settlement.py:90-91`)

### 4-5. 기간 파싱: `date_range`

`app/admin/pagination.py:128-144`

- `YYYY-MM-DD` 문자열 → UTC `datetime` 변환
- `end`는 **익일 0시**로 올림 → 반개구간 `[start, end)` 구현 (`pagination.py:144`)
- 형식 오류 키는 `pp.filters`에서 조용히 제거 (링크 오염 방지)

### 4-6. 엑셀 생성: `xlsx_response`

`app/admin/export.py:23-41`

- `openpyxl` write-only 모드로 메모리 효율을 높임 (`export.py:29`)
- `xlsx_safe(value)`: `=`, `+`, `-`, `@` 로 시작하는 문자열 셀에 `'` 프리픽스 추가(수식 주입 방어, `export.py:16-19`)
- 파일명은 RFC 5987 방식 (`filename*=UTF-8''...`) + ASCII fallback 병행 (`export.py:37-39`)

### 4-7. 월 선택 피커 (JS 전용)

`app/admin/templates/settlement/index.html:10-17`

```html
<input type="month" data-settle-month value="{{ from_filter[:7] }}"
       onchange="if(this.value){var p=this.value.split('-'),f=this.form;
                 f.from.value=this.value+'-01';
                 f.to.value=this.value+'-'+String(new Date(+p[0],+p[1],0).getDate()).padStart(2,'0');
                 f.requestSubmit();}">
```

- `name` 속성 **없음** → 서버로 전송되지 않음. 서버는 `from`/`to`만 처리
- JS가 말일 계산: `new Date(year, month, 0).getDate()` — 월 마지막 날
- `from_filter[:7]`로 현재 기간 시작 연-월을 피커에 미리 표시

### 4-8. 서비스 드롭다운 옵션

`app/admin/filters.py:10-17`의 `service_options()` 호출 시 `include_all=False` (`settlement.py:144`).  
정산 화면은 "전체 서비스" 항목이 없습니다 — 전체 모드는 서비스를 선택하지 않은 상태로 진입하기 때문입니다.

### 4-9. 관련 파일 요약

| 파일 | 역할 |
|------|------|
| `app/admin/routes/settlement.py` | 라우트, `_settlement_context`, `_settlement_payment_query` |
| `app/services/settlement.py` | `settlement_summary` 집계 쿼리, `SettlementRow` |
| `app/admin/templates/settlement/index.html` | Jinja2 템플릿 (툴바·요약·테이블) |
| `app/admin/export.py` | `xlsx_response`, `xlsx_safe` |
| `app/admin/pagination.py` | `PageParams`, `paginate`, `date_range` |
| `app/admin/filters.py` | `service_options`, `plan_name_options` |
| `app/admin/deps.py` | `AdminContext`, `require_any`, `service_scope` |

---

## 5. 주의사항 / 자주 하는 실수

### 정산 기준: approved_at · DONE+CANCELED

정산 집계 기준은 **결제 승인 시각(`approved_at`)** 입니다(생성 시각이나 취소 시각이 아님). 취소 건도 원래 승인 시각이 속한 기간에 집계됩니다. 대상 상태는 **승인(DONE)+취소(CANCELED)**이며, 실패(`FAILED`)·대기(`PENDING`)는 제외됩니다.

취소 건은 총매출에는 원금이, 환불에는 `canceled_amount`가 잡혀 **순매출 = 총매출 − 환불**(= 보유 취소 수수료)로 반영됩니다.

- 집계 조건 코드: `app/services/settlement.py` (`Payment.status.in_((DONE, CANCELED))`)
- 건별 쿼리 조건: `app/admin/routes/settlement.py` (`Payment.status.in_((DONE, CANCELED))`)

### 반개구간: to 날짜의 경계

`to=2026-05-31`로 조회하면 실제 DB 조건은 `approved_at < 2026-06-01 00:00:00 UTC`입니다 (`pagination.py:144`).  
"5월 31일 KST 23:59" 결제도 정상 포함됩니다.

### KST 주의

DB 저장은 **UTC**, 화면 표시는 **KST**입니다 (`index.html:99`의 `|kst` 필터).  
`date_range`는 `YYYY-MM-DD`를 **UTC 0시**로 파싱합니다 (`pagination.py:137`).  
`from=2026-05-01`은 `2026-05-01 00:00:00 UTC` = KST 기준 `2026-04-30 09:00 KST`부터 포함됩니다.  
KST 기준 "5월 1일 0시"와 9시간 차이가 발생할 수 있으며, 필요시 `date_range` 함수를 KST 기준으로 변경해야 합니다.

### plan_name 필터 시 일반결제 자동 제외

요금제를 선택하면 단건(일반) 결제는 집계에서 자동으로 빠집니다.  
일반 결제에는 `plan_id`가 없어 `Subscription → Plan` INNER JOIN에서 탈락하기 때문입니다 (`settlement.py:49-51`, `services/settlement.py:46-49`).  
이는 의도된 동작이지만, "요금제 선택 후 일반 결제가 사라졌다"는 문의가 들어올 수 있으니 운영자에게 미리 안내하세요.

### 월 선택 피커는 서버와 무관

`<input type="month">` 위젯은 JS 전용(`name` 없음)입니다.  
서버 파라미터를 직접 조작하면(`from`/`to` 수동 편집) 피커 표시(`from_filter[:7]`)가 실제 기간과 달라 보일 수 있습니다.

### SERVICE_MANAGER 스코프 문제 디버깅

"담당 서비스가 목록에 안 나온다"는 문의 시:

1. `app/admin/deps.py:77` — `account_service.effective_service_ids(db, user)` 반환값 확인
2. `app/admin/filters.py:13-16` — `service_options` 쿼리에서 `scope` 전달 확인
3. `app/services/settlement.py:54-55` — `scope is not None`이면 `IN (...)` 절 추가됨

### 엑셀 수식 주입

외부에서 유입된 `order_id`나 `external_user_id`가 `=`, `+`, `-`, `@`로 시작하면 일부 스프레드시트 앱이 수식으로 해석할 수 있습니다. `xlsx_safe` 함수가 `'` 프리픽스를 자동 추가해 방어합니다 (`export.py:16-19`).
