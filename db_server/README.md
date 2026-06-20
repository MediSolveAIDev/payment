# db_server — PostgreSQL 단독 인스턴스(Docker)

앱서버와 **분리된 DB 전용 인스턴스**에서 PostgreSQL 16을 Docker로 띄우는 스크립트입니다.
(앱은 `docker-compose.prod.yml`로 따로 띄우고, 이 DB에 `DATABASE_URL`로 접속합니다.)

## 빠른 시작

```bash
cd db_server
cp .env.example .env          # ① 값 채우기 — POSTGRES_PASSWORD 반드시 변경
chmod +x run.sh
./run.sh                      # ② 기동
docker logs -f payment-postgres   # ③ 'database system is ready to accept connections' 확인
```

## 구성 파일

| 파일                    | 역할                                                                                |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `.env.example` → `.env` | 계정·DB명·포트·데이터 경로·튜닝 설정(비밀값 포함, Git 미추적)                       |
| `run.sh`                | `.env`를 읽어 `docker run`으로 PostgreSQL 컨테이너를 기동(멱등·헬스체크·자동재시작) |

## 주요 동작

- **데이터 영속**: `DATA_DIR`(호스트 경로)를 `/var/lib/postgresql/data`에 마운트 → 컨테이너를 지워도 데이터 보존.
- **자동 복구**: `--restart unless-stopped` → 호스트 재부팅 후에도 자동 기동.
- **헬스체크**: `pg_isready`로 상태 확인(`docker inspect`로 health 조회).
- **멱등**: 이미 같은 이름의 컨테이너가 있으면 새로 만들지 않고 안내만 한다.

## 보안 체크리스트

- [ ] `POSTGRES_PASSWORD`를 강한 값으로 변경(예시 기본값이면 스크립트가 거부).
- [ ] `HOST_BIND`를 **사설 IP**로 지정하거나, `0.0.0.0`이면 **방화벽/보안그룹에서 앱서버 IP만** 5432 허용.
- [ ] DB는 **인터넷에 직접 노출 금지**.
- [ ] `DATA_DIR`은 DB 데이터 전용 디스크 권장.

## 자주 쓰는 운영 명령

```bash
docker start  payment-postgres            # 멈춘 컨테이너 시작
docker stop   payment-postgres            # 중지(데이터 유지)
docker rm -f  payment-postgres && ./run.sh  # 재생성(데이터는 DATA_DIR에 보존)
docker exec -it payment-postgres psql -U payment -d payment   # psql 접속
```

## 백업과의 관계

백업 프로그램(`db_backup_sw`)은 **이 DB 서버에서 로컬로** 실행되어 백업 파일을 만듭니다(별도 계획 참조).
같은 호스트이므로 컨테이너의 `pg_dump`를 그대로 쓸 수 있습니다:

```bash
docker exec payment-postgres pg_dump -U payment -Fc payment > payment_$(date +%Y%m%d-%H%M).dump
```
