import uuid

from django.db import models


def _new_customer_key() -> str:
    return uuid.uuid4().hex


class ServiceCredential(models.Model):
    """서비스별 호출 자격증명 — 한번 입력하면 저장해 다시 묻지 않는다.

    service_id는 결제 서버의 서비스 UUID(문자열). api_key/hmac_secret은 어드민
    서비스 생성 시 1회 발급된 평문을 운영자가 붙여넣어 저장한다(서버는 일괄 노출하지 않음).
    """

    # 결제 서버의 서비스 UUID — 유일해야 한다(update_or_create 기준 키)
    service_id = models.CharField(max_length=64, unique=True)
    # 서비스 표시 이름 — 화면에서 어떤 서비스인지 식별용
    name = models.CharField(max_length=100)
    # 결제 서버 인증 키 — x-service-key 헤더에 사용
    api_key = models.CharField(max_length=128)
    # HMAC 서명 비밀 — x-signature 헤더 계산에 사용
    hmac_secret = models.CharField(max_length=128)
    # 저장 시각 — 감사 추적용
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class SampleUser(models.Model):
    """데모 사용자 — 이메일이 구독서버 external_user_id, customer_key는 토스 빌링용."""

    email = models.EmailField(unique=True)
    customer_key = models.CharField(max_length=64, default=_new_customer_key)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email


class OneOffRecord(models.Model):
    """단건(일반) 결제 기록 — billing_success_view에서 성공 시 저장.

    구독서버 /api/v1/payments/{external_user_id} 는 구독 결제만 반환하므로,
    단건 결제는 이 모델에 직접 저장해야 history_view에서 조회·취소 테스트가 가능하다.
    """

    # 결제한 데모 사용자
    user = models.ForeignKey(SampleUser, on_delete=models.CASCADE,
                             related_name="oneoff_records")
    # 구독서버 및 토스에 전달한 주문번호(중복 불가)
    order_id = models.CharField(max_length=128, unique=True)
    # 결제 상품명
    order_name = models.CharField(max_length=200)
    # 결제 금액(원)
    amount = models.IntegerField()
    # 취소 여부 — oneoff_cancel_view 성공 시 True 로 업데이트
    canceled = models.BooleanField(default=False)
    # 결제 기록 생성 시각
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order_id} ({self.order_name}, {self.amount}원)"


class NotificationRecord(models.Model):
    """결제 서버에서 받은 서비스 알림(아웃고잉 웹훅) 기록 — 요청 016 수신 데모.

    POST /notify로 수신해 저장하고 /notifications 화면에서 목록으로 보여준다.
    payload(JSON 원문)와 서명 검증 결과(verified)를 함께 보관한다.
    """

    event = models.CharField(max_length=64)            # payload EVENT 값
    status = models.CharField(max_length=32, blank=True)   # payload STATUS
    email = models.CharField(max_length=255, blank=True)   # 관련 사용자(external_user_id)
    order_id = models.CharField(max_length=128, blank=True)
    subscribe_id = models.CharField(max_length=64, blank=True)
    desc = models.TextField(blank=True)                # payload DESC
    payload = models.JSONField()                       # 수신 원문(JSON)
    verified = models.BooleanField(default=False)      # HMAC 서명 검증 통과 여부
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event} ({self.email})"
