# ─────────────────────────────────────────────────────────────────────────────
# payment_system(FastAPI) 운영 이미지
# ─────────────────────────────────────────────────────────────────────────────
# 무엇을 만드나: uv로 "잠금(lock)된" 의존성을 설치하고 uvicorn으로 앱을 실행하는
#               단일 컨테이너 이미지.
# DB는 왜 없나: PostgreSQL은 이미지에 굽지 않고, 외부 관리형 DB(Azure Database for
#              PostgreSQL/RDS 등)를 런타임에 DATABASE_URL 로 연결한다. 돈 기록(DB)을
#              앱과 분리해 백업·보안·확장을 단순하게 가져가기 위함.
# 빌드:  docker compose -f docker-compose.prod.yml build   (또는 up -d --build)
# ─────────────────────────────────────────────────────────────────────────────

# 베이스 이미지: 데비안 slim + Python 3.13.
#  - slim = 빌드 도구/문서가 빠진 작은 변형 → 이미지 용량·공격면이 작다.
FROM python:3.13-slim

# 파이썬 런타임 위생 + uv 동작 설정(빌드/기동 속도·재현성에 직접 영향).
ENV PYTHONUNBUFFERED=1 \
    # ↑ stdout/stderr 버퍼링 끔 → 로그가 즉시 docker logs 로 흘러나온다(지연 없음).
    PYTHONDONTWRITEBYTECODE=1 \
    # ↑ 실행 중 .pyc 캐시 파일을 만들지 않음(컨테이너엔 불필요한 군더더기).
    UV_COMPILE_BYTECODE=1 \
    # ↑ 반대로 "설치 시점"에 바이트코드를 미리 컴파일 → 첫 요청 기동이 빨라진다.
    UV_LINK_MODE=copy \
    # ↑ uv가 패키지를 하드링크 대신 복사로 배치(레이어/마운트 경계에서 안전).
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    # ↑ 가상환경 위치를 고정(아래 PATH와 짝).
    PATH="/app/.venv/bin:$PATH"
    # ↑ venv 실행파일(uvicorn·alembic 등)을 PATH 앞에 올려 이름만으로 호출 가능.

# 이후 모든 명령의 기준 작업 디렉터리.
WORKDIR /app

# uv 설치: 공식 이미지에서 정적 바이너리만 복사해 온다(pip보다 빠르고 캐시 친화적).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 1) 의존성만 먼저 설치 — "레이어 캐시 전략".
#    의존성 정의 파일만 먼저 복사하므로, 앱 코드만 바뀌면 무거운 아래 RUN 레이어는
#    재사용되어 재빌드가 빨라진다.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
#       --frozen              : uv.lock 을 그대로 설치(재해석/갱신 금지) → 빌드 재현성.
#       --no-dev              : 테스트·린트 등 개발 전용 의존성 제외(이미지 슬림화).
#       --no-install-project  : 이 단계에선 앱 코드는 아직 설치하지 않음(의존성만 분리).

# 2) 앱 소스 + 마이그레이션 파일 복사.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# 로그인 페이지에서 /manual 로 서빙하는 "서비스 담당자 매뉴얼"(정적 사이트)을 이미지에 포함.
# app/main.py 가 /app/docs/manual 을 StaticFiles로 마운트하므로 이 경로에 그대로 둔다.
# (.dockerignore 에서 docs 중 docs/manual 만 빌드 컨텍스트에 남겨 둠)
COPY docs/manual ./docs/manual
# 새 '사용·개발 매뉴얼'(app/main.py 가 /user-manual 로 정적 서빙).
COPY docs/user_manual ./docs/user_manual
# 시작 스크립트(아래 ENTRYPOINT가 실행)를 복사하고 실행 권한 부여.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 비루트 실행: 전용 계정(uid 10001)으로 떨어뜨려 컨테이너 탈취 시 피해를 줄인다.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# 앱이 듣는 포트(문서적 선언). 실제 외부 공개 여부는 compose가 결정한다(여기선 미공개).
EXPOSE 8000

# 컨테이너가 켜지면 "항상" 엔트리포인트가 먼저 실행된다 → DB 마이그레이션을 선행하고
# 그 다음 아래 CMD를 exec 로 교체 실행한다(entrypoint.sh 참조).
ENTRYPOINT ["entrypoint.sh"]

# 엔트리포인트가 마지막에 실행할 실제 앱 명령.
#  - 0.0.0.0:8000 으로 듣되, 앱 포트는 외부에 직접 공개하지 않고 nginx만 접근한다.
#  - --proxy-headers / --forwarded-allow-ips * : nginx가 붙여 준 X-Forwarded-* 헤더를
#    신뢰해 진짜 클라이언트 IP·프로토콜을 인식(IP 화이트리스트 정합).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
