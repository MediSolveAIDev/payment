# 날짜 범위 검색 + 정산 메뉴 설계 (요청 009)

날짜: 2026-06-08
상태: 승인됨
요청: docs/requests/009.md

## 결정 사항

- 결제리스트의 기존 월(month) 필터는 **날짜 범위(시작~끝)로 교체** — 대시보드 카드 링크도
  `month=YYYY-MM` → `from=월초&to=오늘`로 변경 (사용자 결정)
- 정산 = 기간 내 **DONE 결제 금액 집계**, 기간 기준은 **`approved_at`(승인일)**
  (결제리스트 범위 필터는 `requested_at` — FAILED/PENDING 포함 시도 이력 관점이라 기준이 다름.
  정산 화면에 "승인일 기준" 명시)
- 정산 화면 구조: [전체] = 합계 + 서비스별 집계 행(상세보기 → 서비스별 모드),
  [서비스별] = 합계 + 결제 건별 목록(상세보기 → 구독 상세) (사용자 확인)
- 정산 권한: require_any + 기존 스코프 규칙 (SERVICE_MANAGER는 담당 서비스만) (사용자 결정)
- 정산은 단일 화면 + 쿼리 파라미터 모드 전환 (`service_id` 유무)

## 1. 날짜 범위 공용 헬퍼

`app/admin/pagination.py`에 추가:

```python
def date_range(pp: PageParams, from_key: str = "from", to_key: str = "to"):
    """pp.filters의 YYYY-MM-DD 쌍 → (start_dt|None, end_dt|None) UTC.
    end는 익일 0시(반개구간). 형식 오류 키는 무시 + pp.filters에서 제거(링크 오염 방지).
    한쪽만 입력 허용(열린 범위)."""
```

- 반환: `(start, end)` — 각각 `datetime | None`. 호출측은 None 아닌 쪽만 where 적용.

## 2. 구독리스트 — 구독일 범위 검색

- `subscriptions_list`: `filter_keys`에 `("from", "to")` 추가, `Subscription.created_at` 범위 필터.
- `subscriptions/list.html`(또는 `_table.html`의 툴바): 기존 toolbar 매크로를 결제리스트와 같은
  직접 폼으로 교체해 date input 2개(`from`, `to`) 추가. 기존 검색(q)/상태/서비스 필터 유지.
- 빈 값 허용(전체), 시작만/끝만 입력 허용.

## 3. 결제리스트 — 월 필터를 결제일 범위로 교체

- `payments_list`: `month` 필터·`_month_range` 헬퍼 제거 → `("from", "to")` + 공용 `date_range`,
  `Payment.requested_at` 기준.
- `payments/list.html`: `<input type="month">` → date input 2개.
- **대시보드 연동 변경** (`app/services/dashboard.py` `_month_cards`):
  - 매출 카드: `/admin/payments?status=DONE&from={월초}&to={오늘}`
  - 미결제 카드: `/admin/payments?status=FAILED&from={월초}&to={오늘}`
  - 성공률 카드: `/admin/payments?from={월초}&to={오늘}`
  - 날짜 형식 `YYYY-MM-DD` (UTC 기준 오늘)
- 기존 month 필터 테스트(`test_payments_month_filter`)는 범위 필터 테스트로 교체.

## 4. 정산 메뉴 (`/admin/settlement`)

### 서비스 계층 (`app/services/settlement.py` 신설)

```python
@dataclass
class SettlementRow:
    service_id: uuid.UUID
    service_name: str
    count: int      # DONE 결제 건수
    amount: int     # 합계 금액

async def settlement_summary(db, scope, start, end) -> tuple[int, int, list[SettlementRow]]:
    """(총 건수, 총 금액, 서비스별 집계 목록 — 금액 내림차순).
    DONE 결제, approved_at ∈ [start, end). scope는 기존 규칙."""
```

- 서비스별 모드의 결제 건별 목록은 라우트에서 기존 `paginate` + Payment/Subscription join으로
  처리(서비스 계층 추가 함수 불필요 — DONE + approved_at 범위 + service_id 조건).

### 라우트 (`app/admin/routes/settlement.py` 신설, require_any)

- `GET /admin/settlement` — 파라미터: `from`, `to`(기본값: 이번달 1일~오늘, 파라미터 없을 때),
  `service_id`(빈값=전체 모드).
- 전체 모드: `settlement_summary` 호출 → 합계 카드 + 서비스별 테이블 렌더.
  각 행 "상세보기" → `/admin/settlement?from=..&to=..&service_id={id}` (기간 유지).
- 서비스별 모드: 해당 서비스 합계(요약에서 필터) + 결제 건별 페이지(승인시각 내림차순).
  행 "상세보기" → `/admin/subscriptions/{sub_id}`.
- 스코프: SERVICE_MANAGER는 담당 서비스만 — 서비스 select 옵션도 스코프 내로 제한,
  스코프 밖 `service_id` 요청은 NotFoundError.
- `service_id` 파싱 실패(UUID 아님)는 전체 모드로 폴백.

### 템플릿 (`app/admin/templates/settlement/index.html` 신설)

- 상단 컨트롤 폼(GET): date input 2개 + 서비스 select(전체/각 서비스) + 조회 버튼.
- 합계 카드: "정산 금액 N원 · 결제 M건" + "승인일(approved_at) 기준" 안내 문구.
- 전체 모드 테이블: 서비스명 · 건수 · 금액 · 상세보기 버튼.
- 서비스별 모드 테이블: 승인시각 · 사용자(external_user_id) · 주문번호 · 유형 · 금액 · 상세보기.
  `_list.html`의 pager 매크로로 페이지네이션.
- `base.html` 사이드바에 "정산" 메뉴 추가 (결제 아래, 아이콘 `calculator`).

## 에러 처리

- 날짜 형식 오류: 해당 키 무시(공용 헬퍼 규칙).
- from > to: 결과 0건으로 자연 처리(별도 에러 없음 — 반개구간이라 빈 집합).
- 스코프 밖 service_id: 404.

## 테스트

- 단위/통합: `date_range` 헬퍼(정상/한쪽만/형식오류/제거), `settlement_summary`
  (기간 경계 [start,end), DONE만 합산, 서비스별 분리, 스코프 제한, 금액 내림차순).
- e2e: 구독 범위 필터(경계 포함/제외), 결제 범위 필터(month 테스트 교체),
  정산 전체 모드(합계+서비스별 행+상세보기 href 기간 유지), 서비스별 모드(건별 목록+구독 상세 링크),
  SERVICE_MANAGER 스코프(타 서비스 404, select 옵션 제한), 기본 기간(이번달) 렌더,
  사이드바 메뉴 노출, 대시보드 카드 링크 from/to 갱신 회귀.

## 변경하지 않는 것

- 외부 API(/api/v1), 알림, 스케줄러, 모델/마이그레이션 (정산은 집계 조회만 — 저장 없음)
- 감사로그/엑셀 기능, 구독·결제 리스트의 다른 필터
