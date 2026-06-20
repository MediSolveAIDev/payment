# 2026-06-18 워크로그 — 서비스 매뉴얼 "6. 설치·설정·배포"에 Docker 파일 상세 해설 추가

## 요청
서비스 담당자 매뉴얼의 **6. 설치·설정·배포** 장에 Docker 관련 파일을 추가하고,
각 항목(지시어)이 **어떤 역할을 하는지** 상세 설명을 붙여 달라.

## 대상 파일
- `docs/manual/00-setup.html` (네비게이션상 "6. 설치·설정·배포")
  - 이 매뉴얼(`docs/manual/`)은 빌드 스크립트가 없는 **하드코딩 HTML**이라 HTML을 직접 편집(= 게시본).
  - 정식 개발자 매뉴얼(`docs/dev_manual/`, manual.html 재빌드 대상)과는 별개 문서.

## 변경 내용
1. **1.3 저장소 구성 파일** 표 아래에 PART 4로의 교차 링크 문단 추가.
2. PART 3 끝, 마무리 tip 콜아웃 앞에 **PART 4 · 구성 파일 상세 해설** 신설:
   - 4.1 `Dockerfile` — 전체 내용 + 지시어별 역할 표(FROM/ENV/uv sync 캐시 전략/비루트/ENTRYPOINT·CMD 등)
   - 4.2 `docker-compose.prod.yml` — app·redis·nginx 키별 역할 표(env_file 우선순위, REDIS_URL 고정, depends_on healthy, expose vs ports, healthcheck 등)
   - 4.3 `docker/entrypoint.sh` — set -e / RUN_MIGRATIONS 분기 / exec "$@" 역할 표
   - 4.4 `docker/nginx/conf.d/payment.conf` — upstream·80→443·TLS·보안헤더·proxy 헤더(XFF↔HOPS 정합)·read_timeout 표
   - 4.5 `.dockerignore` — 제외 항목과 `!docker/entrypoint.sh` 예외 설명
   - 4.6 (참고) 개발용 `docker-compose.yml` — 루프백 바인딩·개발 전용 자격증명 경고
   - 파일 4개가 맞물리는 흐름을 설명하는 note 콜아웃 + 1.10/PART 2 교차 링크.

## 검증
- 태그 균형 점검: `<div>` 64/64, `<table>`/`</table>` 17/17, `<pre>`/`</pre>` 12/12.
- 게시된 실제 저장소 파일 내용을 그대로 인용(요약 정리)했는지 원본과 대조.

## 메모
- 이 매뉴얼은 별도 빌드 단계가 없어 HTML 직접 수정으로 반영 완료(추가 재빌드 불필요).

---

## 추가 작업 — Docker 파일 본문 주석 상세화
요청: "도커관련 문서에 주석을 상세하게 붙여 주세요" → 매뉴얼이 아니라 **실제 Docker 파일**에
줄/지시어별 상세 주석을 보강(지시어·값은 일절 변경하지 않고 주석만 추가/확장).

대상 파일:
- `Dockerfile` — FROM/ENV 각 항목·uv 캐시 전략(`--frozen`/`--no-dev`/`--no-install-project`)·비루트·ENTRYPOINT/CMD 의미를 줄별 주석화.
- `docker-compose.prod.yml` — app·redis·nginx 키별 주석(env_file 우선순위, environment 고정 이유, depends_on healthy, expose vs ports, healthcheck 필드, redis `--save`/`--appendonly`, 볼륨).
- `docker-compose.yml`(개발) — 루프백 바인딩 보안 이유, 개발 전용 자격증명 경고, init-db/볼륨.
- `docker/entrypoint.sh` — `set -e`, RUN_MIGRATIONS 분기, `exec "$@"`(PID 1·시그널) 주석.
- `docker/nginx/conf.d/payment.conf` — upstream keepalive, 80→443, TLS/암호/세션캐시, 보안헤더, proxy 헤더(XFF↔HOPS), read_timeout 주석.
- `.dockerignore` — 그룹별 제외 의도 + `!docker/entrypoint.sh` 예외 설명.

검증:
- `docker compose -f docker-compose.prod.yml config -q` / `docker-compose.yml config -q` → 둘 다 스키마 유효.
- `sh -n docker/entrypoint.sh` → 셸 문법 OK.
- nginx 핵심 디렉티브(upstream/listen 443 ssl/ssl_certificate/proxy_pass/return 301/read_timeout 90s) 보존 확인.
