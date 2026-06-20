# 전체 기능 테스트케이스 작성·실행 리포팅

- **일자**: 2026-06-12
- **요청**: 전체 기능을 테스트할 수 있는 테스트케이스를 만들고, 각각 수행해 결과를 레포팅

## 접근

이 프로젝트는 이미 기능 전반을 덮는 자동 테스트(이번 실행 기준 530건)를 보유하므로,
"테스트케이스 명세"를 별도 문서로 중복 작성하는 대신:

1. **기존 테스트 = TC 카탈로그**로 공식화 — 테스트 함수의 한국어 docstring을 TC 설명으로
   추출하고, 테스트 모듈을 기능 영역 26종으로 매핑
2. 로직 검증 리포트(L-5)에서 확인된 **테스트 공백을 신규 TC 10건으로 보강**
3. 전체 실행(junit) 결과를 TC별로 매핑한 **기능별 실행 리포트**를 생성 — 재실행 가능한
   스크립트로 만들어 인수인계 후에도 같은 리포트를 뽑을 수 있게 함

## 산출물

| 파일 | 내용 |
|---|---|
| `scripts/feature_test_report.py` | **리포트 생성기(신규)** — junit xml + 테스트 docstring(AST) → 기능 영역별 TC 결과표. 새 테스트 파일은 FEATURE_MAP에 등록 |
| `docs/test_report/feature-test-report.md` | **실행 리포트** — 26개 기능 영역 × 530 TC 전수 결과 |
| `tests/unit/test_billing_math_edges.py` | **신규 TC 10건** — 취소 수수료 불변식(fee+refund==amount) 전수 스윕·내림 방향, 월말 클램프 다단계 드리프트(현행 정책 고정), 윤년 경계, WEEK/DAY 대형 주기, 100%/정가 초과 할인의 0원 클램프(계산층 동작 문서화 — 리포트 H-4의 가드 위치 명시), 할인율 범위 검증, 첫/상시 할인 독립성 |

## 실행 결과 (단독 실행 — 동시 pytest 없음 확인 후)

| 대상 | 결과 |
|---|---|
| 본 서버 (EXTENDED 작업분 포함 워킹트리) | **530 passed / 실패 0** (50.4초) |
| sample_service (Django) | **58 passed / 실패 0** |

기능 영역별 전수 결과는 `docs/test_report/feature-test-report.md` 참조 — 전 영역 100% 통과.

## 자동 테스트가 덮지 못하는 범위 (수동/별도 검증 필요)

- **실제 토스 API 연동**: 자동 테스트는 FakeToss 사용. 실 카드등록→결제→취소는
  sample_service 수동 시나리오(README)로 검증 — 토스 테스트 모드 필수
- **이메일 실발송**: RecordingEmailSender로 호출만 검증 — Gmail SMTP 실발송은 수동 1회 확인
- **운영 설정**(TRUST_PROXY_HOPS·HSTS 등): prod 환경 전용 — 배포 체크리스트(new_manual 06장)로 확인
- **알려진 미수정 이슈**(로직 리포트 High 4 등)는 테스트가 "현재 동작"을 고정하고 있음 —
  수정 시 해당 테스트 기대값도 함께 갱신해야 함(예: test_billing_math_edges의 0원 클램프)

## 재생성 방법

```bash
uv run pytest -q --junitxml=/tmp/junit_main.xml
uv run python scripts/feature_test_report.py /tmp/junit_main.xml docs/test_report/feature-test-report.md
```
