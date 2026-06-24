# 2026-06-24 — 토스 시크릿 키: '***' 마스킹 표시 + 삭제 버튼 + 미설정 안내

## 요청
- 이미 설정된 경우: 입력박스에 `***` 표시 + 뒤에 **삭제 버튼** 추가
- 아직 미설정인 경우: 입력박스에 **"시크릿키 입력해주세요"** 안내

## 변경
- **template** `app/admin/templates/services/detail.html` — 토스 시크릿 키 한 줄 UI 재구성.
  - 설정됨: placeholder `***`, 빨간 '이미 설정됨' 배지, **[삭제]** 버튼(확인 모달 `data-confirm`) 노출.
  - 미설정: placeholder "시크릿키 입력해주세요", 삭제 버튼 숨김.
  - 저장 폼과 삭제 폼은 별개 form이라 flex 컨테이너에 나란히 배치.
- **route** `app/admin/routes/services.py` — `POST /services/{id}/toss-secret-key/delete` 추가(SYSTEM_ADMIN, CSRF). 멱등.
- **service** `app/services/registry.py` — `clear_toss_secret_key()` 추가: `toss_secret_key_encrypted=None`, 감사 `service.toss_secret_key.deleted`, commit. 키 없으면 no-op.
- **audit** `app/admin/audit_labels.py` — `service.toss_secret_key.deleted` → "토스 시크릿 키 삭제" 라벨 추가.

## 동작/보안
- 삭제하면 그 서비스의 결제·구독 첫 결제·자동연장이 거부됨(키 미설정 상태). 확인 모달로 오삭제 방지.
- 쓰기 전용·평문 재표시 금지 유지. `***`는 placeholder일 뿐 실제 값/길이를 노출하지 않음.

## 문서·검증
- 매뉴얼 `docs/user_manual/02-admin-services.md` §2.8에 `***`/삭제/미설정 안내 반영, 재빌드.
- 스모크 검증: Jinja 파싱 OK, set/delete 라우트 등록 확인, `clear_toss_secret_key` 존재, 삭제 감사 라벨 노출 확인.
