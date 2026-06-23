"""서비스별 토스 클라이언트 해석기.

서비스의 암호화된 toss_secret_key를 복호화해 HttpTossClient를 생성·캐시한다.
캐시 키는 복호화된 시크릿 값 → 키 교체 시 새 엔트리가 생기고 옛 엔트리는 유휴화된다.
테스트는 override_client를 주입해 모든 서비스에 동일 Fake를 반환받는다(키 불필요).
"""
from app.core.crypto import AesGcmCipher
from app.core.errors import TossKeyNotConfiguredError
from app.toss.client import HttpTossClient, TossClient


class TossClientProvider:
    """서비스(Service 모델)별로 토스 HTTP 클라이언트를 해석·캐시하는 Provider.

    - override_client가 있으면 항상 그 인스턴스를 반환(테스트 DI).
    - 서비스에 toss_secret_key_encrypted가 없으면 TossKeyNotConfiguredError 발생.
    - 복호화된 시크릿별로 클라이언트를 캐시해 연결을 재사용한다.
    - 평문 시크릿은 절대 로그·예외 메시지에 노출하지 않는다.
    """

    def __init__(
        self,
        cipher: AesGcmCipher,
        base_url: str,
        *,
        override_client: TossClient | None = None,
        factory=HttpTossClient,
    ) -> None:
        self._cipher = cipher
        self._base_url = base_url
        self._override = override_client          # 테스트 주입용(있으면 항상 이 클라이언트 반환)
        self._factory = factory                   # (secret, base_url) -> TossClient
        self._cache: dict[str, TossClient] = {}   # 시크릿별 클라이언트 캐시(연결 재사용)

    def for_service(self, service) -> TossClient:
        """서비스의 토스 클라이언트를 반환한다.

        override_client가 있으면 즉시 반환.
        toss_secret_key_encrypted가 비어 있으면 TossKeyNotConfiguredError 발생.
        시크릿별로 캐시된 클라이언트를 반환하고, 없으면 factory로 생성 후 캐시.
        평문 시크릿은 반환값 외 어디에도 노출하지 않는다.
        """
        # override가 있으면 키 여부와 무관하게 즉시 반환(테스트 모드)
        if self._override is not None:
            return self._override

        # 암호화된 시크릿 키 조회 — 없으면 명확히 거부
        enc = getattr(service, "toss_secret_key_encrypted", None)
        if not enc:
            raise TossKeyNotConfiguredError()

        # 복호화 후 캐시 조회(평문을 로그·예외에 절대 노출하지 않음)
        secret = self._cipher.decrypt(enc)
        client = self._cache.get(secret)
        if client is None:
            # 미캐시 시크릿이면 factory로 생성 후 캐시 등록
            client = self._factory(secret, self._base_url)
            self._cache[secret] = client
        return client

    async def aclose(self) -> None:
        """캐시된 모든 HttpTossClient 정리(앱 종료 시 호출).

        override_client는 소유자가 직접 정리하므로 여기서는 건드리지 않는다.
        """
        for client in self._cache.values():
            # HttpTossClient.aclose()가 있는 경우에만 호출(인터페이스 유연성)
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                await aclose()
        self._cache.clear()
