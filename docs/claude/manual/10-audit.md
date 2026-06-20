# 10. 감사로그 (기록 · 조회 · 엑셀 · 삭제)

> "누가, 언제, 무엇을 했는가"를 남기는 **추적 장치**. 01~09의 거의 모든 상태 변경이
> `record_audit`로 여기에 기록된다. 이 문서는 기록(쓰기)과 조회/엑셀/삭제(읽기·관리)를 다룬다.
>
> 선행: [02-admin-auth.md](02)(권한), [00-overview.md](00)(UTC/KST),
> [12-admin-lists-export.md](12)(엑셀 공용 유틸).

---

## 0. 한눈에 보기

- **기록**: 서비스 계층 어디서나 `record_audit(...)` 호출 → `AuditLog` 한 행 추가(커밋은 호출자).
- **조회/관리**: Admin 화면(`/admin/audit`), **`SYSTEM_ADMIN` 전용**(`require_admin`).

| 하는 일 | HTTP | URL | 라우트 | 비고 |
|---|---|---|---|---|
| 기록(전 기능) | — | — | `services/audit.record_audit` | 호출자가 commit |
| 목록(검색·필터) | GET | `/admin/audit` | `audit_list` | htmx 부분 렌더 |
| 엑셀 다운로드 | GET | `/admin/audit/export.xlsx` | `audit_export` | 현재 필터 적용 전체 |
| 과거 삭제 | POST | `/admin/audit/purge` | `audit_purge` | 기준일 이전 일괄 삭제 |

관련 파일: `app/services/audit.py`(기록), `app/admin/routes/audit.py`(조회/엑셀/삭제),
`app/admin/audit_labels.py`(한글 라벨), `app/models/audit_log.py`.

---

## 1. 데이터 모델 — `AuditLog` (`models/audit_log.py`)

| 컬럼 | 의미 |
|---|---|
| `actor_type` | 행위자 종류: `USER`(관리자) / `SERVICE`(외부 서비스) / `SYSTEM`(배치·스케줄러) |
| `actor_user_id` | USER 행위자의 계정 ID(nullable) |
| `actor_service_id` | SERVICE 행위자의 서비스 ID(nullable) |
| `action` (index) | 액션 키(예: `subscription.create`). 영문 키로 저장 |
| `target_type` / `target_id` | 대상 종류/ID(예: `subscription` / 구독 UUID 문자열) |
| `detail` (JSONB) | 액션별 부가 정보(금액·사유·이름 등 자유 구조) |
| `ip_address` | 행위 IP(로그인 등) |
| `created_at` | 기록 시각(UTC, server_default) |

설계 포인트:
- **행위자가 3종**이고 ID 컬럼이 둘(`actor_user_id`/`actor_service_id`)로 나뉜다. SYSTEM은 둘 다 None.
- **FK가 없다**(actor/target 모두). 서비스·구독이 삭제돼도 로그는 **보존**되어야 하므로 일부러 느슨한 참조.
  대신 화면에서 이름을 resolve할 때 "이미 삭제됨"이면 ID만 표시(2-3).
- `action`은 영문 키로 저장하고, 화면에서 한글 라벨로 변환(`audit_labels`). 데이터·표시 분리.

---

## 2. 기록 — `record_audit` (`services/audit.py`)

```python
async def record_audit(db, *, actor_type, action,
                       actor_user_id=None, actor_service_id=None,
                       target_type=None, target_id=None, detail=None, ip_address=None):
    db.add(AuditLog(actor_type=..., action=..., actor_user_id=..., actor_service_id=...,
                    target_type=..., target_id=..., detail=..., ip_address=...))
    # ❗ commit은 하지 않는다 — 호출자가 자기 트랜잭션에 묶어 commit
```

핵심 규약(전 문서 공통):
- **커밋은 호출자 몫**. `record_audit`은 `db.add`만 한다. 그래서 "상태 변경 + 감사 기록"이
  **하나의 트랜잭션**으로 묶여 원자적으로 저장된다(둘 다 되거나 둘 다 안 됨). 예: `register_service`,
  `_renew_one`은 변경→`record_audit`→`commit` 순서(문서 01·05).
- **행위자 채우기 규칙**:
  - 관리자 동작 → `actor_type="USER", actor_user_id=ctx.user.id`(Admin 라우트).
  - 외부 서비스 동작 → `actor_type="SERVICE", actor_service_id=service.id`(외부 API 경유).
  - 배치/스케줄러 → `actor_type="SYSTEM"`(둘 다 None) — 예: `subscription.renewed`, `subscription.expired`.
  - 가변 경로(취소·재개·카드변경, 문서 06)는 `actor_type`에 따라 알맞은 ID를 넣는다.

기록되는 액션 전체는 `audit_labels.ACTION_LABELS`에서 한눈에 볼 수 있다(로그인/계정/서비스/요금제/
구독/결제/결제정산/감사삭제 등 48종, 아래 부록 참조).

---

## 3. 조회 — `GET /admin/audit` (`audit_list`)

### 3-1. 쿼리 구성 — `_build_audit_query` (목록·엑셀 공용)
```python
base = select(AuditLog)
if pp.q:                                  # 통합 검색(부분일치)
    like = f"%{q}%"
    base = where( actor_user_id IN (User.email ILIKE like)      # 행위자(관리자 이메일)
                | actor_service_id IN (Service.name ILIKE like) # 행위자(서비스명)
                | target_id ILIKE like                          # 대상 ID
                | CAST(detail AS String) ILIKE like )           # 상세(JSONB 텍스트)
if filters[actor_type]: base = where(actor_type == ...)         # 행위자 종류 필터
if filters[action]:     base = where(action == ...)             # 활동 필터
```
- 검색창(`q`)은 **행위자/대상/상세를 한 번에** 부분검색(JSONB는 텍스트로 캐스팅해 LIKE).
- 필터 2종: 행위자 종류(USER/SERVICE/SYSTEM), 활동(ACTION_LABELS 전체가 select 옵션).
- 목록과 엑셀이 **같은 쿼리 빌더를 공유** → 화면과 파일 내용이 항상 일치.

### 3-2. 이름 resolve — `_resolve_names` (N+1 방지 배치 조회)
로그엔 ID만 있으므로 사람이 읽게 이름을 채운다. **한 번에 모아서** 조회한다:
- `actor_user_id`들 → User.email 맵.
- `actor_service_id`들 → Service.name 맵.
- `target_type`별로 ID를 모아 Service/Plan/User/Subscription에서 이름을 배치 조회.
- 삭제돼서 못 찾으면 맵에 없음 → 표시 단계에서 ID나 타입만 노출(로그는 남고 이름만 빈다).

### 3-3. 표시 행 만들기 — `_build_rows` + `audit_labels`
각 로그를 화면/엑셀 공용 dict로 변환하며 한글화:
- `actor` — `actor_label`: USER면 이메일, 아니면 종류 한글("외부 서비스"/"시스템").
  화면 템플릿은 SERVICE이고 서비스명이 있으면 **`외부 서비스 (서비스명)` + 상세 링크**로 렌더.
- `action` — `action_label`: 영문 키 → 한글("구독 생성" 등). 매핑 없으면 키 그대로.
- `target` — `target_label`: "구독 · user-123" 식. 이름 없으면 타입만.
- `detail` — `detail_summary`: JSONB에서 사람에게 의미있는 필드만 골라 "금액 9,000원 · 사유 ..." 식으로.
- `time` — UTC 저장값(화면은 `kst` 필터로 KST 표시, 문서 00).

### 3-4. 라우트
```python
pp = PageParams(filter_keys=("actor_type","action"))
base = _build_audit_query(pp)
page = await paginate(...); page.items = await _build_rows(db, logs)
return render_list("audit/list.html", "audit/_table.html", ...,
                   action_options=[("","전체 활동")] + ACTION_LABELS.items())
```
`render_list`라 htmx 부분 요청이면 표만 갱신(검색/정렬/페이지 이동이 매끄럽게).

> 참고: 감사 화면은 `?error=` 표시 블록이 없어, 에러도 **flash 토스트**로 통일한다(삭제 실패 메시지 등).

---

## 4. 엑셀 다운로드 — `GET /admin/audit/export.xlsx` (`audit_export`)

```python
items_q = _build_audit_query(pp).order_by(...)        # ★ 목록과 같은 필터/검색
logs = list((await db.scalars(items_q)).all())         # 페이지네이션 없이 전량
rows = await _build_rows(db, logs)
out = []
for r in rows:
    actor = (f"외부 서비스 ({r['actor_service_name']})"
             if r["actor_service_name"] else r["actor"])
    out.append([kst_format(r["time"], "%Y-%m-%d %H:%M:%S"), actor,
                r["action"], r["target"], r["detail"], r["ip"]])
return xlsx_response("audit-log", ["시각", "행위자", "활동", "대상", "상세", "IP"],
                     out, sheet_title="감사로그")
```

- **현재 화면의 필터/검색을 그대로 반영**한 전체(페이지 제한 없음)를 내려받는다(같은 `_build_audit_query`).
- **공용 유틸 `xlsx_response` 사용** — `app/admin/export.py`의 공용 함수로 처리된다. write-only 워크북,
  수식 주입 방어(`xlsx_safe`), RFC 5987 한글 파일명, KST 파일명 타임스탬프가 자동 적용된다.
  엑셀 관련 구현 상세는 [12-admin-lists-export.md](12-admin-lists-export.md#1-공용-유틸--appadminexportpy) 참고.
- 시각 셀은 `kst_format(r["time"], "%Y-%m-%d %H:%M:%S")` — KST 문자열로 변환해 넣는다.
- SERVICE 행위자는 "외부 서비스 (서비스명)" 형식으로 조합해 한 셀에 담는다(화면의 링크와 동일 의미).

---

## 5. 과거 삭제 — `POST /admin/audit/purge` (`audit_purge`)

```python
await validate_csrf(request, ctx)
before = date.fromisoformat(form["before"])    # YYYY-MM-DD
  ├ 형식 오류 → flash(error) 리다이렉트
  └ before > 오늘(UTC) → flash(error)("기준일은 오늘 이후일 수 없습니다")
cutoff = before 00:00 UTC
result = DELETE FROM audit_logs WHERE created_at < cutoff
deleted = result.rowcount(>0)
record_audit(actor_type="USER", action="audit.purge",
             detail={before, deleted_count})    # ★ 삭제 행위 자체도 기록
commit
→ flash("감사로그 N건을 삭제했습니다")
```

- **기준일 이전(UTC 자정)** 로그를 일괄 삭제. 로그 보관 정책/용량 관리를 위한 운영 기능.
- **삭제 행위 자체를 `audit.purge`로 기록**한다(누가 언제 몇 건 지웠는지). 이 기록은 현재 시각이라
  방금 지운 cutoff 이전 대상에 포함되지 않는다.
- 미래 날짜 거부(과거 삭제 취지). 형식 오류·미래 모두 flash 에러 토스트로 안내.

> 화면(템플릿)은 날짜 입력 + `data-confirm` 확인 다이얼로그로 실수 삭제를 막는다(문서 외 UI).

---

## 6. "누가 무엇을 했나"를 추적하는 법 (실무 흐름)

문제 조사 예시:
1. `/admin/audit`에서 **행위자 종류**(예: 외부 서비스)와 **활동**(예: 구독 생성) 필터.
2. 검색창에 사용자ID/서비스명/주문번호 일부를 넣어 좁힌다(행위자·대상·상세 동시 검색).
3. 행의 `상세`에서 금액·사유·코드 등을 확인. SERVICE 행위자는 서비스명 링크로 상세 이동.
4. 필요하면 엑셀로 내려받아 보관/분석.

대표적 추적 포인트: 키 노출(`service.keys_viewed`), 강제취소(`subscription.force_cancel`),
정지/만료(`subscription.suspended`/`expired`), 단건 결제(`payment.one_off`/`one_off_failed`/`one_off_unresolved`),
결제 정산(`payment.reconciled_*`), 감사 삭제(`audit.purge`).

---

## 7. 예외 · 주의

| 상황 | 처리 |
|---|---|
| 행위자/대상이 이미 삭제됨 | 이름 resolve 실패 → ID/타입만 표시(로그는 보존) |
| 검색이 JSONB 내부까지 필요 | `CAST(detail AS String) ILIKE`로 텍스트 검색 |
| 엑셀 수식 주입 | `xlsx_safe`로 `=`,`+`,`-`,`@` 시작 셀 텍스트화(공용 유틸, 문서 12) |
| purge 날짜 오류/미래 | flash 에러 토스트, 삭제 안 함 |
| 감사 기록 누락 위험 | `record_audit`는 commit 안 함 → 호출자가 같은 트랜잭션에 commit(원자성) |
| SERVICE_MANAGER 접근 | 감사 화면은 `require_admin`(시스템관리자 전용) — 매니저 불가 |

성능: `action`에 인덱스가 있다. 검색의 `actor_*` 서브쿼리/`detail` 캐스팅은 풀스캔 가능 —
대량 누적 시 인덱스 추가나 purge로 관리(유지보수 참고).

---

## 8. 관련 테스트

- `tests/integration/test_audit.py` — `record_audit` 저장(actor_user_id/actor_service_id/detail).
- `tests/e2e/test_admin_operations.py` — 목록 렌더/한글 라벨/대상·상세 표시, 외부서비스 행위자
  서비스명 링크, 활동 필터, q 검색(이메일/서비스명/대상/상세), 엑셀 다운로드(필터 반영),
  purge(기준일 이전만 삭제 + audit.purge 기록 + 미래/오류 거부 + CSRF).

---

## 9. 유지보수 체크리스트

1. **새 동작에 감사 추가**: 서비스 계층에서 `record_audit(...)` 호출(commit은 그 함수가 묶어서).
   액션 키는 `자원.동사`(예: `plan.archive`) 컨벤션. **행위자 ID를 올바르게**(USER/SERVICE/SYSTEM).
2. **한글 라벨 추가**: `audit_labels.ACTION_LABELS`에 키→한글. 안 넣으면 화면에 영문 키가 그대로 노출.
3. **detail에 보여줄 새 필드**: `_DETAIL_FIELDS`에 키→한글 라벨 추가(금액류는 자동 원 포맷).
4. **새 target_type**: `_TARGET_TABLE`(테이블명 매핑) + `TARGET_TYPE_LABELS`(한글) + `_resolve_names`의
   `names(...)` 호출 추가(이름 resolve).
5. **목록/엑셀 일관성 유지**: 둘 다 `_build_audit_query`+`_build_rows`를 쓴다. 새 필터는 양쪽에 자동 반영되니
   쿼리 빌더만 고칠 것(엑셀 따로 구현 금지). 엑셀 응답은 `xlsx_response` 공용 유틸로 통일(문서 12).
6. **감사 기록을 commit 분리하지 말 것**: 상태 변경과 같은 트랜잭션에 둬야 "변경됐는데 기록 누락"이
   안 생긴다.
7. **purge는 비가역**: 정책상 필요한 경우만. 삭제 자체가 `audit.purge`로 남으므로 추적은 가능.
8. 외부 유래 문자열(서비스명·상세)을 파일/메일에 넣을 땐 항상 sanitize/escape(엑셀 `_xlsx_safe`,
   웹훅 `_sanitize` 문서 07) — 인젝션 방지.

---

## 부록: ACTION_LABELS 전체 목록

`app/admin/audit_labels.py`의 `ACTION_LABELS` 현재 등록 항목. 화면 필터 드롭다운과 엑셀 "활동" 컬럼에 이 한글로 표시된다.

| 키(action) | 한글 | 비고 |
|---|---|---|
| `auth.login` | 로그인 | |
| `auth.login_failed` | 로그인 실패 | |
| `auth.password_set` | 비밀번호 설정 | |
| `account.create` | 계정 생성 | |
| `account.update` | 계정 정보 수정 | |
| `account.disable` | 계정 비활성화 | |
| `account.enable` | 계정 활성화 | |
| `account.delete` | 계정 삭제 | |
| `account.assign_service` | 서비스 담당 추가 | |
| `account.unassign_service` | 서비스 담당 해제 | |
| `user.create_admin` | 관리자 계정 생성 | |
| `user.password_reset_issued` | 비밀번호 재설정 메일 발송 | |
| `service.register` | 서비스 등록 | |
| `service.rotate_keys` | 서비스 키 재발급 | |
| `service.keys_viewed` | 서비스 키 조회 | 평문 키 노출 추적 |
| `service.update_ips` | 허용 IP 변경 | |
| `service.set_status` | 서비스 상태 변경 | |
| `service.delete` | 서비스 삭제 | |
| `service.set_primary_manager` | 대표 담당자 지정 | |
| `plan.create` | 요금제 생성 | |
| `plan.update` | 요금제 수정 | |
| `plan.archive` | 요금제 비활성화 | |
| `plan.delete` | 요금제 삭제 | |
| `subscription.create` | 구독 생성 | |
| `subscription.cancel` | 구독 취소 | |
| `subscription.resume` | 구독 재개 | |
| `subscription.change_card` | 카드 변경 | |
| `subscription.force_cancel` | 구독 강제 취소 | |
| `subscription.renewed` | 구독 자동연장 결제 | SYSTEM 행위자 |
| `subscription.suspended` | 구독 정지(재시도 소진) | SYSTEM 행위자 |
| `subscription.expired` | 구독 만료 | SYSTEM 행위자 |
| `subscription.payment_failed` | 갱신 결제 실패 | |
| `subscription.renewal_unresolved` | 갱신 결제 결과 불명 | |
| `subscription.first_payment_failed` | 첫 결제 실패 | |
| `subscription.first_payment_unresolved` | 첫 결제 결과 불명 | |
| `subscription.manual_pay` | 수동 결제(정지 복구) | |
| `subscription.manual_pay_failed` | 수동 결제 실패 | |
| `subscription.manual_pay_unresolved` | 수동 결제 결과 불명 | |
| `payment.one_off` | 단건 결제 | 구독 외 일반결제 성공 |
| `payment.one_off_failed` | 단건 결제 실패 | |
| `payment.one_off_unresolved` | 단건 결제 결과 불명 | |
| `payment.reconciled_done` | 결제 정산 확정(성공) | |
| `payment.reconciled_failed` | 결제 정산 확정(실패) | |
| `audit.purge` | 감사로그 삭제 | |

`payment.one_off*` 3종은 구독과 무관한 단건 결제의 성공·실패·결과 불명을 각각 기록한다. 문서 12의
서비스 상세 > 일반결제 탭과 연관된다.

---

## 부록: 매뉴얼 전체 마무리

이로써 기능별 프로세스 문서 01~10이 완성됐다. 전체 지도는
[00-overview.md](00-overview.md), 색인은 [README.md](README.md) 참고.
각 문서는 **프로세스 정의 → 관여 코드 → 처리 흐름 → 예외 → 테스트 → 유지보수 체크리스트** 틀을
공유하므로, 기능 추가·수정 시 해당 문서의 체크리스트부터 확인하면 된다.
