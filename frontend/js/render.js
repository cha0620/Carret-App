/* 렌더링 계층 — 화면만 그림. 서버 모름. */

const qBox      = document.getElementById('quality-box');
const qScores   = document.getElementById('quality-scores');
const qAnalysis = document.getElementById('quality-analysis');
const overlay   = document.getElementById('overlay');
const gateBadge = document.getElementById('gate-badge');

const feedbackBox     = document.getElementById('feedback-box');
const feedbackStars   = document.getElementById('feedback-stars');
const feedbackComment = document.getElementById('feedback-comment');
const feedbackStatus  = document.getElementById('feedback-status');

function renderQuality(quality) {
  if (!quality) { qBox.classList.add('hidden'); return; }
  qScores.textContent =
    `🛡️ 충실성 ${quality.fidelity}/5 · 현실감 ${quality.realism}/5 · 신뢰 ${quality.trust}/5`;
  qAnalysis.textContent = quality.analysis || '';
  qBox.classList.remove('hidden');
}

// 캔버스 안에서 이미지가 실제로 그려지는 영역(%) — object-fit:contain 이 만드는
// 레터박스(여백)를 그대로 계산해서, 좌표(0~1000, 이미지 기준)를 오버레이(캔버스 기준)
// %로 정확히 옮긴다. 캔버스 자체가 줌으로 transform: scale() 되어도 두 rect의
// 비율은 그대로 유지되므로 줌 배율과 무관하게 항상 맞는다.
function imageBoxInCanvas() {
  const img = document.getElementById('after');
  const canvas = document.getElementById('result-canvas');
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  if (!img.naturalWidth || !cw || !ch) return null;

  const scale = Math.min(cw / img.naturalWidth, ch / img.naturalHeight);
  const dispW = img.naturalWidth * scale;
  const dispH = img.naturalHeight * scale;
  return {
    offXPct: (cw - dispW) / 2 / cw * 100,
    offYPct: (ch - dispH) / 2 / ch * 100,
    wPct: dispW / cw * 100,
    hPct: dispH / ch * 100,
  };
}

function renderBubbles(bubbles) {
  overlay.innerHTML = '';
  const box = imageBoxInCanvas();
  if (!box) return;

  (bubbles || []).forEach(b => {
    const L = box.offXPct + (b.x1 / 1000) * box.wPct;
    const T = box.offYPct + (b.y1 / 1000) * box.hPct;
    const W = ((b.x2 - b.x1) / 1000) * box.wPct;
    const H = ((b.y2 - b.y1) / 1000) * box.hPct;

    const el = document.createElement('div');
    el.className = 'defect-box';
    el.style.cssText = `left:${L}%;top:${T}%;width:${W}%;height:${H}%`;
    overlay.appendChild(el);

    const below = b.y1 < 150;
    const bub = document.createElement('div');
    bub.className = 'bubble' + (below ? ' below' : '');
    bub.style.left = (L + W / 2) + '%';
    bub.style.top  = (below ? T + H : T) + '%';
    bub.textContent = `💬 ${b.what} (${b.where})`;
    overlay.appendChild(bub);
  });
}

// res = transform 응답. 무엇을 보여주는지(원본/합성/생성)에 따라 문구가 달라야 정직하다.
function renderGate(res) {
  const show = (text, color) => {
    gateBadge.hidden = false;
    gateBadge.textContent = text;
    gateBadge.style.color = color;
  };
  if (res.status === 'blocked') {
    return show('⛔ 변환 결과가 검사를 통과하지 못해 원본 사진을 그대로 보여드려요', '#c0392b');
  }
  if (res.mode === 'composite') {
    return res.detect_failed
      ? show('⚠️ 하자 검사를 하지 못해, 원본 물건 사진에 배경만 바꿨어요', '#b9770e')
      : show('🛡️ 하자·글자를 지키려고 원본 물건 사진에 배경만 바꿨어요', '#2a7f2a');
  }
  if (res.detect_failed) {
    return show('⚠️ 하자 검사를 하지 못했습니다 — 생성 이미지에서 하자가 지워졌을 수 있어요', '#c0392b');
  }
  const passed = res.gate_passed;
  if (passed === null || passed === undefined) { gateBadge.hidden = true; return; }
  gateBadge.hidden = false;
  gateBadge.textContent = passed
    ? '🛡️ 검출된 하자 모두 보존됨'
    : '⚠️ 일부 하자가 보존되지 않았을 수 있음 (과잉보정 주의)';
  gateBadge.style.color = passed ? '#2a7f2a' : '#c0392b';
}

function resetZoom() {
  const canvas = document.getElementById('result-canvas');
  const viewport = document.getElementById('after-wrap');
  if (canvas) canvas.style.transform = '';
  if (viewport) viewport.classList.remove('zoomed');
  if (typeof resetZoomScale === 'function') resetZoomScale();  // main.js 의 내부 배율도 동기화
}

function clearResults() {
  stopQualityPoll();
  overlay.innerHTML = '';
  gateBadge.hidden = true;
  qBox.classList.add('hidden');
  hideFeedbackBox();
  resetZoom();
}

// ===== 피드백 =====
function setStars(rating) {
  feedbackStars.querySelectorAll('.star').forEach(s => {
    s.classList.toggle('active', Number(s.dataset.value) <= rating);
  });
}

function resetFeedbackBox() {
  feedbackBox.classList.remove('hidden');
  setStars(0);
  feedbackComment.value = '';
  feedbackStatus.textContent = '';
}

function renderFeedback(fb) {
  feedbackBox.classList.remove('hidden');
  setStars(fb.rating);
  feedbackComment.value = fb.comment || '';
  feedbackStatus.textContent = '이전에 남긴 피드백이에요. 수정 후 다시 보낼 수 있어요.';
}

function hideFeedbackBox() {
  feedbackBox.classList.add('hidden');
  setStars(0);
  feedbackComment.value = '';
  feedbackStatus.textContent = '';
}

function renderMetaChips(res) {
  document.getElementById("meta-chips").style.display = "flex";
  document.getElementById("meta-item").textContent = res.item || "object";

  const considered = res.considered || [];
  if (considered.length) {
    document.getElementById("meta-considered-wrap").style.display = "inline";
    // VLM 출력이라 사진 속 글자로 조작될 수 있다 — innerHTML 금지 (XSS)
    const wrap = document.getElementById("meta-considered");
    wrap.replaceChildren(...considered.map(c => {
      const chip = document.createElement("span");
      chip.className = "chip chip-considered";
      chip.textContent = c;
      return chip;
    }));
  } else {
    document.getElementById("meta-considered-wrap").style.display = "none";
  }
}
