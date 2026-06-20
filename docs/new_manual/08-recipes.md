# 08. 기능 추가 레시피 — 그대로 따라 하는 실습 4종

> 목표: 실제 변경 작업을 단계 누락 없이 수행한다. 각 레시피의 "참고 예시"는
> 실제로 이 저장소에 있는 과거 작업이다 — 막히면 그 커밋/파일을 열어 모방하라.

## 공통 마무리 (모든 레시피의 마지막 단계)

1. 변경 코드에 주석/docstring (왜 그렇게 했는지)
2. `uv run pytest` 전체 통과
3. `docs/dev_manual/` 해당 장 갱신 → `uv run --with markdown python docs/dev_manual/build_html.py`
4. `docs/audit/YYYY-MM-DD-<주제>-worklog.md` 작업 기록
5. main에 커밋 (메시지에 무엇을·왜)

---

## 레시피 A — 모델에 컬럼 추가 (예: 구독에 메모 필드)

1. `app/models/subscription.py`에 컬럼 추가 + 줄 끝 주석
2. 새 마이그레이션: `alembic/versions/` 최신 파일 복사 →
   `revision` 새 값/`down_revision`=직전 head, `upgrade()/downgrade()` 작성
   (기존 데이터 백필 필요 시 `op.execute` — 예시: `a7b8c9d0e1f2_payment_order_scope.py`)
3. `uv run alembic upgrade head`
4. 노출이 필요하면: 어드민 템플릿 / `schemas/api.py`(외부 API라면)
5. integration 테스트에 케이스 추가

## 레시피 B — 외부 API 엔드포인트 추가

1. **서비스 함수부터**: `app/services/…`에 비즈니스 로직 — 검증 → 처리 →
   `record_audit` → `commit`. 오류는 DomainError 계열로.
2. 스키마: `app/schemas/api.py`에 요청/응답 모델(+`from_model`) — ORM 직접 반환 금지,
   민감 필드(빌링키 등) 절대 비노출
3. 라우트: `app/api/v1/…` — `service: Service = Depends(authenticate_service)`
   (결제성이면 `payment_rate_limit`). 라우트 본문은 서비스 함수 호출+응답 변환만
4. 테스트: `tests/integration/`에 서비스 함수 테스트, `tests/e2e/test_api_endpoints…`에 HTTP 테스트
   (HMAC 서명 헬퍼는 `tests/helpers.py` 참고)
5. 샘플 반영: `sample_service/shop/payment_client.py`에 함수 1개 추가 +
   해당 화면 「개발자 노트」 — 서비스팀이 보는 레퍼런스이므로 잊지 말 것
   - 참고 예시: 구독 조회/취소/재개 일습 — `app/api/v1/subscriptions.py`

## 레시피 C — 어드민 화면(목록) 추가

1. 라우트: `app/admin/routes/`에 모듈 — `PageParams.from_request`(검색·정렬·필터) →
   쿼리 빌더(공유 가능하면 `app/admin/filters.py`에) → `paginate(db, q, pp)` →
   `render_list(전체템플릿, _partial템플릿)` (htmx가 partial만 갱신)
2. 템플릿: `templates/<영역>/list.html` + `_table.html` — 기존 구독 목록을 복사해 시작
3. 라우터 등록: `app/admin/__init__.py` — ⚠️ **고정 경로(`/foo/export.xlsx`)가 있는
   라우터는 가변 경로(`/foo/{id}`)를 가진 라우터보다 먼저 등록**(아니면 UUID 파싱 422)
4. 쓰기 액션이 있으면: POST 라우트에서 `validate_csrf` → 서비스 함수 →
   성공 `saved_redirect`, 실패 `?error=` 리다이렉트 패턴
5. e2e 테스트: `tests/e2e/` — `admin_login` 헬퍼 + 화면 응답 검증
   - 참고 예시: `app/admin/routes/services_export.py`(분리·등록 순서 주석 포함)

## 레시피 D — 구독 상태 추가 (최근 EXTENDED 추가가 실제 사례)

상태 하나를 추가하면 **아래 전부**를 손봐야 한다. 하나라도 빠지면 어디서 터지는지 함께 적는다:

| # | 수정 지점 | 빠뜨리면 |
|---|---|---|
| 1 | `app/models/enums.py` — 상태 + ACCESS_ALLOWED/OPEN 집합 | 접근 판정·1구독 규칙 오동작 |
| 2 | `app/models/subscription.py` 부분 유니크 인덱스 WHERE + **마이그레이션** | 그 상태에서 중복 구독 생성 가능 |
| 3 | `app/services/transitions.py` ALLOWED_TRANSITIONS | 전이 시도가 500(InvalidStateTransition) |
| 4 | `app/services/locks.py` DUE_STATUSES(갱신 대상이면) | 배치가 갱신을 안 함 |
| 5 | `app/services/renewals.py` 만료 스윕 대상 여부 | 영원히 안 끝나는 구독 |
| 6 | `app/services/dashboard.py` _STATUS_ORDER/_COLOR/_KO/_OPEN_STATUSES | 대시보드 KeyError 또는 집계 누락 |
| 7 | `app/admin/__init__.py` 상태 한글 라벨 + 템플릿 배지 | 화면에 영문 원값 노출 |
| 8 | `app/schemas/api.py` status/access_allowed 설명 | 외부 서비스가 새 값에 당황 |
| 9 | `sample_service` base.html `st-<상태>` 배지 + README | 샘플 화면 회색 배지 |
| 10 | `tests/unit/test_transitions.py` — "전 상태 등재" 테스트가 누락을 자동으로 잡는다 | — |
| 11 | dev_manual 05장 상태 전이표 + 이 가이드 03장 | 문서·코드 불일치 |

> ⚠️ 새 상태가 결제와 얽히면 11장(알려진 이슈)의 W-1~W-3을 먼저 읽어라 —
> EXTENDED 작업에서 발견된 함정(비자동갱신 정책, 기존 PENDING 고아화)이 그대로 재발한다.
