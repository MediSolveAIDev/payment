"""커맨드라인 관리 도구 모듈.

``uv run payment-system <command>`` 형태로 실행하는 관리 명령을 제공한다.
현재 지원 명령:
  create-admin  — SYSTEM_ADMIN 권한의 관리자 계정을 최초 생성한다.

pyproject.toml의 ``[project.scripts]``에 ``payment-system = "app.cli:main"``으로
등록되어 있다.
"""

import argparse
import asyncio
import sys

from app.core.config import Settings
from app.core.db import create_engine, create_session_factory
from app.core.errors import DomainError
from app.services.auth import create_system_admin


async def _create_admin(email: str, password: str) -> None:
    """SYSTEM_ADMIN 계정을 DB에 생성하는 비동기 내부 함수.

    명령 실행마다 독립적인 엔진을 생성하고, 완료 후 ``engine.dispose()``로
    커넥션 풀을 반드시 정리해 프로세스가 깔끔하게 종료되도록 한다.
    """
    settings = Settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as db:
            await create_system_admin(db, email=email, password=password)
        print(f"SYSTEM_ADMIN 생성 완료: {email}")
    finally:
        await engine.dispose()


def main() -> None:
    """CLI 진입점. 서브커맨드를 파싱해 해당 비동기 작업을 실행한다.

    DomainError는 stderr에 메시지를 출력하고 exit code 1로 종료해
    쉘 스크립트에서 오류를 감지할 수 있게 한다.
    """
    parser = argparse.ArgumentParser(prog="payment-system")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="시스템 관리자 생성")
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        try:
            asyncio.run(_create_admin(args.email, args.password))
        except DomainError as exc:
            print(f"오류: {exc.message}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
