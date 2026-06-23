"""애플리케이션 전역 설정 모듈.

환경변수(.env 또는 OS 환경)를 pydantic-settings로 읽어 단일 ``Settings`` 인스턴스로
공급한다. 비밀값(encryption_key 등)은 .env에만 보관하며 소스에는 기본값을
빈 문자열로 유지해 실수로 하드코딩되지 않도록 한다.

T7 컷오버: 전역 toss_secret_key 제거. 각 서비스의 토스 시크릿 키는 Service 모델의
toss_secret_key_encrypted 컬럼(AES-GCM 암호화)에 서비스별로 저장한다.

환경(dev/prod) 분리:
  OS 환경변수 ``APP_ENV``(없으면 ``ENVIRONMENT``, 기본 "dev")로 실행 환경을 정하고,
  공통 ``.env``를 먼저 읽은 뒤 환경별 ``.env.<env>``(예: .env.dev / .env.prod)로 덮어쓴다.
  즉 공통값은 .env에, 환경마다 다른 값(URL·키·플래그 등)은 .env.<env>에 둔다.
  예) APP_ENV=prod uvicorn app.main:app  →  .env + .env.prod 로드
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _active_env() -> str:
    """현재 실행 환경 이름을 반환한다. APP_ENV > ENVIRONMENT > "dev" 순으로 결정."""
    raw = os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or "dev"
    return raw.strip().lower()


def _env_files() -> tuple[str, ...]:
    """읽을 .env 파일 목록(뒤쪽이 우선). 존재하지 않는 파일은 pydantic이 자동으로 건너뛴다.

    (".env", ".env.<env>") 순서로 반환 — 공통 .env를 환경별 파일이 덮어쓴다.
    """
    return (".env", f".env.{_active_env()}")

# 토스페이먼트 웹훅 발신 IP 목록 (공식 문서 기준).
# webhook_ip_check_enabled=True 일 때 이 목록 외 IP는 웹훅을 거부한다.
TOSS_WEBHOOK_IPS = [
    "13.124.18.147", "13.124.108.35", "3.36.173.151", "3.38.81.32",
    "115.92.221.121", "115.92.221.122", "115.92.221.123",
    "115.92.221.125", "115.92.221.126", "115.92.221.127",
]


class Settings(BaseSettings):
    """환경변수 기반 전역 설정.

    .env 파일 또는 OS 환경변수에서 값을 읽는다. extra="ignore"로
    선언되지 않은 변수는 조용히 무시해 배포 환경의 잡다한 변수와 충돌하지 않는다.
    """

    # 공통 .env + 환경별 .env.<env>를 순서대로 로드(뒤 파일이 우선). _env_files() 참조.
    model_config = SettingsConfigDict(
        env_file=_env_files(), env_file_encoding="utf-8", extra="ignore")

    environment: Literal["dev", "test", "prod" , "stg"] = "dev"
    # 이메일 내 링크 생성 등에 사용되는 서버 공개 URL.
    base_url: str = "http://localhost:8000"
    # asyncpg 드라이버를 사용하는 비동기 PostgreSQL 연결 문자열.
    # DB는 별도 docker로 따로 구성한다(개발·배포 공통) — 기본값은 외부 PostgreSQL(host 5432).
    # 실제 값은 .env / .env.prod 의 DATABASE_URL 로 덮어쓴다.
    database_url: str = "postgresql+asyncpg://payment:Payment!2002@localhost:5432/payment"
    redis_url: str = "redis://localhost:6380/0"
    # DB 커넥션 풀 설정(감사 Phase 1 — 성능 M3). 총 최대 커넥션 = pool_size + max_overflow.
    # 토스 API 지연 시 커넥션 점유가 길어질 수 있어 기본값을 SQLAlchemy 기본(5+10)보다 상향.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # 풀 고갈 시 커넥션 대기 한도(초) — 초과 시 오류로 즉시 드러나게 한다.
    db_pool_timeout: int = 30
    # 커넥션 재활용 주기(초) — DB/로드밸런서의 유휴 연결 강제 종료보다 짧게 유지.
    db_pool_recycle: int = 1800
    # AES-256-GCM 키: base64 인코딩된 32바이트. 빌링키·HMAC secret DB 저장 시 암호화에 사용.
    encryption_key: str = ""
    # T7 컷오버: 전역 toss_secret_key 제거 — 서비스별 키는 Service.toss_secret_key_encrypted에 저장.
    toss_api_base_url: str = "https://api.tosspayments.com"
    # 토스 API HTTP 타임아웃(초). 자동결제 승인은 토스 명세상 최대 60초 → read에 여유,
    # connect는 짧게. 운영 중 토스 지연 양상에 맞춰 조정 가능.
    toss_read_timeout_seconds: float = 65.0
    toss_connect_timeout_seconds: float = 5.0
    # 어드민 세션 만료 시간(초). 기본 1800초(30분) — 미사용 방치 계정 보호.
    session_ttl_seconds: int = 1800
    # 어드민 세션 절대 수명(초). 기본 43200초(12시간) — 감사 Phase 2(보안 L-5).
    # 유휴 TTL(session_ttl_seconds)은 활동 시마다 연장되므로, 탈취된 세션이
    # 계속 사용되면 영구 유효해진다 — 생성 후 이 시간이 지나면 활동과 무관하게 파기.
    session_absolute_ttl_seconds: int = 43200
    # 외부 서비스의 HMAC 요청 서명 타임스탬프 허용 오차(초).
    # 재전송 공격 방지: 현재 시각과 ±300초 초과 시 요청 거부.
    hmac_timestamp_tolerance_seconds: int = 300
    # HMAC 요청 nonce 보관 시간(초) — 같은 nonce 재사용(재전송 공격) 차단 윈도우.
    hmac_nonce_ttl_seconds: int = 600
    # 일반 API 분당 허용 요청 수. Redis sliding-window 카운터로 계산.
    rate_limit_per_minute: int = 120
    # 결제 API는 별도로 더 낮게 제한 — 카드 무차별 시도 방지.
    rate_limit_payment_per_minute: int = 20
    # 어드민 로그인 분당 허용 횟수(외부 API rate limit과 별개) — 무차별 로그인 방지.
    admin_login_rate_limit_per_minute: int = 10
    # 어드민 계정 보안 정책: 연속 로그인 실패 잠금 임계치 / 잠금 지속(분) / 비밀번호 최소 길이.
    max_failed_logins: int = 5
    account_lock_minutes: int = 15
    min_password_length: int = 10
    # 비밀번호 설정·재설정 링크 유효시간(시간). setup/reset 공통.
    password_link_ttl_hours: int = 48
    # 단건(일반) 결제 1회 최대 금액(원) — 리스크 한도.
    one_off_max_amount: int = 100_000_000
    # True이면 X-Forwarded-For 헤더로 실제 클라이언트 IP를 판별 (리버스 프록시 환경).
    trust_proxy: bool = False
    # 신뢰하는 리버스 프록시 단(hop) 수(감사 Phase 1 — 보안 M-5).
    # XFF에서 "오른쪽에서 n번째" 값을 클라이언트 IP로 취한다 — 오른쪽 끝 n-1개는
    # 신뢰 프록시들이 추가한 값이고, n번째가 첫 신뢰 프록시에 도달한 실제 피어 IP다.
    # 왼쪽 항목들은 클라이언트가 임의 헤더로 위조할 수 있으므로 절대 신뢰하지 않는다.
    # 예) 클라이언트 → nginx → 앱 구조면 1 (가장 흔한 구성).
    trust_proxy_hops: int = 1
    # False로 끄면 APScheduler가 시작되지 않아 갱신 배치가 실행되지 않는다.
    scheduler_enabled: bool = True
    # 갱신 배치 실행 주기(분). 기본 5분 — 만료 직후 최대 지연을 5분으로 제한.
    scheduler_interval_minutes: int = 5
    # 갱신 배치 Redis 전역 락 TTL(초) — heartbeat가 멈춘(프로세스 사망) 뒤 락이
    # 자연 해소되기까지의 데드맨 스위치. heartbeat 주기는 이 값의 1/3로 파생된다.
    scheduler_lock_ttl_seconds: int = 240
    # 갱신 배치 1회당 카테고리별 처리 상한 — due 폭주 시 한 배치가 길어지지 않게 끊는다.
    renewal_batch_limit: int = 1000
    # 엑셀 내려받기 1건 최대 행 수 — 초과 시 상위 N행까지만(워커 OOM 방지). 기간 필터로 좁혀 재요청.
    export_max_rows: int = 100_000
    # 구독 결제 실패 재시도 정책(요청 002): 12시간 간격 최대 4회.
    retry_interval_hours: int = 12
    retry_limit: int = 4
    # Suspended(강제 정지) 후 EXPIRED까지 수동 결제 대기 일수.
    suspended_grace_days: int = 30
    # False로 끄면 웹훅 발신 IP 검증을 건너뜀 (개발/테스트 환경 편의).
    webhook_ip_check_enabled: bool = True
    # 무인증 서비스 목록 API(GET /api/v1/services) 노출 여부 — 감사 Phase 2(보안 L-1).
    # 개발·연동 도구 편의용이라 기본 True. 사내 서비스 구성(이름·상태)이 노출되는
    # 정보이므로 인터넷에 직접 노출되는 운영 환경에서는 false 권장(404 반환).
    public_service_list_enabled: bool = True
    toss_webhook_allowed_ips: list[str] = TOSS_WEBHOOK_IPS
    # Swagger UI(/docs)·OpenAPI 스키마(/openapi.json) 접근용 HTTP Basic 계정.
    # 둘 다 설정된 경우에만 docs가 노출되며, 이 id/pw로 인증해야 접근할 수 있다.
    # 비워두면(기본) docs는 비활성화(404)된다.
    swagger_id: str = ""
    swagger_pw: str = ""
    # 개발 편의: 로그인 폼 기본값 자동 채움. 로컬 개발(environment == "dev")에서만 노출되며
    # stg·prod 등 외부 노출 환경에서는 채우지 않는다(자격증명 화면 노출 방지).
    dev_login_email: str = ""
    dev_login_password: str = ""
    # Gmail SMTP 발송(요청 003). 앱 비밀번호 사용. 둘 다 설정 시 실제 발송.
    gmail_id: str = ""
    gmail_pw: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    mail_from_name: str = "결제시스템"


@lru_cache(maxsize=1)
def default_settings() -> Settings:
    """DI(Depends(get_settings))로 settings를 받지 못하는 서비스/모듈 레벨 코드용 폴백.

    .env(+.env.<env>)를 읽는 단일 캐시 인스턴스. 모듈 상수(잠금 임계치·토큰 TTL·
    결제 상한 등)를 .env로 조정 가능하게 하되, 요청 경로는 기존대로 주입된 settings를 쓴다.
    값은 프로세스 시작 시 한 번 읽으므로 변경 후에는 재시작이 필요하다(.env 설정의 일반 동작).
    """
    return Settings()
