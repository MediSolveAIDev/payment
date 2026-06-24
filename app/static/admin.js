// Payment Admin 상호작용 (lucide 아이콘 / 모달 / 토스트 / Eye 토글)
(function () {
  "use strict";

  // --- lucide 아이콘 렌더 (로드 시 + htmx 스왑 후) ---
  function renderIcons() { if (window.lucide) window.lucide.createIcons({ nameAttr: "data-lucide" }); }
  document.addEventListener("DOMContentLoaded", renderIcons);
  document.body && document.body.addEventListener("htmx:afterSwap", renderIcons);
  document.body && document.body.addEventListener("htmx:historyRestore", renderIcons);

  // --- 사이드바 시계: 왼쪽 메뉴 위 현재 시각(매초 갱신) ---
  // 저장은 UTC, 표시는 KST 규약(app/core/clock.py)에 맞춰 PC 시간대와 무관하게 Asia/Seoul 고정.
  var clockFmt = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", hour12: false,
    month: "numeric", day: "numeric", weekday: "short",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  function tickClock() {
    var t = document.getElementById("clock-time");
    if (!t) return;
    var v = {};
    clockFmt.formatToParts(new Date()).forEach(function (p) { v[p.type] = p.value; });
    t.textContent = v.hour + ":" + v.minute + ":" + v.second;
    var d = document.getElementById("clock-date");
    if (d) d.textContent = v.month + "/" + v.day + " (" + v.weekday + ")";
  }
  document.addEventListener("DOMContentLoaded", function () {
    tickClock();
    setInterval(tickClock, 1000);
  });

  // --- Confirmation Modal (modal.md): 폼에 data-confirm 이 있으면 네이티브 confirm 대체 ---
  function buildModal() {
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML =
      '<div class="modal modal--warning" role="dialog" aria-modal="true">' +
      '  <div class="modal-body">' +
      '    <div class="modal-icon">!</div>' +
      '    <div class="modal-title"></div>' +
      '    <div class="modal-desc"></div>' +
      '    <input class="modal-input" style="display:none;width:100%;margin-top:12px;padding:8px 10px;box-sizing:border-box">' +
      '  </div>' +
      '  <div class="modal-actions">' +
      '    <button type="button" class="btn btn-ghost" data-modal-cancel>취소</button>' +
      '    <button type="button" class="btn btn-danger" data-modal-ok>확인</button>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(overlay);
    return overlay;
  }

  var overlay, pendingForm, pendingOk, pendingInputField;

  // --- 완료 모달(✓): DB 쓰기 성공 후 body[data-saved] 값으로 띄운다 ---
  function showSavedModal(msg) {
    if (!overlay) overlay = buildModal();
    pendingForm = null;   // 확인 클릭 시 폼 제출 없이 그냥 닫힘
    pendingOk = null;
    pendingInputField = null;
    overlay.querySelector(".modal-input").style.display = "none";
    var modal = overlay.querySelector(".modal");
    modal.className = "modal modal--complete";
    modal.querySelector(".modal-icon").textContent = "✓";
    modal.querySelector(".modal-title").textContent = "완료";
    modal.querySelector(".modal-desc").textContent = msg || "저장되었습니다";
    var ok = modal.querySelector("[data-modal-ok]");
    ok.textContent = "확인";
    ok.className = "btn btn-primary";
    // 완료 모달에서는 취소 버튼 불필요 — 숨김
    var cancel = modal.querySelector("[data-modal-cancel]");
    if (cancel) cancel.style.display = "none";
    overlay.classList.add("open");
  }

  function openConfirm(form, onOk) {
    pendingOk = onOk || null;
    if (!overlay) overlay = buildModal();
    pendingForm = form;
    // confirm 모달 재사용 시 취소 버튼이 숨겨져 있을 수 있으므로 다시 보임
    var cancel = overlay.querySelector("[data-modal-cancel]");
    if (cancel) cancel.style.display = "";
    var warning = form.getAttribute("data-confirm-type") !== "complete";
    var modal = overlay.querySelector(".modal");
    modal.className = "modal " + (warning ? "modal--warning" : "modal--complete");
    modal.querySelector(".modal-icon").textContent = warning ? "!" : "✓";
    modal.querySelector(".modal-title").textContent =
      form.getAttribute("data-confirm-title") || "확인이 필요합니다";
    modal.querySelector(".modal-desc").textContent = form.getAttribute("data-confirm") || "";
    var ok = modal.querySelector("[data-modal-ok]");
    ok.textContent = form.getAttribute("data-confirm-ok") || "확인";
    ok.className = "btn " + (warning ? "btn-danger" : "btn-primary");
    // 입력형 모달(data-confirm-input="<폼 필드명>"): 모달에 입력칸을 띄우고 확인 시 그 값을 폼 필드에 채운다
    pendingInputField = form.getAttribute("data-confirm-input") || null;
    var input = modal.querySelector(".modal-input");
    if (pendingInputField) {
      input.style.display = "";
      input.type = form.getAttribute("data-confirm-input-type") || "number";
      input.placeholder = form.getAttribute("data-confirm-input-placeholder") || "";
      input.min = form.getAttribute("data-confirm-input-min") || "";
      input.max = form.getAttribute("data-confirm-input-max") || "";
      input.value = form.getAttribute("data-confirm-input-default") || "";
      setTimeout(function () { input.focus(); }, 30);
    } else {
      input.style.display = "none";
    }
    overlay.classList.add("open");
  }

  function closeConfirm() {
    if (overlay) overlay.classList.remove("open");
    pendingForm = null;
    pendingOk = null;
    pendingInputField = null;
    if (overlay) overlay.querySelector(".modal-input").style.display = "none";
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.hasAttribute("hx-post")) return;  // htmx 폼은 htmx:confirm에서 처리
    if (form.hasAttribute("data-confirm") && !form.__confirmed) {
      e.preventDefault();
      openConfirm(form);
    }
  });

  // --- 제출 중 로딩 표시 (data-loading 폼) ---
  // 일반 POST 폼(예: 계정 생성 + 설정 메일 발송)은 응답이 올 때까지 시간이 걸린다.
  // 버튼을 스피너+비활성으로 바꾸고 상단 진행바를 띄워 "처리 중"임을 알리고 중복 제출을 막는다.
  // 응답은 메일 발송까지 끝낸 뒤 도착하므로(303 리다이렉트/오류 재렌더) 페이지 전환 시 자동 해제된다.
  function startSubmitLoading(form) {
    var btn = form.querySelector('button[type="submit"], .actions button');
    if (btn && !btn.dataset.loadingActive) {
      btn.dataset.loadingActive = "1";
      btn.dataset.loadingOrig = btn.innerHTML;
      var text = form.getAttribute("data-loading-text") || btn.textContent.trim();
      btn.disabled = true;
      btn.classList.add("is-loading");
      btn.innerHTML = '<span class="btn-spinner" aria-hidden="true"></span>' + text;
    }
    var bar = document.getElementById("global-progress");
    if (bar) bar.classList.add("loading");
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented) return;                       // 확인/검증에서 막힌 제출은 제외
    if (!form.hasAttribute || !form.hasAttribute("data-loading")) return;
    if (form.hasAttribute("hx-post")) return;             // htmx 폼은 #global-progress가 자동 처리
    startSubmitLoading(form);
  });

  // 뒤로가기(bfcache) 복원 시 로딩 상태가 남지 않도록 초기화
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var bar = document.getElementById("global-progress");
    if (bar) bar.classList.remove("loading");
    document.querySelectorAll('button[data-loading-active="1"]').forEach(function (btn) {
      btn.disabled = false;
      btn.classList.remove("is-loading");
      if (btn.dataset.loadingOrig != null) btn.innerHTML = btn.dataset.loadingOrig;
      delete btn.dataset.loadingActive;
      delete btn.dataset.loadingOrig;
    });
  });

  // --- htmx 요청의 data-confirm 모달 (요청 005) ---
  document.body.addEventListener("htmx:confirm", function (e) {
    var form = e.detail.elt.closest && e.detail.elt.closest("form[data-confirm]");
    if (!form) return;                 // data-confirm 없으면 그대로 진행
    e.preventDefault();                // 요청 보류
    openConfirm(form, function () { e.detail.issueRequest(true); });
  });

  // 입력형 모달의 '확인' 처리 — 값 검증 후 폼 필드에 채우고 진행. 유효하지 않으면 모달 유지.
  function confirmOk() {
    var form = pendingForm;
    var ok = pendingOk;
    if (pendingInputField) {
      var input = overlay.querySelector(".modal-input");
      var val = (input.value || "").trim();
      var num = parseInt(val, 10);
      var min = input.min ? parseInt(input.min, 10) : null;
      var max = input.max ? parseInt(input.max, 10) : null;
      // 빈값·숫자 아님·범위 밖이면 입력 유지(모달 닫지 않음)
      if (val === "" || isNaN(num) || (min !== null && num < min) || (max !== null && num > max)) {
        input.focus();
        input.style.borderColor = "var(--red, #E5396E)";
        return;
      }
      // 폼의 동명 필드(hidden)에 입력값을 채운다
      if (form) {
        var field = form.querySelector('[name="' + pendingInputField + '"]');
        if (field) field.value = val;
      }
    }
    closeConfirm();
    if (ok) { ok(); }                              // htmx 요청 재개
    else if (form) { form.__confirmed = true; form.submit(); }
  }

  // 입력형 모달에서 Enter → 확인, Esc → 취소
  document.addEventListener("keydown", function (e) {
    if (!overlay || !overlay.classList.contains("open")) return;
    if (e.key === "Enter" && e.target.classList && e.target.classList.contains("modal-input")) {
      e.preventDefault();
      confirmOk();
    } else if (e.key === "Escape") {
      closeConfirm();
    }
  });

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-modal-cancel]")) {
      closeConfirm();
    } else if (e.target.closest("[data-modal-ok]")) {
      confirmOk();
    } else if (e.target.closest("[data-copy]")) {
      // --- 키 복사 (1.3) ---
      var cbtn = e.target.closest("[data-copy]");
      var text = cbtn.getAttribute("data-copy");
      var done = function () { showToast("복사되었습니다", "complete"); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
      } else { fallbackCopy(text); done(); }
    } else if (e.target.closest("[data-toggle]")) {
      // --- 인라인 폼 토글 (요청 005: 담당자 추가) ---
      var tbtn = e.target.closest("[data-toggle]");
      var target = document.querySelector(tbtn.getAttribute("data-toggle"));
      if (target) {
        var hidden = target.style.display === "none";
        target.style.display = hidden ? "" : "none";
      }
    } else if (e.target.closest(".eye-btn")) {
      // --- 비밀번호 Eye 토글 (input.md) ---
      var btn = e.target.closest(".eye-btn");
      var input = btn.parentElement.querySelector("input");
      if (input) {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.textContent = show ? "\u{1F441}" : "\u{1F441}‍\u{1F5E8}"; // 표시/숨김
      }
    }
  });

  // --- Toast (상단 중앙, 2초) ---
  function showToast(msg, kind) {
    kind = kind === "error" ? "error" : "complete";
    var wrap = document.createElement("div");
    wrap.className = "toast-wrap";
    wrap.innerHTML =
      '<div class="toast toast--' + kind + '">' +
      '  <span class="toast-icon">' + (kind === "error" ? "!" : "✓") + "</span>" +
      '  <span></span></div>';
    wrap.querySelector(".toast span:last-child").textContent = msg;
    document.body.appendChild(wrap);
    setTimeout(function () { wrap.remove(); }, 2000);
  }

  // --- 허용 IP 옥텟 입력 (요청 005) — data-ip-form 폼에서 동작 ---
  function ipRowHtml() {
    var oct = '<input type="text" inputmode="numeric" maxlength="3" class="ip-oct">';
    return '<div class="ip-row" data-ip-row>' + oct + oct + oct + oct +
      '<button type="button" class="btn-text" data-ip-del style="color:#E5396E">삭제</button></div>';
  }

  document.addEventListener("click", function (e) {
    var add = e.target.closest("[data-ip-add]");
    if (add) {
      var rows = add.closest("form").querySelector("[data-ip-rows]");
      rows.insertAdjacentHTML("beforeend", ipRowHtml());
      return;
    }
    var del = e.target.closest("[data-ip-del]");
    if (del) del.closest("[data-ip-row]").remove();
  });

  document.addEventListener("input", function (e) {
    if (!e.target.classList || !e.target.classList.contains("ip-oct")) return;
    var el = e.target;
    el.value = el.value.replace(/\D/g, "").slice(0, 3);
    if (el.value.length === 3) {
      var next = el.nextElementSibling;
      if (next && next.classList.contains("ip-oct")) next.focus();
    }
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute("data-ip-form")) return;
    var lines = [];
    var bad = false;
    form.querySelectorAll("[data-ip-row]").forEach(function (row) {
      var octs = Array.prototype.map.call(
        row.querySelectorAll(".ip-oct"), function (i) { return i.value.trim(); });
      var filled = octs.filter(Boolean).length;
      row.style.outline = "";
      if (filled === 0) return;  // 빈 행 무시
      var valid = filled === 4 && octs.every(function (v) {
        return /^(0|[1-9]\d{0,2})$/.test(v) && +v <= 255;  // 앞자리 0 금지(서버 IPv4 규칙과 일치)
      });
      if (!valid) { bad = true; row.style.outline = "1px solid #E5396E"; return; }
      lines.push(octs.join("."));
    });
    if (bad) { e.preventDefault(); showToast("IP 형식을 확인하세요 (각 칸 0~255)", "error"); return; }
    // data-ip-allow-empty: 빈 목록 허용(예: 어드민 IP=제한 없음). 없으면 1개 이상 필수.
    if (lines.length === 0 && !form.hasAttribute("data-ip-allow-empty")) {
      e.preventDefault();
      showToast("IP를 1개 이상 등록해야 합니다", "error");
      return;
    }
    // data-ip-target: 값을 채울 hidden 필드명(기본 allowed_ips — 서비스 등록 폼).
    var ipTarget = form.getAttribute("data-ip-target") || "allowed_ips";
    var ipHidden = form.querySelector('input[name="' + ipTarget + '"]');
    if (ipHidden) ipHidden.value = lines.join("\n");
  }, true);  // capture — data-confirm/htmx 핸들러보다 먼저 합성

  // --- 천단위 콤마 입력 ([data-comma]) ---
  // 입력 중에는 콤마로 포맷해 보여 주고, 제출 직전 콤마를 제거해 서버(int 파싱)에는
  // 순수 숫자만 전달한다. 캐럿 위치는 "앞쪽 숫자 개수" 기준으로 복원해 편집감을 유지.
  function formatComma(el) {
    var digitsBefore = el.value.slice(0, el.selectionStart).replace(/\D/g, "").length;
    var digits = el.value.replace(/\D/g, "");
    el.value = digits ? Number(digits).toLocaleString("en-US") : "";
    var pos = 0, seen = 0;
    while (pos < el.value.length && seen < digitsBefore) {
      var c = el.value.charCodeAt(pos);
      if (c >= 48 && c <= 57) seen++;
      pos++;
    }
    if (el.setSelectionRange) { try { el.setSelectionRange(pos, pos); } catch (e) { /* ignore */ } }
  }
  document.addEventListener("input", function (e) {
    var el = e.target.closest && e.target.closest("[data-comma]");
    if (el) formatComma(el);
  });
  // 제출 직전 콤마 제거 — [data-comma]가 없는 폼이면 아무 일도 하지 않음
  document.addEventListener("submit", function (e) {
    e.target.querySelectorAll("[data-comma]").forEach(function (el) {
      el.value = el.value.replace(/,/g, "");
    });
  }, true);  // capture — htmx 직렬화/전송보다 먼저 콤마 제거

  // 클립보드 폴백(execCommand)
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) { /* ignore */ }
    ta.remove();
  }

  // 일회성 알림 파라미터(saved/flash/flash_type)를 URL에서 제거한다.
  // 저장/수정 성공은 ?saved=... 리다이렉트로 모달을 띄우는데, URL에 남아 있으면
  // 새로고침(F5) 시 같은 URL이 다시 로드돼 모달이 또 뜬다. 한 번 표시한 뒤 제거해 재출력을 막는다.
  function stripNotifyParams() {
    if (!window.history || !history.replaceState || typeof URL !== "function") return;
    try {
      var url = new URL(window.location.href);
      var changed = false;
      ["saved", "flash", "flash_type"].forEach(function (k) {
        if (url.searchParams.has(k)) { url.searchParams.delete(k); changed = true; }
      });
      if (!changed) return;
      var qs = url.searchParams.toString();
      history.replaceState(history.state, "", url.pathname + (qs ? "?" + qs : "") + url.hash);
    } catch (e) { /* URL API 미지원 등 — 무시 */ }
  }

  // body[data-flash] → 플래시 토스트 / body[data-saved] → 완료 모달(✓)
  document.addEventListener("DOMContentLoaded", function () {
    var flash = document.body.getAttribute("data-flash");
    if (flash) showToast(flash, document.body.getAttribute("data-flash-type"));
    var saved = document.body.getAttribute("data-saved");
    if (saved) showSavedModal(saved);
    // 표시 후 URL에서 알림 파라미터 제거 → 새로고침 시 모달/토스트 재출력 방지
    stripNotifyParams();
  });

  // htmx로 쓰는 액션(폼 hx-post)은 리다이렉트가 XHR로 흡수돼 body[data-saved]가
  // 스왑 밖에 있어 위 DOMContentLoaded가 안 탄다. 서버가 HX-Trigger:{"showSaved":메시지}를
  // 보내면 htmx가 이 이벤트를 발생시키고, 여기서 완료 모달을 띄운다(요청).
  document.body && document.body.addEventListener("showSaved", function (e) {
    showSavedModal((e.detail && (e.detail.value || e.detail)) || "저장되었습니다");
  });

  // --- 커스텀 툴팁([data-tip]) — 결제 실패코드 의미 등. 네이티브 title의 지연/누락 보완 ---
  // body에 팝업 div를 붙여 표시(테이블 overflow에 잘리지 않음).
  var tipEl = null;
  function showTip(target) {
    var text = target.getAttribute("data-tip");
    if (!text) return;
    tipEl = document.createElement("div");
    tipEl.className = "tip-pop";
    tipEl.textContent = text;
    document.body.appendChild(tipEl);
    var r = target.getBoundingClientRect();
    // 요소 아래에 표시하되, 화면 오른쪽을 넘지 않게 left를 보정
    var left = r.left + window.scrollX;
    var maxLeft = window.scrollX + document.documentElement.clientWidth - tipEl.offsetWidth - 12;
    tipEl.style.left = Math.round(Math.min(left, Math.max(window.scrollX + 8, maxLeft))) + "px";
    tipEl.style.top = Math.round(r.bottom + window.scrollY + 6) + "px";
  }
  function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
  document.addEventListener("mouseover", function (e) {
    var t = e.target.closest && e.target.closest("[data-tip]");
    if (t) { hideTip(); showTip(t); }
  });
  document.addEventListener("mouseout", function (e) {
    if (e.target.closest && e.target.closest("[data-tip]")) hideTip();
  });
  document.addEventListener("htmx:beforeSwap", hideTip);  // 부분 갱신 시 잔존 팝업 제거

  // --- 컴팩트 뷰 토글 (UX 개선) ---
  var compactChk = document.getElementById("compact-view-checkbox");
  if (compactChk) {
    var isCompact = localStorage.getItem("admin_compact_view") === "true";
    compactChk.checked = isCompact;
    if (isCompact) document.body.classList.add("is-compact");

    compactChk.addEventListener("change", function() {
      if (this.checked) {
        document.body.classList.add("is-compact");
        localStorage.setItem("admin_compact_view", "true");
      } else {
        document.body.classList.remove("is-compact");
        localStorage.setItem("admin_compact_view", "false");
      }
    });
  }

  // --- 다크/라이트 테마 토글 ---
  // 초기 테마는 <head>의 인라인 스크립트가 이미 적용(깜빡임 방지). 여기선 클릭 전환만 담당.
  // 아이콘(해/달) 표시는 CSS가 data-theme에 따라 전환하므로 JS 아이콘 재렌더가 필요 없다.
  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("admin-theme", t); } catch (e) { /* 사파리 프라이빗 등 */ }
  }
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
      setTheme(cur === "dark" ? "light" : "dark");   // 선택할 때마다 즉시 전환
    });
  });
})();
