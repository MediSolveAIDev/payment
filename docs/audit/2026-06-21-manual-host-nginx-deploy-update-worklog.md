# 매뉴얼 배포 절을 호스트 nginx 방식으로 갱신 워크로그

작성일: 2026-06-21
배경: 실제 배포를 `docs/cloud/PAY-VM-ONBOARDING.md`(Public IP 직결 Azure VM + 호스트 nginx + Let's Encrypt)대로 진행함. 매뉴얼(`docs/user_manual/10-install-deploy.md`)이 기존엔 **컨테이너 nginx(3개)** 기준이라 실제 구성(호스트 nginx + app·redis 2개)과 어긋나 갱신.

## 변경 (docs/user_manual/10-install-deploy.md)

- **§1.2 구성 요소**: 다이어그램·표를 "호스트 nginx(80/443) → `127.0.0.1:8000` app → 외부 DB / redis"로 교체. 컨테이너 nginx 행 제거, 호스트 nginx 행 추가. app 노출을 `127.0.0.1:8000`(루프백)으로 명시. 온보딩 실제값(stg `api-stg-pay.medisolveai.com` / prod `api-pay.medisolveai.com`, NSG 22·80·443, Azure) 참고 추가. "왜 호스트 nginx인가"(포트 충돌) 설명 추가.
- **§5.0 전체 절차**: 9단계를 호스트 nginx 순서로 재구성 — ①Docker ②클론(/opt/pay) ③외부 PG ④`.env.prod`(BASE_URL=https FQDN 강조) ⑤app·redis 기동(+자동 마이그레이션, `curl 127.0.0.1:8000` 확인) ⑥`create-admin` ⑦`apt install nginx certbot` + `payment-host.conf` 복사 + `sites-enabled/default` 제거 + `nginx -t`/reload ⑧`certbot --nginx` 발급(자동 갱신) + NSG 안내 ⑨http 301·https 도달 확인.
- **§5.1 사전 준비**: TLS를 `docker/nginx/certs` 배치/openssl 자체서명 → **호스트 nginx + certbot --nginx**로 교체. `docker/nginx/`는 이 방식에서 미사용임을 명시.
- **§5.3 참고**: "외부 포트는 nginx 80/443" → "호스트 nginx 80/443, app은 127.0.0.1:8000".
- **§5.4 문제해결 신설**: 실제 겪은 이슈를 표로 — cert 미발급(emerg), 인증서 valid인데 "Not secure"(reload 누락/리다이렉트), conflicting server name(default 사이트), 502(app 미기동), https 전용 쿠키, 클라이언트 IP 127.0.0.1(헤더 누락).
- **§6 운영 주의사항**: 프로젝트명 분리(3개→app·redis 2개), 클라이언트 IP(호스트 nginx + X-Forwarded-Proto), 인증서 갱신(컨테이너 reload → 호스트 certbot systemd 타이머 자동 갱신)로 수정.

## 검증
- `uv run --with markdown python docs/user_manual/build.py` → 17개 문서 재빌드.
- 잔여 컨테이너-nginx 표현 0건(grep). HTML에 "호스트 nginx" 11곳·"5.4 문제해결" 반영 확인.

## 후속: 문서 10 전면 재구성 (VM 1대 = 호스트 nginx + docker 2개)

요청에 따라 §10 전체를 "1 VM에 셋을 올리는" 구조로 재작성. DB도 외부 관리형이 아니라 **같은 VM의 별도 docker(db_server)** 기준으로 변경(관리형은 대안으로 명시).

- **§1.2 구성 요소**: VM 1대 다이어그램(호스트 nginx → app/redis → docker A PostgreSQL) + 설치방식·노출·설정절 표(3 구성요소). 관리형 DB는 대안 노트.
- **§5 운영 배포 재편**: 5.1 공통 준비(Docker·코드) → **5.2 docker A: PostgreSQL(`db_server/run.sh`, `.env`, payment-postgres, 데이터 영속·HOST_BIND 보안)** → **5.3 docker B: app·redis(`docker-compose.prod.yml`, `.env.prod`의 `DATABASE_URL=host.docker.internal:5432`, 자동 마이그레이션, create-admin)** → 5.4 호스트 nginx+TLS(certbot --nginx) → 5.5 동작확인 → 5.6 운영 → 5.7 문제해결(맨 위에 DB 접속 실패=host.docker.internal 케이스 추가).
- **§6 주의사항**: DB를 docker A(host.docker.internal)로, POSTGRES_PASSWORD 비밀값 항목 추가.
- **§7 백업**: 관리형 PITR → **db_server 컨테이너 `pg_dump`/`pg_restore`(docker exec)** 기준으로 교체, `db_backup_sw` 참고.
- **§2/§4 로컬·테스트**: 동일 원칙(별도 DB docker)으로 정리.

검증: 재빌드 OK, HTML에 "docker A — PostgreSQL"·"docker B — app·redis"·host.docker.internal(8)·payment-postgres(15) 반영 확인.

## 비고
- compose 자체는 앞선 작업(2026-06-21-host-nginx-migration)에서 이미 nginx 제거 + app `127.0.0.1:8000` 노출 완료. 본 작업은 그 구성을 매뉴얼에 동기화 + DB-as-docker 구조로 전면 재구성.
- `docker/README.md`는 여전히 컨테이너 nginx 기준 — 필요하면 별도로 호스트 nginx + db_server 기준으로 갱신 가능(이번 범위는 user_manual).
- 컨테이너 nginx 자산(`docker/nginx/conf.d`·`certs`·`certbot-www`)은 되돌릴 때 대비 보존.
