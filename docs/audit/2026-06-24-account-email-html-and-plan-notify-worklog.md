# 계정 설정 메일 HTML 적용 + 요금제 등록 관리자 알림 메일 워크로그

- 날짜: 2026-06-24
- 작업자: seungjinhan
- 요청:
  1. 계정 설정 메일에도 (비밀번호 재설정과 동일한) UI/UX 적용.
  2. 구독 요금제 등록 시에도 관리자에게 메일이 와야 함.

## 1) 계정 설정 메일 HTML
- `app/services/accounts.py` `create_account` — 평문 메일을 `render_action_email`(공용 템플릿)로 교체.
  제목 "관리자 계정 설정 안내", 역할 한글 라벨(시스템 관리자/서비스 담당자), 버튼 "비밀번호 설정하기", 48시간 안내.
  발송은 `email_sender.send(..., html=...)` → 운영은 메모리 큐 경유·감사로그 동일.

## 2) 요금제 등록(plan.created) 관리자 알림 메일
- `app/notifications/admin_notify.py`
  - `EVENT_PLAN_CREATED = "plan.created"` + `AdminNotifier.plan_created` (프로토콜·Email·Recording).
  - 결제주기 한글(`_cycle_label`: 년/월/주 단위, N일마다, N분마다)·혜택 표기(`_benefit_label`: 무료/정액원/정률%/없음) 헬퍼.
  - 메일 항목: 서비스명·요금제명·가격·결제주기·첫 결제 혜택·상시 할인·체험·자동결제·생성자·생성시각.
- `app/services/plans.py` `create_plan` — `admin_notifier=None` 파라미터 추가, 커밋 후 `plan_created` 호출(best-effort).
- `app/admin/routes/plans.py` — 두 생성 라우트(`POST /plans`, `POST /services/{id}/plans`)에 `get_admin_notifier` 주입·전달.
- 수신자: 기존 관리자 알림과 동일하게 활성 SYSTEM_ADMIN 전원.

## 문서/매뉴얼
- `17-feature-notifications.md` §17.10 이벤트 표에 `plan.created` 추가, 항목 설명 보강.
- `07-admin-accounts.md` §7.3 계정 설정 메일이 HTML(CTA 버튼)임을 명시.
- `build.py` 재빌드.

## 검증
- 단위 `tests/unit/test_admin_notify.py` — `plan_created` 디스패치(서비스명 해석·가격·주기·혜택·체험 HTML 포함),
  `_cycle_label`/`_benefit_label` 라벨.
- 통합 `tests/integration/test_admin_notify_flows.py::test_create_plan_triggers_admin_notifier` — create_plan이 trigger.
- **전체 스위트 666 passed**(Postgres 5432 + 임시 Redis 6380).

## 비고
- 정적/메일 변경은 실행 중 컨테이너 재배포 후 반영.
