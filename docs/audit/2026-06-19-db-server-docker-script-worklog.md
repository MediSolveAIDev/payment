# 2026-06-19 워크로그 — PostgreSQL 단독 인스턴스 Docker 실행 스크립트

## 요청
PostgreSQL을 (앱과 분리된) 인스턴스 하나에 Docker로 단독으로 띄우려고 한다.
docker 실행 스크립트를 만들어 달라.

## 산출물 (`db_server/`)
- `run.sh` — `.env`를 읽어 `docker run`으로 PostgreSQL 16 컨테이너를 기동하는 실행 스크립트.
  - 데이터 영속(호스트 볼륨), `--restart unless-stopped`, 헬스체크(`pg_isready`),
    `--shm-size=256m`, 선택적 성능 튜닝(`-c shared_buffers/max_connections`),
    멱등(이미 있으면 재생성 안 함), 비밀번호 기본값/누락 가드.
- `.env.example` — 컨테이너/이미지/계정/DB/포트 바인딩/데이터 경로/타임존/튜닝 설정.
- `README.md` — 빠른 시작, 보안 체크리스트, 운영 명령, 백업 연계.

## 설계 결정
- **이미지 postgres:16-alpine** — 앱(payment_system)과 메이저 버전 일치(pg_dump/복원 호환).
- **데이터: 호스트 경로 바인드**(`DATA_DIR`) — 전용 디스크 지정 용이, 컨테이너 삭제와 무관하게 보존.
- **포트 노출 보안**: `HOST_BIND` 기본 0.0.0.0이되, 사설 IP 바인딩 또는 방화벽으로 앱서버만 허용하도록 README/주석에 강조. 인터넷 직접 노출 금지.
- **init-db.sql 미마운트**: 기존 `scripts/init-db.sql`은 `payment_test`(테스트용) 생성이라 운영 DB 서버엔 불필요 → 제외. 앱 DB(`payment`)는 `POSTGRES_DB`로 최초 1회 자동 생성.
- **raw docker run 선택**: 요청이 "실행 스크립트"이고 단일 컨테이너라 cron/restart로 충분. (compose 버전이 필요하면 추가 가능.)

## 검증
- `bash -n run.sh` 문법 OK.
- `.env` 부재 시 가드 동작(exit 1) 확인.
- **엔드투엔드 스모크 테스트**: 일회용 컨테이너(포트 55432, 임시 DATA_DIR)로 기동 →
  11초 만에 health=healthy → `psql` 접속 성공(current_database=payment) →
  튜닝 반영 확인(shared_buffers=128MB) → 컨테이너·임시데이터 정리.

## 수정 — DATA_DIR 권한 오류(mkdir Permission denied) 대응
증상: 기본 `DATA_DIR=/var/lib/payment-postgres/data` 가 비-root에서 생성 불가 → `mkdir: Permission denied`.
변경:
- **기본 저장소를 Docker 명명 볼륨(`DATA_VOLUME=payment_pgdata`)으로 전환** — 호스트 디렉터리/권한 불필요.
- `DATA_DIR`(호스트 경로)은 선택. 값이 있으면 바인드, 생성 실패 시 **해결책 3가지를 안내하고 종료**(크래시 방지).
- `.env.example` 기본값에서 DATA_DIR 주석 처리(명명 볼륨이 기본), 전용 디스크 사용 시 사전 mkdir/chown 예시 추가.
검증: 명명 볼륨 경로 기동→healthy(11s)→psql OK; 권한 없는 호스트 경로는 친절한 안내 후 exit 1.

## 후속(미진행)
- 이 DB 서버에서 동작할 백업 프로그램(`db_backup_sw`)은 `docker exec payment-postgres pg_dump ...`로
  연계 가능(README에 예시 기록).
- 필요 시 docs/manual 6장(설치·설정·배포)에 "자체 설치 PostgreSQL(Docker)" 옵션 추가 검토.
