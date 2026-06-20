# Phase 3 수정 작업 내역 (확장성·성능)

- **일자**: 2026-06-11
- **근거**: [2026-06-11-code-audit.md](2026-06-11-code-audit.md) — Phase 3 (확장성·성능)
- **선행**: [Phase 1](2026-06-11-phase1-worklog.md) (운영 안정성) · [Phase 2](2026-06-11-phase2-worklog.md) (보안 보강)
- **검증**: 전체 테스트 `497 passed` (Phase 2 종료 시 495 → 신규 2, 실패 0)
- **대상 항목**: 성능 M1·H3·M2·M4 (M5는 부분 해소 — 하단 참조)

---

## 작업 1. 핵심 조회 컬럼 인덱스 6종 추가 (성능 M1) ✅ — DB 마이그레이션 포함

**문제**: 정산 스윕(5분마다 실행)이 결제 테이블을 풀스캔하는 등, 자주 쓰는 조회 경로에 인덱스가 없었음.

**변경**: 모델 `__table_args__` + 마이그레이션 `b8c9d0e1f2a3_perf_indexes.py` (dev DB 적용 완료):

| 인덱스 | 지원하는 조회 |
|---|---|
| `payments(status, requested_at)` | 정산 스윕(PENDING+유예경과 — **5분마다**), 결제목록 기본 정렬 |
| `payments(service_id, approved_at)` | 대시보드 매출 집계, 월별 정산 |
| `audit_logs(created_at)` | 감사 목록 정렬, 대시보드 기간 집계 (append-only라 쓰기 비용 대비 효과 큼) |
| `audit_logs(target_type, target_id)` | 대시보드 `target_id IN(...)`, 서비스 상세 이벤트 |
| `subscriptions(service_id)` | 어드민 목록·대시보드 스코프 필터 (부분 유니크는 EXPIRED 미커버라 대체 불가) |
| `subscriptions(status, current_period_end)` | 배치 취소/비자동갱신 만료 조회, 만료임박 레일 |

**운영 적용 주의**: 데이터가 큰 운영 DB에서는 인덱스 생성 잠금을 고려해 트래픽이 적은 시간대에 `alembic upgrade head` 실행 권장(마이그레이션 docstring에도 명시).

## 작업 2. 대시보드 DB 집계 전환 (성능 H3) ✅

**문제**: `_fetch_sub_states`가 스코프 구독 **전체**를 메모리에 적재한 뒤, 12개월+30일 = 42개 버킷마다 전 행을 재순회(42×N 파이썬 루프). 구독 10만 건이면 대시보드 1회 로드에 420만 회 루프가 이벤트 루프를 점유.

**변경** (`app/services/dashboard.py`): 버킷별 `count(*)/sum() FILTER (WHERE ...)` 컬럼을 가진 **단일 쿼리**로 전환 — DB가 테이블을 1회만 스캔하고 행을 전송하지 않음:

- `_open_new_counts(db, scope, buckets, now)` — 버킷별 (열린 구독 스냅샷, 신규 수)를 한 쿼리로 (12개월용 24컬럼 / 30일용 60컬럼)
- `_oneoff_sums` — 월별 ONE_OFF 매출 12컬럼 한 쿼리 (전체 범위 WHERE로 신규 인덱스 활용)
- `_audit_counts` — 일별 취소/만료 이벤트 수 (액션군별 30컬럼 한 쿼리)
- 제거: `_fetch_sub_states`, `_open_count_at`, `_new_count_between`, `_fetch_oneoff_payments`, `_fetch_audit_events`

**동작 동일성**: '열린 구독' 판정 규칙(`_open_subs_cond`)·버킷 경계([start, end) 반개구간, UTC)·스냅샷 시점(min(버킷 끝, now)) 모두 기존 파이썬 구현과 동일. 기존 대시보드 테스트 14건 전부 통과로 확인.

## 작업 3. 엑셀 export 행 상한 (성능 M2) ✅

**문제**: 모든 export가 필터 결과 전체를 ORM 객체로 적재 + BytesIO에 전체 워크북 생성 — 수십만 건이면 요청 1건이 수백 MB, 동시 다운로드 시 워커 OOM 가능.

**변경**: `app/admin/export.py`에 `EXPORT_MAX_ROWS = 100,000` 상수 신설, 5개 export 쿼리에 `.limit()` 적용:
- 결제 전체(`payments_export`), 구독 전체(`subscriptions_export`), 감사 로그(`audit_export` — 무한 증가 테이블이라 특히 중요), 서비스 상세 구독 탭/단건 탭.
- 상한 도달 시 정렬 기준 상위(최신) 10만 건까지 받게 되며, 그 이전 데이터는 기간 필터로 좁혀 받도록 상수 주석에 안내.

**미적용(의도)**: `StreamingResponse`/CSV 스트리밍 — xlsx 형식 유지 요구와 코드 복잡도 대비, 10만 행 상한으로 OOM 위험은 충분히 차단됨.

## 작업 4. 킬스위치 Redis 캐시 (성능 M4) ✅

**문제**: `ensure_server_enabled`가 **모든 외부 API 요청**마다 GlobalSettings DB 조회를 수행.

**변경** (`app/services/app_settings.py`):
- 결과를 Redis 키 `cache:global:server_disabled`에 **5초 TTL**로 캐시. 인코딩: `""`=활성, 비어있지 않으면 비활성 사유(503 메시지로 그대로 사용).
- `set_server_disabled(redis=...)` 전환 시 캐시 **즉시 무효화** — 킬스위치 전파 지연 없음. TTL은 무효화가 닿지 않는 경우(다중 인스턴스 등)의 최대 지연 상한.
- `redis=None`이면(배치·기존 테스트 경로) 기존대로 DB 직접 조회 — 하위 호환.
- 호출부: `app/api/deps.py`(redis 전달), `app/admin/routes/settings.py` server-toggle(무효화용 redis 전달).

## M5 (대시보드 직렬 쿼리 ~25회) — 부분 해소

- H3 전환으로 시리즈 계산 쿼리가 통합되어 가장 무거운 부분이 해소됨. 인덱스(M1)로 나머지 쿼리도 가속.
- 서비스별 테이블(`_service_revenue`/`_service_subs`)의 상관 서브쿼리는 유지 — 사내 서비스 수가 적은 동안 충분하며, 수십 개 이상으로 늘면 GROUP BY 조인 전환 검토(11번 매뉴얼에 기준 명시).
- Redis 대시보드 캐시는 **미적용(의도)** — 레일 영역이 ORM 객체를 직접 템플릿에 전달해 직렬화 비용·복잡도가 크고, H3 해소 후 남는 비용이 작음. 필요해지면 시리즈(JSON 호환 dict)만 선별 캐시 권장.

## 신규 테스트

- `tests/integration/test_killswitch.py` +2건: ① redis 전달 시 캐시 적재·캐시 경로 차단 검증, ② 킬스위치 전환 시 캐시 즉시 무효화 검증.
- 대시보드는 기존 테스트 14건(integration+e2e)이 집계 동일성의 회귀망 역할.

```
전체: 497 passed (Phase 2 종료 시 495 → +2)
```

## 변경 파일 전체 목록

| 파일 | 작업 |
|---|---|
| `app/models/payment.py`, `app/models/subscription.py`, `app/models/audit_log.py` | 1 (인덱스 정의) |
| `alembic/versions/b8c9d0e1f2a3_perf_indexes.py` | 1 (신규) |
| `app/services/dashboard.py` | 2 |
| `app/admin/export.py` + `routes/payments.py`·`subscriptions.py`·`audit.py`·`services.py` | 3 |
| `app/services/app_settings.py`, `app/api/deps.py`, `app/admin/routes/settings.py` | 4 |
| `tests/integration/test_killswitch.py` | 테스트 +2 |
| `docs/dev_manual/02·11·14` + `manual.html` | 매뉴얼 반영 + 재빌드 |

## 감사 리포트 대비 잔여 항목 (Phase 4 — 유지보수성)

Phase 1~3으로 보안·성능 항목은 모두 처리(또는 사유와 함께 보류)됨. 남은 것은 구조 개선:
문자열 리터럴→enum 치환(S5), locks.py rename(S7), 구독 쿼리 중복 통합(S2), paginate 개선(S3),
대표 담당자 규칙 서비스 레이어 이동(S4), **구독 상태 전이 중앙화(S1 — 가장 가치 큼)**,
services.py 라우터 분리(S6) 등 — 감사 리포트 4장 Phase 4 표 참조.
