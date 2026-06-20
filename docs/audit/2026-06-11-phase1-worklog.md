# Phase 1 수정 작업 내역 (운영 안정성)

- **일자**: 2026-06-11
- **근거**: [2026-06-11-code-audit.md](2026-06-11-code-audit.md) — Phase 1 (장애 방지, 시급)
- **검증**: 수정 전 기준선 `483 passed` → 수정 후 `490 passed` (신규 단위 테스트 7개 포함, 실패 0)
- **대상 항목**: 성능 H1·H2·M3, 보안 M-5

---

## 작업 1. DB 커넥션 풀 명시 설정 (성능 M3) ✅

**문제**: `create_async_engine`이 풀 옵션 없이 생성돼 SQLAlchemy 기본값(pool_size=5 + max_overflow=10 = 최대 15)으로 동작. 토스 지연 시 동시 처리량이 15로 캡핑.

**변경 파일**:

| 파일 | 변경 내용 |
|---|---|
| `app/core/db.py` | `create_engine`에 `pool_size/max_overflow/pool_timeout/pool_recycle` 파라미터 추가 (기본 10/20/30s/1800s) |
| `app/core/config.py` | `Settings`에 `db_pool_size`, `db_max_overflow`, `db_pool_timeout`, `db_pool_recycle` 추가 — `.env`로 조정 가능 |
| `app/main.py` | `create_engine` 호출 시 위 설정값 전달 |
| `.env.example` | `DB_POOL_*` 4개 키 예시 추가 |

**참고**: `app/cli.py`의 일회성 명령은 기본값 사용(의도적 — 단명 프로세스라 풀 튜닝 불필요).

---

## 작업 2. X-Forwarded-For 위조 방어 (보안 M-5) ✅

**문제**: `trust_proxy=true`일 때 XFF의 **맨 왼쪽**(클라이언트가 위조 가능)을 신뢰. 프록시가 XFF를 append만 하면 공격자가 `X-Forwarded-For: <화이트리스트IP>` 헤더 하나로 서비스 IP 화이트리스트·어드민 IP 제한·토스 웹훅 IP 검증(웹훅의 유일한 인증 수단)을 모두 우회 가능.

**변경 내용**:

| 파일 | 변경 내용 |
|---|---|
| `app/api/deps.py` | `get_client_ip`가 XFF의 **오른쪽에서 `trust_proxy_hops`번째**를 취하도록 변경. 항목 수가 hop 수보다 적으면(위조 의심) 헤더 무시하고 소켓 피어 IP 폴백 |
| `app/core/config.py` | `trust_proxy_hops: int = 1` 설정 추가 |
| `tests/unit/test_client_ip.py` | **신규** — 스푸핑 시도 무시·hop 수별 선택·폴백 등 7케이스 단위 테스트 |
| `.env.example` | `TRUST_PROXY_HOPS=1` 예시 추가 |

**원리**: 신뢰 프록시는 자신이 본 피어 IP를 XFF 오른쪽에 append하므로, 오른쪽 n개까지가 신뢰 가능한 값. 프록시가 XFF를 덮어쓰지 않고 append만 해도 안전해짐.

**⚠️ 운영 배포 시 확인 필요**: 프록시가 2단 이상이면(`클라이언트→LB→nginx→앱` 등) `.env.prod`에 `TRUST_PROXY_HOPS`를 실제 단 수로 설정해야 함. 잘못 설정하면 IP 검사가 프록시 IP를 보게 되어 정상 요청이 거부됨(fail-closed — 보안상 안전한 방향으로 실패).

---

## 작업 3. 외부(토스) 호출 전 트랜잭션 분리 (성능 H1) ✅

**문제**: 토스 API read timeout이 65초인데, 아래 3개 경로가 FOR UPDATE 행 잠금(또는 열린 읽기 트랜잭션)과 풀 커넥션을 쥔 채 토스 응답을 대기. 풀 15개(작업 1 이전) 기준, 토스 지연 시 몇 건만으로 풀 고갈 → 결제와 무관한 API·어드민 전면 중단 위험.

### 3-1. `app/services/renewals.py` — `_renew_one` 3단계 분리

```
[1단계 트랜잭션] FOR UPDATE 검증 + PENDING 선기록 → commit (잠금·커넥션 반납)
[2단계]          토스 resolve_charge — DB 트랜잭션/커넥션 비점유
[3단계 트랜잭션] FOR UPDATE 재취득 + 재검증 → 결과 확정
```

- 기존에는 PENDING이 **이미 존재하는 재시도 경로**에서 commit 없이 토스를 호출했음 → 이제 모든 경로에서 외부 호출 전 commit.
- **3단계 재검증 추가** (외부 호출 동안 행 잠금이 풀려 있으므로):
  - `payment`가 더 이상 PENDING이 아니면(웹훅/정산 스윕이 먼저 확정) 중복 적용 금지 → skip
  - 구독이 호출 사이에 갱신 풀(TRIAL/ACTIVE/PAST_DUE)을 벗어났으면(취소 등):
    - 성공 시: 기간 전진 없이 결제만 DONE + `requires_review` 감사(환불 검토 대상 — reconciliation의 orphaned 정책과 동일)
    - 실패 시: 구독 상태 불변, 결제만 FAILED + `sub_left_due_pool` 감사
- 외부 호출 사이 동시성 방어 3중: ① 구독별 Redis 락(TTL 300s > 토스 65s), ② 결정적 order_id + 토스 멱등키, ③ 3단계 재검증.

### 3-2. `app/services/reconciliation.py` — `_reconcile_one_payment` 분리

기존엔 **항상** Payment FOR UPDATE 상태로 토스를 재조회했음(경로 무관 — 가장 심각).

```
[읽기 트랜잭션] PENDING 검증 + order_id 추출 → rollback (커넥션 반납)
[외부 호출]     toss.get_payment_by_order_id — DB 비점유
[확정 트랜잭션] Payment FOR UPDATE 재취득 + PENDING 재검증 → 확정
```

### 3-3. `app/services/subscriptions.py` — `create_subscription`

검증 SELECT들이 열어 둔 읽기 트랜잭션이 빌링키 발급(외부 호출) 동안 커넥션을 점유했음 → 발급 전 `await db.commit()`으로 트랜잭션 종료.
- `rollback`이 아닌 `commit`인 이유: rollback은 로드된 ORM 객체(plan)를 expire시켜 이후 속성 접근이 비동기 세션에서 오류(MissingGreenlet)가 됨. 읽기 전용이라 commit은 무해하고 `expire_on_commit=False` 덕에 객체가 유지됨.
- 동시 가입 경쟁의 최종 심판은 기존대로 flush 시 DB 부분 유니크 인덱스.

---

## 작업 4. 갱신 배치 병렬화 + 청크 상한 + 전역 락 heartbeat (성능 H2) ✅

**문제**: ① 배치가 구독을 1건씩 직렬 처리(1만 건 ≈ 수 시간), ② due 목록 무제한 적재, ③ 전역 락 TTL 240초가 배치 도중 만료 → 다중 인스턴스에서 배치 중첩 실행.

### 4-1. `app/services/renewals.py` — `process_due` 병렬화 + 상한

- **`BATCH_CONCURRENCY = 10`**: 4개 카테고리(취소 만료/정지 만료/갱신/비자동갱신 만료)의 전 작업을 `asyncio.Semaphore(10)` 병렬 풀로 실행. 카테고리 간 대상 상태 집합이 겹치지 않아 순서 의존성 없음. 건별 동시성은 구독별 Redis 락 + 멱등키가 방어(기존 안전판 재사용).
- **`BATCH_LIMIT = 1000`**: 각 due 쿼리에 due 시각 오름차순 + 상한 1000 적용. 잔여분은 다음 주기(5분)가 오래된 건부터 처리. 상한 도달 시 `logger.warning`으로 명시.
- 한 항목 실패는 기존대로 `stats["errors"]`만 올리고 계속.

### 4-2. `app/scheduler/runner.py` — 전역 락 heartbeat

- 락 값을 `"1"` → **무작위 토큰**으로 변경.
- 배치 실행 동안 백그라운드 태스크가 TTL(240s)의 1/3 주기(80s)마다 **토큰 비교 Lua**로 자기 소유 락의 TTL 연장 → 배치가 오래 걸려도 락 만료로 인한 중첩 실행 없음.
- 해제도 토큰 비교 Lua — 락을 잃은 인스턴스가 남의 락을 삭제하지 못함.
- TTL은 이제 "heartbeat가 멈춘 뒤(프로세스 사망) 락이 자연 해소되기까지의 시간"(데드맨 스위치)으로만 작동.

### 적용하지 않은 것 (의도적)

- **실패 이메일 fire-and-forget 분리**(감사 권고 4번째 항목): 병렬화로 이메일 대기가 배치 전체를 막지 않게 되어 시급성이 낮아졌고, fire-and-forget으로 바꾸면 발송 실패 추적이 어려워지며 기존 통합 테스트(발송 후 즉시 검증)와도 충돌. 추후 큐 도입 시 함께 처리 권장.

---

## 성능 영향 추정

| 시나리오 | 변경 전 | 변경 후 |
|---|---|---|
| 토스 65초 지연 × 동시 결제/정산 다수 | 풀(15) 고갈 → 전 서비스 중단 | 외부 호출 중 커넥션 비점유 + 풀 30 — 영향 격리 |
| 구독 1만 건 갱신 도래 | 직렬 ≈ 2.8시간+, 락 만료로 중첩 실행 | 병렬 10 × 상한 1000/주기 — 주기당 ~2분, 중첩 없음 |

## 테스트 결과

```
수정 전 기준선: 483 passed (48.5s)
수정 후:        490 passed (48.3s)   # +7 = tests/unit/test_client_ip.py
```

갱신 상태머신(`tests/integration/test_renewals.py` 539줄), 정산(`test_reconcile*`), 스케줄러(`test_scheduler.py`), 구독 생성 등 기존 통합·e2e 테스트가 모두 통과 — 트랜잭션 분리·병렬화가 기존 동작(타임아웃 PENDING 유지, 멱등 수렴, 락 스킵 등)을 보존함을 확인.

## 변경 파일 전체 목록

| 파일 | 작업 |
|---|---|
| `app/core/db.py` | 1 |
| `app/core/config.py` | 1, 2 |
| `app/main.py` | 1 |
| `app/api/deps.py` | 2 |
| `app/services/renewals.py` | 3-1, 4-1 |
| `app/services/reconciliation.py` | 3-2 |
| `app/services/subscriptions.py` | 3-3 |
| `app/scheduler/runner.py` | 4-2 |
| `tests/unit/test_client_ip.py` | 2 (신규) |
| `.env.example` | 1, 2 |
| `docs/dev_manual/01-getting-started.md` | 환경변수 표·prod 주의사항 갱신 |
| `docs/dev_manual/03-auth-and-security.md` | XFF 처리 방식 갱신 |
| `docs/dev_manual/05-subscription-renewal.md` | 배치 병렬화·트랜잭션 3단계·heartbeat 반영 |
| `docs/dev_manual/manual.html` | 재빌드 |

## 후속 권고 (Phase 1 범위 밖, 감사 리포트 참조)

- `_expire_subscription`(renewals.py)과 reconciliation의 NOT_FOUND 경로는 여전히 FOR UPDATE 상태에서 빌링키 삭제(외부 호출)를 수행 — 호출 빈도가 낮아 Phase 1에서 제외했으나 같은 패턴 적용 가능.
- 수동결제(`_perform_manual_charge`)와 배치 갱신이 동시에 같은 구독을 결제할 수 있는 이론적 경쟁은 **이번 변경 이전부터 존재**(수동결제는 FOR UPDATE·Redis 락 없이 토스 호출). 구독별 Redis 락을 수동결제에도 적용하는 것을 Phase 2+에서 권장.
- Phase 2(보안 보강): order_id 스코프 분리, 로그인 rate limit, 보안 헤더, docker-compose 바인딩 — 감사 리포트 4장 참조.
