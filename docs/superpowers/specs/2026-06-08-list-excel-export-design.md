# 모든 Admin 리스트 엑셀 다운로드 설계

날짜: 2026-06-08
상태: 승인됨
요청: 모든 리스트에 엑셀 다운로드 기능 추가

## 결정 사항
- 기존 감사로그 export 패턴(`/admin/audit/export.xlsx`, openpyxl write-only, 현재 필터 반영, 스코프 적용)을 **공용 유틸로 추출**해 재사용.
- **목록 라우트와 export가 동일 쿼리 빌더를 공유**(필터·정렬 동일, 페이지네이션만 생략)해 드리프트 방지.
- 대상: 독립 리스트 6개(서비스·요금제·구독·결제이력·정산·관리자) + 서비스 상세 내 표 3개(구독·일반결제·요금제). 감사로그는 이미 보유 → 공용 유틸로 이관만.
- 정산은 현재 보는 모드 그대로(전체=서비스별 합계 / 서비스별=결제 건별).
- 모델/마이그레이션 없음. openpyxl는 이미 의존성.

## 1. 공용 유틸 `app/admin/export.py` (신설)
```python
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def xlsx_safe(value):
    """수식 주입 방어 — =,+,-,@ 로 시작하는 문자열에 ' 프리픽스."""

def xlsx_response(filename_prefix: str, header: list[str],
                  rows: Iterable[list], *, now) -> Response:
    """write-only 워크북 생성 → Response. now는 호출측이 전달(utcnow)."""
```
- `Workbook(write_only=True)`, 첫 행 header, 이후 rows(`xlsx_safe` 적용). 시각은 KST 문자열, 금액은 정수 셀.
- `Content-Disposition: attachment; filename="{prefix}-{YYYYmmdd-HHMM}.xlsx"` (KST 시각), media=XLSX_MEDIA.
- 기존 `app/admin/routes/audit.py`의 `_xlsx_safe`/`_XLSX_MEDIA`/응답 생성부를 이 유틸로 이관(동작 동일, 회귀 테스트로 보호).

## 2. 쿼리 빌더 공유
각 목록 라우트의 base 쿼리 구성을 `_build_*_query(pp, ctx)` 형태 헬퍼로 추출(이미 audit는 `_build_audit_query` 보유). 목록 라우트는 이 헬퍼 + paginate, export는 이 헬퍼 + 전체 실행. 필터/검색/정렬/스코프 동일.

## 3. 독립 리스트 export 엔드포인트 (표시 컬럼과 동일 순서)
| 화면 | URL | dep | 컬럼 |
|---|---|---|---|
| 서비스 | `GET /admin/services/export.xlsx` | require_admin | 서비스명, 담당자 이메일, 허용 IP, 상태 |
| 요금제 | `GET /admin/plans/export.xlsx` | require_any | 서비스, 요금제, 결제주기, 정가, 첫결제, 정기결제, 상태 |
| 구독 | `GET /admin/subscriptions/export.xlsx` | require_any(스코프) | 서비스, 사용자, 요금제, 상태, 만료일, 다음결제 |
| 결제이력 | `GET /admin/payments/export.xlsx` | require_any(스코프) | 주문번호, 서비스, 종류, 사용자, 유형, 금액, 상태, 실패코드, 요청시각 |
| 관리자 | `GET /admin/users/export.xlsx` | require_admin | 이메일, 역할, 주 서비스, 상태 |
| 정산 | `GET /admin/settlement/export.xlsx` | require_any(스코프) | 모드별(아래) |

- 정산 모드 판별: `service_id` 쿼리 유무.
  - 전체 모드: 서비스명, 건수, 구독매출, 일반매출, 합계
  - 서비스별 모드: 승인시각, 사용자, 주문번호, 유형, 종류(구독/일반), 금액
- 모든 export는 현재 화면의 검색·필터·정렬 쿼리스트링을 그대로 받아 반영. 페이지네이션만 무시.
- 스코프: 매니저는 담당 서비스 데이터만(목록과 동일). 잘못된/타 서비스 service_id는 목록과 동일하게 처리(정산은 404, 그 외 무시).
- 종류/상태/주기 등 코드값은 화면 표기와 동일 라벨 사용(가능하면 화면 매핑 재사용).

## 4. 서비스 상세 내 표 export (서비스 고정)
| 표 | URL | 컬럼 |
|---|---|---|
| 구독 | `GET /admin/services/{id}/subs.xlsx` | 사용자, 요금제, 상태, 만료일, 다음결제 |
| 일반결제 | `GET /admin/services/{id}/oneoff.xlsx` | 승인시각, 사용자, 주문번호, 금액, 상태 |
| 요금제 | `GET /admin/services/{id}/plans.xlsx` | 요금제, 결제주기, 정가, 첫결제, 정기결제, 상태 |
- 서비스 상세 라우트의 해당 partial 쿼리(구독은 status 필터 반영)와 동일. 권한은 상세와 동일(require_admin; 매니저 접근 시 자기 서비스만 — 기존 상세 권한 그대로).

## 5. 화면 버튼
- 공용 `app/admin/templates/_list.html` `toolbar` 매크로에 선택적 `export_url` 인자 추가:
  있으면 우측에 `<a class="btn btn-sm btn-ghost" href="{export_url}?{{ pp.query_without('page') }}">엑셀 다운로드</a>`(lucide download 아이콘) 렌더. 감사로그 버튼도 이 방식으로 통일.
- toolbar 미사용 화면은 헤더에 동일 버튼 직접 추가:
  - 정산 `settlement/index.html`: 현재 모드의 export URL(+ 현재 from/to/service_id 쿼리).
  - 서비스 상세 `_subs_table.html`/`_oneoff_table.html`/`_plans_table.html`: 각 `/admin/services/{id}/*.xlsx`(+ 현재 필터).

## 6. 테스트
- 단위(`tests/unit/test_export.py` 신설): `xlsx_safe`(=,+,-,@ 방어), `xlsx_response`(헤더/행/파일명 헤더).
- e2e(`tests/e2e/test_list_export.py` 신설): 6개 독립 리스트 + 정산 2모드 + 상세 3표 각각
  - 200 + Content-Type=XLSX_MEDIA + Content-Disposition filename
  - openpyxl로 다시 열어 헤더/행 수 확인(필터 반영: 한 건만 매칭되는 필터로 1행 검증)
  - 스코프 격리: 매니저 토큰으로 타 서비스 데이터 미포함
  - 버튼 노출: 각 목록 화면에 export 링크 존재(현재 쿼리스트링 유지)
- 감사로그 회귀: 기존 export 테스트 그대로 통과(유틸 이관 후).

## 변경하지 않는 것
- 목록 화면의 데이터/필터 로직(쿼리 빌더 추출은 동작 동일).
- 도메인/모델/마이그레이션. 외부 API. openpyxl 의존성(이미 존재).
