"""앱 공통 인프라 의존성(Depends) — API·어드민 양쪽이 사용.

감사 Phase 4(S13): 과거에는 이 함수들이 app/api/deps.py에 있어 어드민 라우트가
api 레이어를 import하는 형태였다(레이어 방향성 흐림). DB·Redis·암호화·토스·메일
주입은 특정 레이어 소유가 아닌 앱 공통 인프라이므로 core로 내린다.
app/api/deps.py는 인증(authenticate_service 등) 전용으로 남고, 호환을 위해
이 모듈의 이름들을 재export한다.

T7 컷오버: get_toss(전역 TossClient) 제거. 모든 토스 호출은 get_toss_provider +
for_service(service)로 서비스별 키를 사용한다. 전역 폴백 없음.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.notifications.email import EmailSender
from app.toss.provider import TossClientProvider  # 서비스별 토스 클라이언트 해석기


def get_settings(request: Request) -> Settings:
    """앱 설정 객체를 반환한다. app.state.settings에서 가져온다."""
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """요청 범위 AsyncSession을 생성하고 요청 완료 후 닫는다."""
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    """앱 공유 Redis 클라이언트를 반환한다."""
    return request.app.state.redis


def get_cipher(request: Request) -> AesGcmCipher:
    """AES-GCM 암복호화 객체를 반환한다. HMAC 시크릿·빌링키 등의 복호화에 사용된다."""
    return request.app.state.cipher


def get_toss_provider(request: Request) -> TossClientProvider:
    """서비스별 토스 클라이언트 해석기를 반환한다."""
    return request.app.state.toss_provider


def get_email_sender(request: Request) -> EmailSender:
    """이메일 발송 구현체를 반환한다. 환경에 따라 Console/Gmail/Recording 중 하나가 주입된다."""
    return request.app.state.email_sender


def get_notifier(request: Request) -> "ServiceNotifier":
    """서비스 알림(아웃고잉 웹훅) 발송기를 반환한다(Http/Recording 주입)."""
    return request.app.state.notifier


def get_client_ip(request: Request, settings: Settings) -> str:
    """클라이언트 IP를 판별한다. (감사 Phase 1 — 보안 M-5 강화)

    trust_proxy=True일 때 X-Forwarded-For에서 **오른쪽에서 trust_proxy_hops번째**
    값을 취한다. 프록시는 자신이 본 피어 IP를 XFF 오른쪽에 append하므로,
    오른쪽 n번째까지가 신뢰 프록시 체인이 기록한 값이다. 과거처럼 맨 왼쪽을
    신뢰하면 공격자가 `X-Forwarded-For: <화이트리스트IP>` 헤더를 직접 보내
    IP 화이트리스트·웹훅 IP 검증을 우회할 수 있다.

    XFF 항목 수가 hop 수보다 적으면(프록시를 거치지 않은 직접 요청 등)
    위조 가능성이 있으므로 헤더를 무시하고 소켓 피어 IP로 폴백한다.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            hops = max(1, settings.trust_proxy_hops)
            # 오른쪽에서 hops번째 = 첫 신뢰 프록시에 도달한 실제 클라이언트 IP.
            # 항목이 부족하면 클라이언트가 직접 보낸(위조 가능한) 헤더 → 무시.
            if len(parts) >= hops:
                return parts[-hops]
    return request.client.host if request.client else ""


# 같은 서버(로컬) 루프백 — 허용목록과 무관하게 항상 허용한다.
# 127.0.0.1(IPv4)·::1(IPv6)은 동일 서버 환경이므로 IP 화이트리스트의 영향을 받지 않으며,
# 저장 시에도 목록에서 제거한다(무조건 허용이라 보관할 필요가 없다).
LOOPBACK_IPS = ("127.0.0.1", "::1")


def is_loopback_ip(ip: str) -> bool:
    """같은 서버(로컬) 루프백 IP인지 — 화이트리스트와 무관하게 항상 허용 대상."""
    return ip in LOOPBACK_IPS


def strip_loopback_ips(ips: list[str]) -> list[str]:
    """루프백 IP를 제거한 목록을 반환 — 항상 허용이라 목록에 저장하지 않는다."""
    return [ip for ip in ips if ip not in LOOPBACK_IPS]
