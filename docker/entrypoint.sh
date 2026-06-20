#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# 컨테이너 시작 스크립트 (Dockerfile의 ENTRYPOINT가 실행)
# ─────────────────────────────────────────────────────────────────────────────
# 하는 일: 앱을 띄우기 "전에" DB 스키마를 최신으로 맞추고(alembic), 그 다음 컨테이너에
#          전달된 명령(CMD = uvicorn ...)을 실행한다.
# - 대상 DB는 DATABASE_URL 환경변수가 가리킨다(alembic.ini의 dev 기본값을 덮어씀 —
#   alembic/env.py 참조).
# - RUN_MIGRATIONS=0 으로 두면 마이그레이션을 건너뛴다. 앱을 여러 대로 늘릴 때
#   "한 대만" 마이그레이션하고 나머지는 건너뛰게 해 중복 적용을 막는 용도.
# ─────────────────────────────────────────────────────────────────────────────
set -e   # 어느 명령이라도 실패하면 즉시 종료 → 마이그레이션 실패 시 앱이 뜨지 않게 한다.

# RUN_MIGRATIONS 가 비어 있으면 기본 '1'(적용)로 본다. '0'일 때만 건너뜀.
if [ "${RUN_MIGRATIONS:-1}" != "0" ]; then
  echo "[entrypoint] applying DB migrations (alembic upgrade head)..."
  alembic upgrade head            # DB 스키마를 최신 리비전(head)까지 적용
else
  echo "[entrypoint] RUN_MIGRATIONS=0 — skipping migrations"
fi

echo "[entrypoint] starting: $*"
# exec: 현재 셸을 CMD(uvicorn)로 "교체"해 PID 1이 되게 한다. 셸이 중간에 끼지 않아
#       종료 시그널(SIGTERM)이 앱에 그대로 전달되어 깔끔하게 종료된다.
exec "$@"
