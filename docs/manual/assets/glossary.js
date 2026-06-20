/* 상태/에러 코드 위에 마우스를 올리면 설명을 보여주는 플로팅 툴팁.
   표(overflow) 안에서도 잘리지 않도록 body에 떠 있는 단일 툴팁을 위치시킨다. */
(function(){
  var G={
    // 구독 상태
    "TRIAL":"구독 상태 · 무료 체험 중(첫 결제 전). 접근 허용 — 체험 종료 시 첫 결제.",
    "ACTIVE":"구독 상태 · 정상 이용 중. 접근 허용 — 만료일에 자동 갱신.",
    "PAST_DUE":"구독 상태 · 결제 실패로 자동 재시도 중. 접근은 아직 허용.",
    "EXTENDED":"구독 상태 · 관리자가 만료일을 수동 연장. 접근 허용 — 새 만료일에 갱신.",
    "CANCELED":"해지 예약(만료일까지 유지) 또는 결제 취소(환불) 처리됨.",
    "SUSPENDED":"구독 상태 · 반복 결제 실패로 정지. 접근 차단 — 수동 결제로 복구.",
    "EXPIRED":"구독 상태 · 완전 종료(최종). 접근 차단.",
    // 결제 상태
    "PENDING":"결제 상태 · 처리 중. 정산 스윕에서 최종 확정.",
    "DONE":"결제 상태 · 정상 승인 완료.",
    "FAILED":"결제 상태 · 실패. failure_code/message 확인.",
    // 결제 회차
    "FIRST":"결제 회차 · 첫 결제.",
    "RENEWAL":"결제 회차 · 정기 갱신.",
    "RETRY":"결제 회차 · 재시도.",
    "ONE_OFF":"결제 회차 · 단건(1회성) 결제.",
    // API 에러 코드
    "UNAUTHORIZED":"API 에러 401 · 인증 헤더/서명 누락·불일치.",
    "FORBIDDEN":"API 에러 403 · 허용되지 않은 IP.",
    "PAYMENT_FAILED":"API 에러 402 · 토스 결제 승인/자동결제 실패.",
    "NOT_FOUND":"API 에러 404 · 리소스 없음.",
    "CONFLICT":"API 에러 409 · 구독 1개 규칙 위반 등 상태 충돌.",
    "VALIDATION_ERROR":"API 에러 422 · 형식 오류·비즈니스 규칙 위반.",
    "RATE_LIMITED":"API 에러 429 · 요청 한도 초과.",
    "SERVER_DISABLED":"API 에러 503 · 결제서버 비활성화(킬스위치)."
  };
  var tip=document.createElement('div');
  tip.className='gloss-tip';
  document.body.appendChild(tip);
  function place(e){
    var pad=12,w=tip.offsetWidth,h=tip.offsetHeight;
    var x=e.clientX+14,y=e.clientY+18;
    if(x+w>window.innerWidth-pad) x=window.innerWidth-pad-w;
    if(x<pad) x=pad;
    if(y+h>window.innerHeight-pad) y=e.clientY-h-14;
    tip.style.left=x+'px'; tip.style.top=y+'px';
  }
  function bind(el,txt){
    el.classList.add('gloss');
    el.addEventListener('mouseenter',function(e){tip.textContent=txt;tip.style.display='block';place(e);});
    el.addEventListener('mousemove',place);
    el.addEventListener('mouseleave',function(){tip.style.display='none';});
  }
  document.querySelectorAll('.badge, code').forEach(function(el){
    var t=(el.textContent||'').trim();
    if(Object.prototype.hasOwnProperty.call(G,t)) bind(el,G[t]);
  });
})();
