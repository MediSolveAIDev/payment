# QA 테스트케이스 자동 실행 & 리포트

매뉴얼 **10장(QA 테스트케이스)**의 140개 케이스를 저장소의 자동화 테스트(pytest)에 매핑해
실행하고, 결과를 **단일 HTML 리포트**(`qa-report.html`)로 만든다.

```
docs/manual/qa/
├─ qa_cases.py   # 140 케이스 ↔ pytest 함수 매핑 (편집해서 유지보수)
├─ run_qa.py     # 실행 + junit 파싱 + HTML 리포트 생성 (표준 라이브러리만 사용)
├─ README.md     # 이 문서
└─ qa-report.html# 실행 결과 리포트 (run_qa.py가 생성)
```

## 사전 준비
프로젝트 테스트 실행 환경이 필요하다(0장 설치와 동일).
```bash
docker compose up -d      # PostgreSQL(5433) · Redis(6380)
uv sync                   # 의존성 설치 (Python 3.13)
```

## 실행
저장소 루트에서:
```bash
# ① pytest 실행 → 결과를 HTML 리포트로
uv run python docs/manual/qa/run_qa.py

# ② 이미 만든 junit 결과로 리포트만 생성
uv run pytest --junitxml=results.xml
python docs/manual/qa/run_qa.py --from-xml results.xml

# ③ 모의 데이터로 리포트 형식 미리보기 (DB 불필요)
python docs/manual/qa/run_qa.py --demo --out docs/manual/qa/qa-report.sample.html
```
실행이 끝나면 `docs/manual/qa/qa-report.html`을 브라우저로 열어 본다.
불합격이 1건이라도 있으면 종료 코드 `1`을 반환하므로 **CI에 그대로 연결**할 수 있다.

## 리포트가 보여주는 것
- 상단 배너(전체 통과/불합격 건수), **자동 실행 합격률**, P1 합격률
- 요약 카드(합격·불합격·부분/미발견·건너뜀·수동)
- 우선순위(P1/P2/P3)별 합격 막대
- 모듈 A~L별 표: `케이스 ID · 우선순위 · 케이스 · 결과 · 매핑된 자동화 테스트`

## 결과 상태 의미
| 상태 | 의미 |
|---|---|
| 합격(PASS) | 매핑된 자동화 테스트가 모두 통과 |
| 불합격(FAIL) | 매핑된 테스트 중 하나라도 실패 |
| 부분(PARTIAL) | 일부만 결과에 존재(매핑 일부 누락) |
| 미발견(N/A) | 매핑한 테스트명이 결과에 없음 → `qa_cases.py` 갱신 필요 |
| 건너뜀(SKIP) | pytest가 skip 처리 |
| 수동(MANUAL) | 자동화 대상이 아니라 사람이 확인 (현재 매핑상 없음) |

## 유지보수
- 케이스를 추가/수정하려면 `qa_cases.py`의 `CASES` 리스트만 편집한다(ID는 매뉴얼 10장과 일치시킬 것).
- 테스트 함수명이 바뀌어 `미발견(N/A)`이 뜨면, 해당 케이스의 매핑 함수명을 새 이름으로 바꾼다.
  현재 사용 가능한 테스트명 목록: `grep -rhoE 'def test_[a-z0-9_]+' tests/ | sed 's/def //' | sort -u`

> 참고: 이 도구는 `tests/`의 코드를 실행만 하며 앱 코드를 변경하지 않는다.
> pytest 실행에는 프로젝트 환경(Python 3.13 + DB/Redis)이 필요하지만, 리포트 생성·파싱은 표준 라이브러리만 쓴다.
