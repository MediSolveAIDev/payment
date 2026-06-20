# sample_service(Django 데모 상점) — 테스트 전용 이미지.
# 결제서버(payment_system) API를 호출하는 "외부 서비스" 참고 구현을 컨테이너로 띄운다.
# 운영용이 아니라, 연동을 눌러보며 확인하는 용도다(Django 개발 서버 사용).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 의존성 먼저(레이어 캐시) — Django / requests / python-dotenv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스(.dockerignore로 .venv·.git·db.sqlite3·.env 제외)
COPY . .

EXPOSE 8000

# 테스트 전용: 시작 시 마이그레이션(SQLite 스키마 생성) 후 Django 개발 서버 실행.
# 환경값(PAYMENT_API_BASE·SERVICE_API_KEY 등)은 compose가 주입한다.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:8000"]
