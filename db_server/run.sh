#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL 단독 인스턴스를 Docker로 기동하는 실행 스크립트
# ─────────────────────────────────────────────────────────────────────────────
# 용도: 앱서버와 분리된 "DB 전용 인스턴스"에서 PostgreSQL 16 컨테이너 1개를 띄운다.
#       설정값은 같은 폴더의 .env 에서 읽는다(.env.example 참고).
# 특징: 데이터 영속(호스트 볼륨) · 헬스체크 · 재부팅 후 자동 복구(restart) · 멱등 실행.
#
#   사용:   cp .env.example .env   # 값(특히 비밀번호) 채우기
#           ./run.sh               # 기동
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

# 1) .env 로드 ----------------------------------------------------------------
if [ ! -f .env ]; then
  echo "[!] .env 가 없습니다. 먼저 만들고 값을 채우세요: cp .env.example .env" >&2
  exit 1
fi
set -a; . ./.env; set +a   # .env 의 변수들을 환경변수로 적재

# 2) 필수값 검증 --------------------------------------------------------------
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 가 비어 있습니다(.env 에 설정)}"
case "$POSTGRES_PASSWORD" in
  *CHANGE_ME*|*변경*) echo "[!] POSTGRES_PASSWORD 가 예시 기본값입니다 — 반드시 변경하세요." >&2; exit 1;;
esac

# 3) 기본값 채우기 ------------------------------------------------------------
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-payment-postgres}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
POSTGRES_USER="${POSTGRES_USER:-payment}"
POSTGRES_DB="${POSTGRES_DB:-payment}"
HOST_BIND="${HOST_BIND:-0.0.0.0}"
HOST_PORT="${HOST_PORT:-5432}"
# 데이터 저장: DATA_DIR(호스트 경로)이 있으면 그 경로에 바인드, 비어 있으면 Docker
# 명명 볼륨(DATA_VOLUME)을 사용한다. 명명 볼륨은 호스트 디렉터리 생성/권한이 필요 없어
# 기본값으로 안전하다(root 없이 동작).
DATA_VOLUME="${DATA_VOLUME:-payment_pgdata}"
DATA_DIR="${DATA_DIR:-}"
TZ="${TZ:-UTC}"
if [ -n "$DATA_DIR" ]; then DATA_DESC="$DATA_DIR (호스트 경로)"; else DATA_DESC="docker 볼륨 '$DATA_VOLUME'"; fi

# 4) docker 설치/구동 확인 ----------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "[!] docker 가 설치돼 있지 않습니다." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "[!] docker 데몬에 접근할 수 없습니다(권한/기동 확인)." >&2; exit 1; }

# 5) 이미 있으면 중복 생성하지 않음(멱등) -------------------------------------
if docker ps -a --format '{{.Names}}' | grep -qx "$POSTGRES_CONTAINER"; then
  echo "[i] '$POSTGRES_CONTAINER' 컨테이너가 이미 존재합니다. 현재 상태:"
  docker ps -a --filter "name=^${POSTGRES_CONTAINER}$" \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  echo
  echo "    멈춤이면 시작:   docker start $POSTGRES_CONTAINER"
  echo "    완전 재생성:     docker rm -f $POSTGRES_CONTAINER && ./run.sh   (데이터는 ${DATA_DESC} 에 보존)"
  exit 0
fi

# 6) 데이터 저장소 준비(마운트 인자 구성) -------------------------------------
if [ -n "$DATA_DIR" ]; then
  # 호스트 경로 바인드: 디렉터리를 만들 수 있어야 한다(권한 없으면 친절히 안내).
  if ! mkdir -p "$DATA_DIR" 2>/dev/null; then
    echo "[!] DATA_DIR 생성/쓰기 권한이 없습니다: $DATA_DIR" >&2
    echo "    아래 중 하나로 해결하세요:" >&2
    echo "    1) .env 의 DATA_DIR 을 '쓰기 가능한 경로'로 변경 (예: \$HOME/payment-postgres/data)" >&2
    echo "    2) DATA_DIR 을 비워 Docker 명명 볼륨('$DATA_VOLUME') 사용 (권장, 권한 불필요)" >&2
    echo "    3) 해당 경로를 미리 만들고 소유권을 넘기기:" >&2
    echo "         sudo mkdir -p '$DATA_DIR' && sudo chown \$(id -u):\$(id -g) '$DATA_DIR'" >&2
    exit 1
  fi
  MOUNT_ARG=(-v "${DATA_DIR}:/var/lib/postgresql/data")
else
  # 명명 볼륨: docker 데몬이 관리하므로 호스트 디렉터리/권한이 필요 없다.
  MOUNT_ARG=(-v "${DATA_VOLUME}:/var/lib/postgresql/data")
fi

# 7) (선택) 성능 튜닝 인자 구성 ----------------------------------------------
tuning=()
[ -n "${PG_SHARED_BUFFERS:-}" ] && tuning+=(-c "shared_buffers=${PG_SHARED_BUFFERS}")
[ -n "${PG_MAX_CONNECTIONS:-}" ] && tuning+=(-c "max_connections=${PG_MAX_CONNECTIONS}")

# 8) docker run 인자 조립 -----------------------------------------------------
args=(run -d
  --name "$POSTGRES_CONTAINER"
  --restart unless-stopped                       # 컨테이너/호스트 재부팅 후 자동 복구
  -e POSTGRES_USER="$POSTGRES_USER"
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD"
  -e POSTGRES_DB="$POSTGRES_DB"                   # 최초 1회 이 DB 자동 생성
  -e TZ="$TZ" -e PGTZ="$TZ"
  -p "${HOST_BIND}:${HOST_PORT}:5432"            # 호스트 ${HOST_BIND}:${HOST_PORT} → 컨테이너 5432
  "${MOUNT_ARG[@]}"                              # 데이터 영속(호스트 경로 또는 명명 볼륨)
  --shm-size=256m                                # 정렬/해시 등 공유메모리(대형 쿼리 안정성)
  --health-cmd "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"
  --health-interval 10s --health-timeout 3s --health-retries 5
  "$POSTGRES_IMAGE"
)
# 튜닝 인자가 있으면 기본 CMD(postgres)를 덮어써 -c 옵션을 전달
if [ "${#tuning[@]}" -gt 0 ]; then
  args+=(postgres "${tuning[@]}")
fi

echo "[*] PostgreSQL 컨테이너 기동: $POSTGRES_CONTAINER ($POSTGRES_IMAGE)"
echo "    데이터 저장: $DATA_DESC"
docker "${args[@]}"

# 9) 안내 ---------------------------------------------------------------------
cat <<EOF

[✓] 기동 명령 실행 완료. 다음으로 확인하세요:
    상태:   docker ps --filter name=$POSTGRES_CONTAINER
    로그:   docker logs -f $POSTGRES_CONTAINER
    헬스:   docker inspect --format '{{.State.Health.Status}}' $POSTGRES_CONTAINER
    접속:   docker exec -it $POSTGRES_CONTAINER psql -U $POSTGRES_USER -d $POSTGRES_DB

[i] 앱(payment_system)의 .env.prod 에 넣을 DATABASE_URL 예시:
    postgresql+asyncpg://$POSTGRES_USER:<비밀번호>@<이 서버의 사설 IP>:$HOST_PORT/$POSTGRES_DB
    (관리형이 아니라 자체 설치이므로 ?ssl=require 는 불필요. 단, 사설망/방화벽으로 앱서버만 허용)
EOF
