# 09. 감사 로그 (조회·검색·정리·엑셀)

> **권한**: SYSTEM_ADMIN 전용. SERVICE_MANAGER 계정은 이 화면에 접근할 수 없다.

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

**감사 로그(Audit Log)** 화면은 시스템 내에서 발생한 모든 중요 행위(로그인·계정 변경·서비스 등록·요금제 수정·구독·결제·설정 변경 등)를 시간 역순으로 조회하는 곳이다. 로그는 삽입만 허용하는 불변 이력이다(`app/models/audit_log.py`, AuditLog 클래스 주석).

| 항목 | 값 |
|------|-----|
| 접근 URL | `/admin/audit` |
| 권한 | SYSTEM_ADMIN 전용 (`require_admin` 의존성) |
| 라우트 파일 | `app/admin/routes/audit.py` |
| 메인 템플릿 | `app/admin/templates/audit/list.html` |
| 테이블 부분 템플릿 | `app/admin/templates/audit/_table.html` |

---

## 2. 화면 구성 — 무엇이 보이나

### 페이지 레이아웃

`audit/list.html`(line 1-7)은 `base.html`을 상속하며 본문으로 `audit/_table.html`을 include한다. 제목은 **"감사 로그"**.

### 툴바

`_table.html`(line 3-7)에서 공용 매크로 `L.toolbar`를 호출한다.

- **검색 입력란**: placeholder "행위자·대상·상세 검색". `q` 파라미터로 전달된다.
- **행위자 유형 필터** (`actor_type`): 전체 행위자 / 관리자(`USER`) / 외부 서비스(`SERVICE`) / 시스템(`SYSTEM`)
- **활동 유형 필터** (`action`): 전체 활동 + `ACTION_LABELS`에 정의된 한글 항목(인증·계정·서비스·요금제·구독·결제·**카드**·전역설정 등) (`app/admin/audit_labels.py`)
- **엑셀 내보내기 버튼**: 현재 필터·검색을 그대로 유지하여 `/admin/audit/export.xlsx`로 이동

### 테이블 컬럼

`_table.html`(line 9-34), `_build_rows()` (`audit.py`, line 112-131) 기준:

| 컬럼 | 정렬 | 설명 |
|------|------|------|
| **시각** | 정렬 가능(`created_at`) | KST `YYYY-MM-DD HH:MM:SS` 형식으로 표시. 저장은 UTC. |
| **행위자** | 정렬 가능(`actor_type`) | USER이면 이메일, SERVICE이면 "외부 서비스 (서비스명)" 링크, SYSTEM이면 "시스템" |
| **활동** | 정렬 가능(`action`) | `ACTION_LABELS` 한글 변환값 (`audit_labels.py`, `action_label()`, line 74-75). 미등록 코드는 원문 그대로 표시. |
| **대상** | 정렬 불가 | `target_type` + 이름(서비스명/요금제명/이메일/external_user_id). `target_label()` (`audit_labels.py`, line 84-88) |
| **상세** | 정렬 불가 | `detail` JSONB를 `detail_summary()`로 요약 (`audit_labels.py`). 설정·요금제 등 **값이 바뀌는 동작은 "라벨 변경전 → 변경후"로 표시** (아래 참조) |
| **IP** | 정렬 불가 | 요청 출처 IPv4/IPv6 (없으면 `-`) |

#### 상세(detail)의 "변경 전 → 변경 후" 표시

`detail`에 `old_<필드>`/`new_<필드>` 쌍이 있으면 `detail_summary()`가 이를 묶어 **`라벨 변경전 → 변경후`** 형태로 보여준다(`audit_labels.py`의 `_DIFF_FIELDS`). 실제로 값이 바뀐 항목만 표시하고, 빈 목록은 "없음", 금액은 천단위 콤마+"원"으로 렌더한다. 예:

| 동작 | 상세 표시 예 |
|------|------------|
| 재시도 설정 변경(`settings.retry_updated`) | `재시도 횟수 4 → 6 · 재시도 간격(시간) 12 → 6` |
| 어드민 IP 변경(`settings.admin_ips_updated`) | `허용 IP 10.0.0.1 → 10.0.0.1, 10.0.0.2` |
| 요금제 수정(`plan.update`) | `정가 9,900원 → 19,900원 · 첫결제 할인 없음 → 정률 10% · 상시 할인 정액 5,000원 → 없음` (할인은 비율/값을 결합해 표시) |
| 서비스 상태 변경(`service.set_status`) | `상태 ACTIVE → INACTIVE` |
| 허용 IP 변경(`service.update_ips`) | `허용 IP 10.0.0.1 → 10.0.0.1, 10.0.0.2` |
| 취소 정책 변경(`service.cancel_policy_updated`) | `취소 허용 False → True · 취소 수수료율(%) 0 → 10` |
| 대표 담당자 지정(`service.set_primary_manager`) | `이메일 a@x → b@x` |
| 계정 수정(`account.update`) | `이메일 old@x → new@x` |
| 강제 취소(`subscription.force_cancel`) | `사용자 u1 · 요금제 기본 · 상태 ACTIVE → CANCELED` |
| 만료일 연장(`subscription.extended`) | `사용자 u1 · 상태 ACTIVE → EXTENDED · 만료일 2026-06-30… → 2026-09-30… · 다음 결제일 … → …` |

이를 위해 각 서비스 함수(`app_settings`·`registry`·`accounts`·`subscriptions`·`plans`)는 **변경 직전 값을 캡처**해 `old_*`/`new_*`로 감사 detail에 기록한다(요청 015). 변경 쌍이 아닌 단일 부가정보(사유·이메일·서비스명·메일 발송 안내 등)는 `라벨 값` 형태로 표시된다. 키재발급·키복사·비밀번호 재설정처럼 전/후가 없는 동작은 `note` 필드(설명 텍스트)로 무슨 일이 있었는지 남긴다.

### 페이지네이션

기본 15건/페이지(`PER_PAGE_DEFAULT = 15`, `app/admin/pagination.py`, line 18). `L.pager` 매크로로 렌더링 (`_table.html`, line 35).

### 이전 로그 삭제 폼 (Purge)

테이블 하단에 위치 (`_table.html`, line 36-44).

- 날짜 입력(`<input type="date" name="before">`)
- **"이전 로그 삭제"** 버튼(빨간색, trash-2 아이콘)
- `data-confirm` 속성으로 확인 모달 표시: 제목 "과거 로그를 삭제할까요?", 본문 "기준일 이전의 감사로그가 영구 삭제됩니다. 필요하면 먼저 엑셀로 내려받으세요.", 확인 버튼 "삭제"

---

## 3. 할 수 있는 동작

### 3-1. 검색

- **검색어(`q`)**: 행위자 이메일 ilike, 서비스명 ilike, `target_id` ilike, `detail` JSONB 텍스트 ilike 중 하나라도 일치하면 포함 (`audit.py`, line 96-108, `_build_audit_query()`)
- **행위자 유형 필터**: `actor_type` 컬럼 일치 (`audit.py`, line 105-106)
- **활동 필터**: `action` 컬럼 일치 (`audit.py`, line 107-108)
- 검색·필터·정렬은 URL 파라미터(`q`, `actor_type`, `action`, `sort`, `dir`, `page`)로 전달되며, htmx로 `#list-audit` 영역만 부분 교체된다.
- 정렬 기본값: `created_at DESC` (`PageParams.from_request`, `default_sort="created_at"`, `default_dir="desc"`)

### 3-2. 엑셀 내보내기 (`GET /admin/audit/export.xlsx`)

현재 URL의 `q`, `actor_type`, `action`, `sort`, `dir` 파라미터를 그대로 사용하여 페이지네이션 없이 전체 결과를 xlsx로 반환한다 (`audit.py`, line 153-170).

- 컬럼 순서: 시각 / 행위자 / 활동 / 대상 / 상세 / IP
- 행위자가 외부 서비스인 경우 "외부 서비스 (서비스명)" 형식 (`audit.py`, line 165-166)
- 시각은 KST `YYYY-MM-DD HH:MM:SS` (`kst_format` 사용, `audit.py`, line 167)
- 파일명 접두어: `audit-log`, 시트명: `감사로그`

### 3-3. 이전 로그 삭제 (`POST /admin/audit/purge`)

**운영자 주의**: 삭제된 로그는 복구할 수 없다. 반드시 엑셀 내보내기로 백업 후 진행하라.

1. 날짜 입력 후 "이전 로그 삭제" 버튼 클릭 → 확인 모달 표시
2. 모달에서 "삭제" 클릭 → `POST /admin/audit/purge` 전송 (CSRF 토큰 포함)
3. 서버 처리 흐름 (`audit.py`, line 173-199):
   - CSRF 검증 (`validate_csrf`)
   - `before` 날짜 파싱 (`date.fromisoformat`) — 형식 오류 시 `?flash=기준일이 올바르지 않습니다&flash_type=error` 리다이렉트
   - `before > 오늘(UTC)` 이면 `?flash=기준일은 오늘 이후일 수 없습니다&flash_type=error` 리다이렉트
   - 기준 시각 생성: `datetime(before.year, before.month, before.day, tzinfo=timezone.utc)` (= 해당일 UTC 00:00)
   - `DELETE FROM audit_logs WHERE created_at < cutoff` 실행
   - 삭제 행위 자체를 `audit.purge` action으로 감사 기록 (`before`, `deleted_count` 포함)
   - `db.commit()`
   - 성공: `?flash=감사로그 {N}건을 삭제했습니다` 리다이렉트 → 녹색 토스트

---

## 4. 개발 참조

### 4-1. 라우트 함수

| 메서드·경로 | 함수 | 파일·라인 |
|-------------|------|-----------|
| `GET /audit` | `audit_list()` | `app/admin/routes/audit.py:134` |
| `GET /audit/export.xlsx` | `audit_export()` | `app/admin/routes/audit.py:153` |
| `POST /audit/purge` | `audit_purge()` | `app/admin/routes/audit.py:173` |

### 4-2. 내부 헬퍼 함수

| 함수 | 파일·라인 | 역할 |
|------|-----------|------|
| `_build_audit_query(pp)` | `audit.py:93` | 목록·엑셀이 공유하는 검색·필터 WHERE 절 생성 |
| `_build_rows(db, logs)` | `audit.py:112` | ORM 행 → 화면/엑셀 공용 dict 변환 |
| `_resolve_names(db, logs)` | `audit.py:53` | actor/target UUID → 이름 배치 조회 |

정렬 가능 컬럼 맵: `_AUDIT_SORT = {"created_at": AuditLog.created_at, "action": AuditLog.action, "actor_type": AuditLog.actor_type}` (`audit.py:42-43`)

target → DB 테이블 맵: `_TARGET_TABLE = {"service": "services", "plan": "plans", "user": "users", "subscription": "subscriptions", "payment": "payments", "card": "cards"}`. 카드 대상은 `external_user_id`로 이름을 표시한다(`_resolve_names`에 `Card` 추가).

### 4-3. 모델 — `AuditLog` (`app/models/audit_log.py`)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | 자동 생성 |
| `actor_type` | `String(10)` | `USER` / `SERVICE` / `SYSTEM` |
| `actor_user_id` | UUID, nullable | USER 행위자일 때 `users.id` |
| `actor_service_id` | UUID, nullable | SERVICE 행위자일 때 `services.id` |
| `action` | `String(100)`, indexed | 행위 식별자 (예: `subscription.cancel`) |
| `target_type` | `String(50)`, nullable | 대상 엔티티 종류 (예: `subscription`) |
| `target_id` | `String(64)`, nullable | 대상 엔티티 PK 문자열 직렬화 |
| `detail` | `JSONB`, nullable | 변경 전·후 값 등 부가 정보 |
| `ip_address` | `String(45)`, nullable | 요청 IP (IPv6 포함 최대 45자) |
| `created_at` | `DateTime(timezone=True)` | 이벤트 발생 시각(UTC), `server_default=func.now()` |

- **삽입 전용**: 수정·삭제하지 않는다(불변 이력). `audit_log.py:18` 클래스 docstring.
- `actor_type`에 따라 `actor_user_id` 또는 `actor_service_id` 중 하나만 채워진다. SYSTEM이면 두 필드 모두 NULL 가능.

### 4-4. 서비스 함수 — `record_audit` (`app/services/audit.py:15`)

```python
async def record_audit(db, *, actor_type, action,
                       actor_user_id=None, actor_service_id=None,
                       target_type=None, target_id=None,
                       detail=None, ip_address=None) -> None
```

`db.add(AuditLog(...))` 만 수행하고 **commit은 호출하지 않는다** (`audit.py:3-6` 모듈 docstring). 호출자가 비즈니스 로직과 같은 트랜잭션 안에서 `await db.commit()`을 해야 상태 변경과 감사 기록이 원자적으로 반영된다.

### 4-5. 주요 action 종류 및 기록 위치

#### 인증 (`app/services/auth.py`)

| action | 라인 | 설명 |
|--------|------|------|
| `auth.login_failed` | `auth.py:104` / `auth.py:131` | 이메일 미존재(line 104) 또는 비밀번호 불일치(line 131) |
| `auth.login` | `auth.py:138` | 로그인 성공 |
| `auth.password_set` | `auth.py:220` | 비밀번호 설정 완료 |
| `user.create_admin` | `auth.py:248` | 시스템 최초 관리자 자동 생성 (`actor_type="SYSTEM"`) |
| `user.password_reset_issued` | `auth.py:283` | 비밀번호 재설정 메일 발송 |

#### 계정 (`app/services/accounts.py`)

| action | 라인 | 설명 |
|--------|------|------|
| `account.create` | `accounts.py:127` | 계정 생성 |
| `account.update` | `accounts.py:181` | 계정 정보 수정 |
| `account.disable` / `account.enable` | `accounts.py:215` | 계정 활성화·비활성화 |
| `account.delete` | `accounts.py:251` | 계정 삭제 |
| `account.assign_service` | `accounts.py:288` | 서비스 담당 추가 |
| `account.unassign_service` | `accounts.py:310` | 서비스 담당 해제 |

#### 서비스 (`app/services/registry.py`, `app/admin/routes/services.py`)

| action | 라인 | 설명 |
|--------|------|------|
| `service.register` | `registry.py:161` | 서비스 등록 |
| `service.rotate_keys` | `registry.py:186` | 서비스 키 재발급 |
| `service.keys_viewed` | `services.py:251` | 서비스 키 조회 |
| `service.update_ips` | `registry.py:198` | 허용 IP 변경 |
| `service.cancel_policy_updated` | `registry.py:223` | 취소 정책 변경 |
| `service.set_status` | `registry.py:242` | 서비스 상태 변경 |
| `service.delete` | `registry.py:275` | 서비스 삭제 |
| `service.set_primary_manager` | `registry.py:308` | 대표 담당자 지정 |

#### 요금제 (`app/services/plans.py`)

| action | 라인 | 설명 |
|--------|------|------|
| `plan.create` | `plans.py:149` | 요금제 생성 |
| `plan.update` | `plans.py:286` | 요금제 수정 |
| `plan.archive` | `plans.py:302` | 요금제 비활성화 (소프트 삭제) |
| `plan.activate` | `plans.py:317` | 요금제 활성화 |
| `plan.delete` | `plans.py` | 요금제 삭제 |
| `plan.bonus_days` | `plans.py` | 사용일 추가(보너스) — 요금제·추가 일수·적용 구독 수 |

#### 구독 (`app/services/subscriptions.py`, `app/services/renewals.py`)

| action | 기록 위치 | actor_type | 설명 |
|--------|-----------|------------|------|
| `subscription.create` | `subscriptions.py:224` | SERVICE | 외부 서비스에서 구독 생성 |
| `subscription.first_payment_unresolved` | `subscriptions.py:243` | SERVICE | 첫 결제 결과 불명 |
| `subscription.first_payment_failed` | `subscriptions.py:262` | SERVICE | 첫 결제 실패 |
| `subscription.cancel` | `subscriptions.py:299` | USER/SERVICE | 구독 취소 |
| `subscription.resume` | `subscriptions.py:415` | USER/SERVICE | 구독 재개 |
| `subscription.change_card` | `subscriptions.py:450` | USER/SERVICE | 카드 변경 |
| `subscription.force_cancel` | `subscriptions.py` | USER | 강제 취소 |
| `subscription.extended` | `subscriptions.py` | USER | 만료일 연장(연장처리) |
| `subscription.usage_added` | `subscriptions.py` | SERVICE/USER | 구독 사용일 추가(외부 API) |
| `subscription.manual_pay` | `subscriptions.py:377` | SERVICE | 수동 결제(정지 복구) |
| `subscription.manual_pay_unresolved` | `subscriptions.py:348` | SERVICE | 수동 결제 결과 불명 |
| `subscription.manual_pay_failed` | `subscriptions.py:359` | SERVICE | 수동 결제 실패 |
| `subscription.renewed` | `renewals.py:305` / `renewals.py:375` | SYSTEM | 자동연장 결제 성공 |
| `subscription.renewal_unresolved` | `renewals.py:337` | SYSTEM | 갱신 결제 결과 불명 |
| `subscription.payment_failed` | `renewals.py:426` | SYSTEM | 갱신 결제 실패 |
| `subscription.suspended` | `renewals.py:409` | SYSTEM | 재시도 소진으로 구독 정지 |
| `subscription.expired` | `renewals.py:201` / `reconciliation.py:152` | SYSTEM | 구독 만료 |

#### 결제 (`app/services/payments.py`, `app/services/reconciliation.py`)

| action | 라인 | 설명 |
|--------|------|------|
| `payment.one_off` | `payments.py:95` | 단건 결제 성공 |
| `payment.one_off_failed` | `payments.py:110` / `payments.py:146` | 단건 결제 실패 |
| `payment.one_off_unresolved` | `payments.py:132` | 단건 결제 결과 불명 |
| `payment.canceled` | `payments.py:226` / `payments.py:230` | 결제 취소 성공 |
| `payment.cancel_failed` | `payments.py:209` / `payments.py:213` | 결제 취소 실패 |
| `payment.reconciled_done` | `reconciliation.py:119` | 결제 정산 확정(성공) |
| `payment.reconciled_failed` | `reconciliation.py:156` | 결제 정산 확정(실패) |

#### 카드 (`app/services/cards.py`)

| action | actor | 설명 |
|--------|-------|------|
| `card.register` | SERVICE | 카드(빌링키) 신규 등록 |
| `card.replace` | SERVICE | 카드 교체(재등록) |
| `card.delete` | SERVICE | 카드 삭제 |
| `card.activate` | USER(관리자) | 카드 활성화 |
| `card.deactivate` | USER(관리자) | 카드 비활성화(결제 차단) |

모든 카드 이벤트의 `detail`은 공통 빌더 `_card_audit_detail()`로 **사용자(`external_user_id`)·마스킹 카드번호(`card_number`)·발급사(`issuer`)**를 남긴다. `service_id`도 detail에 기록하지만 화면에는 표시하지 않고(원시 UUID), 서비스 상세 “이벤트” 섹션의 스코프 필터에만 쓰인다. 자세한 흐름은 `16-card-vault` 참조.

#### 전체 설정 (`app/services/app_settings.py`)

| action | 라인 | 설명 |
|--------|------|------|
| `settings.retry_updated` | `app_settings.py:50` | 갱신 재시도 설정 변경 |
| `settings.admin_ips_updated` | `app_settings.py:86` | 어드민 허용 IP 변경 |
| `server.disabled` / `server.enabled` | `app_settings.py:129` | 결제서버 킬스위치 |

#### 감사 로그 자체

| action | 라인 | 설명 |
|--------|------|------|
| `audit.purge` | `audit.py:194` | 과거 로그 삭제 (삭제 행위 자체를 기록) |

### 4-6. `detail` JSONB 필드에 무엇이 담기나

`detail`은 행위 별로 자유 형식 JSON이다. 화면에는 `detail_summary()` (`audit_labels.py:91-101`)가 `_DETAIL_FIELDS` 맵(`audit_labels.py:66-71`)에 등록된 키만 "라벨 값" 형식으로 요약 표시한다.

주요 키 목록 (`_DETAIL_FIELDS`, `audit_labels.py:66-71`):

| 키 | 화면 라벨 | 대표 사용처 |
|----|-----------|-------------|
| `name` | 이름 | 서비스 등록(`registry.py:164`), 요금제 생성(`plans.py:151`) |
| `email` | 이메일 | 계정 생성(`accounts.py:128`), 계정 삭제(`accounts.py:253`) |
| `role` | 역할 | 계정 생성(`accounts.py:130`) |
| `status` | 상태 | 서비스 상태 변경(`registry.py:244`) |
| `code` | 사유코드 | 결제 실패(`payments.py:113`), 구독 정지(`renewals.py:411`) |
| `reason` | 사유 | 구독 만료(`renewals.py:203`), 로그인 실패(`auth.py:105`) |
| `amount` | 금액 | 단건 결제(`payments.py:99`), 수동 결제(`subscriptions.py:380`) |
| `ips` | IP | 허용 IP 변경(`registry.py:200`) |
| `service_count` | 서비스 수 | 계정 생성(`accounts.py:131`) |
| `external_user_id` | 사용자 | 구독 생성(`subscriptions.py:227`) |
| `old_price` / `new_price` | 기존가 / 변경가 | 요금제 수정(`plans.py:288`) |
| `service_id` | 서비스 | 담당자 배정(`accounts.py:290`) |

그 밖의 키(`before`, `deleted_count`, `trial`, `order_id`, `billing_key_deleted`, `manager_count`, `fee_percent`, `trial_days`, `auto_renew`, `retry_count`, `recovered`, `recovered_via` 등)는 detail JSON에 저장되지만 화면 요약에는 나타나지 않는다. 원본 JSON 전체는 DB에서 직접 조회해야 한다.

**민감정보 주의**:
- `email` 키: 로그인 실패 시 입력된 이메일이 `detail.email`에 기록될 수 있다 (`auth.py:105`). 로그 조회 권한을 SYSTEM_ADMIN으로만 제한하는 이유.
- `ips`: 허용 IP 목록 변경 시 이전·이후 IP 목록이 `ips` 키에 저장된다 (`registry.py:200`).
- `billing_key_deleted`: 결제 실패 시 빌링키 삭제 여부(`subscriptions.py:265`). 상세 조회는 DB 직접 확인.
- `code`: Toss 결제 오류 코드. 에러 원인 분석에 활용 가능하나 외부 공개하지 않는다.

### 4-7. `audit_labels.py` — 한글 변환 레이어 (`app/admin/audit_labels.py`)

| 심볼 | 라인 | 역할 |
|------|------|------|
| `ACTION_LABELS` | `audit_labels.py:3` | action 코드 → 한글 (57개 항목) |
| `ACTOR_TYPE_LABELS` | `audit_labels.py:59` | `USER`/`SERVICE`/`SYSTEM` → 한글 |
| `TARGET_TYPE_LABELS` | `audit_labels.py:60` | target_type → 한글 |
| `_DETAIL_FIELDS` | `audit_labels.py:66` | detail 키 → 화면 요약 라벨 맵 |
| `action_label(action)` | `audit_labels.py:74` | 미등록 코드는 원문 반환 |
| `actor_label(type, email)` | `audit_labels.py:78` | 이메일 있으면 이메일, 없으면 타입 한글 |
| `target_label(type, name)` | `audit_labels.py:84` | "엔티티 · 이름" 형식 |
| `detail_summary(detail)` | `audit_labels.py:91` | `_DETAIL_FIELDS` 키만 요약 |

---

## 5. 주의사항 / 자주 하는 실수

### 운영자

- **Purge 전 반드시 엑셀 백업**: 삭제된 로그는 복구 불가. 기준일 이전 전체가 삭제된다. 확인 모달 문구("필요하면 먼저 엑셀로 내려받으세요")를 따른다.
- **기준일은 오늘 포함 미래 지정 불가**: 기준일이 오늘보다 미래이면 서버가 거부한다 (`audit.py:187-190`). 당일 로그는 삭제할 수 없다(UTC 자정 기준이므로 KST 오전에는 전날 UTC 날짜에 해당하는 로그가 남아있을 수 있다).
- **Purge 자체도 감사 기록**: 누가 몇 건을 삭제했는지 `audit.purge` action으로 남는다 (`audit.py:194-196`).
- **엑셀 내보내기는 전체 결과**: 페이지네이션 무시하고 현재 필터 조건의 전체 데이터를 내려받는다. 데이터가 많으면 응답이 느릴 수 있다.
- **시각은 KST 표시, 저장은 UTC**: 화면의 `YYYY-MM-DD HH:MM:SS`는 KST. Purge의 기준일(`before`)은 UTC 자정(`00:00:00Z`)을 기준으로 처리된다 (`audit.py:191`).

### 개발자

- **`record_audit`은 commit하지 않는다**: `app/services/audit.py:3-6`. 비즈니스 로직과 같은 트랜잭션 안에서 호출해야 원자성이 보장된다. 별도 트랜잭션에서 단독 commit하면 비즈니스 로직 롤백 후에도 감사 기록만 남는 문제가 생긴다.
- **새 action 추가 시 `ACTION_LABELS` 등록**: `app/admin/audit_labels.py:3-57`. 등록하지 않으면 화면에 코드 원문이 표시되고 활동 필터 드롭다운에도 나타나지 않는다.
- **`detail` JSONB 키 추가 시 `_DETAIL_FIELDS` 검토**: 화면 요약에 표시할 키는 `audit_labels.py:66-71`에 추가한다.
- **`_TARGET_TABLE` 맵**: `target_type` 값 추가 시 `audit.py:89-90` 맵도 함께 확장해야 `_resolve_names`가 이름을 조회한다.
- **CSRF 검증**: `POST /audit/purge`는 `validate_csrf(request, ctx)` 로 검증(`audit.py:177`). form에 `<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">` 필수.
- **정렬 컬럼 확장**: `_AUDIT_SORT` 딕셔너리(`audit.py:42-43`)에 등록된 컬럼만 정렬 가능. 미등록 컬럼 지정 시 `PageParams`가 `default_sort`로 폴백한다.

---

## 관련 문서

- [../02-database.md](../02-database.md) — `audit_logs` 테이블 스키마 및 인덱스
- [../13-admin-accounts.md](../13-admin-accounts.md) — SYSTEM_ADMIN / SERVICE_MANAGER 권한 구조
- [README.md](README.md) — 어드민 공통 UI 패턴(검색·정렬·페이지네이션·확인 모달·CSRF·토스트)
