# 사용자 매뉴얼에 「상태값 사전」 장 추가 워크로그

작성일: 2026-06-21
요청: 각 상태값의 설명·의미를 상세히 다루는 메뉴(장) 하나 추가.

## 변경
- **신규 문서**: `docs/user_manual/18-status-reference.md` — 제목 `# 상태값 사전 — 모든 상태의 의미`(부록형 무번호, 네비 배지 `·`).
- **build.py**: `DOCS`에서 `09-dashboard` 바로 뒤, `10-install-deploy` 앞에 `("18-status-reference.md", "사용자 매뉴얼")` 삽입 → **사용자 매뉴얼 그룹 끝**에 위치(09 다음, 개발자 그룹 10 앞). 페이저도 09→상태값사전→10으로 연속.
- 재빌드: 19개 문서.

## 내용(소스 대조: app/models/enums.py + 한글 라벨맵)
1. **구독 상태**(SubscriptionStatus 7종): TRIAL/ACTIVE/PAST_DUE/SUSPENDED/CANCELED/EXTENDED/EXPIRED — 화면 표시(체험·활성·미수·정지·취소·연장처리·만료, `_SUB_STATUS_KO` 일치), **서비스 이용 가능 여부**(ACCESS_ALLOWED_STATUSES: TRIAL·ACTIVE·PAST_DUE·CANCELED·EXTENDED), 가능한 동작, 상태 전이도(ASCII), "열린 구독"(EXPIRED 제외) 개념.
2. **결제 상태**(PaymentStatus): PENDING/DONE/FAILED/CANCELED + 타임아웃=PENDING·부분취소=DONE 주의.
3. **결제 종류·회차**(PaymentKind/PaymentType): SUBSCRIPTION/ONE_OFF, FIRST/RENEWAL/RETRY/ONE_OFF.
4. **계정 상태·역할**(UserStatus/UserRole): PENDING/ACTIVE/LOCKED/DISABLED/DELETED, SYSTEM_ADMIN/SERVICE_MANAGER.
5. **요금제 상태·설정값**(PlanStatus/BillingCycle/FirstPaymentType/DiscountType).
6. **서비스 상태·카드 상태**(ServiceStatus ACTIVE/INACTIVE, 카드 활성/비활성).
7. **(참고) 내부 처리 상태**(WebhookStatus RECEIVED/PROCESSED/IGNORED/FAILED — 토스→서버 인입 웹훅).
- 각 절에서 관련 장(03·05·06·04·02·15)으로 교차링크.

## 검증
- 재빌드 19개, `18-status-reference.html` 생성, 사이드바 사용자 그룹 끝 배치 확인, 내부 링크(.md→.html) 정상, 잔여 `.md` 링크 0.
- 헤드리스 렌더 캡처로 구독 상태 표·색상 배지·우측 목차 정상 표시 확인.
- 라벨은 실제 UI(`미수` 등)와 일치하도록 코드 라벨맵 기준.
