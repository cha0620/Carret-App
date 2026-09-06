/* 렌더링 계층 — 화면만 그림. 서버 모름. */

const qBox      = document.getElementById('quality-box');
const qScores   = document.getElementById('quality-scores');
const qAnalysis = document.getElementById('quality-analysis');
const overlay   = document.getElementById('overlay');
const gateBadge = document.getElementById('gate-badge');

function renderQuality(quality) {
  if (!quality) { qBox.classList.add('hidden'); return; }
  qScores.textContent =
    `🛡️ 충실성 ${quality.fidelity}/5 · 현실감 ${quality.realism}/5 · 신뢰 ${quality.trust}/5`;
  qAnalysis.textContent = quality.analysis || '';
  qBox.classList.remove('hidden');
}

function renderBubbles(bubbles) {
  overlay.innerHTML = '';
  (bubbles || []).forEach(b => {
    const L = b.x1 / 10, T = b.y1 / 10;
    const W = (b.x2 - b.x1) / 10, H = (b.y2 - b.y1) / 10;

    const box = document.createElement('div');
    box.className = 'defect-box';
    box.style.cssText = `left:${L}%;top:${T}%;width:${W}%;height:${H}%`;
    overlay.appendChild(box);

    const below = b.y1 < 150;              // 꼭대기면 말풍선을 아래로
    const bub = document.createElement('div');
    bub.className = 'bubble' + (below ? ' below' : '');
    bub.style.left = (L + W / 2) + '%';
    bub.style.top  = (below ? T + H : T) + '%';
    bub.textContent = `💬 ${b.what} (${b.where})`;
    overlay.appendChild(bub);
  });
}

function renderGate(passed) {
  if (passed === null || passed === undefined) { gateBadge.hidden = true; return; }
  gateBadge.hidden = false;
  gateBadge.textContent = passed
    ? '🛡️ 검출된 하자 모두 보존됨'
    : '⚠️ 일부 하자가 보존되지 않았을 수 있음 (과잉보정 주의)';
  gateBadge.style.color = passed ? '#2a7f2a' : '#c0392b';
}

function clearResults() {
  overlay.innerHTML = '';
  gateBadge.hidden = true;
  qBox.classList.add('hidden');
}

// 기존 함수들 아래에
function renderMetaChips(res) {
  document.getElementById("meta-chips").style.display = "flex";
  document.getElementById("meta-item").textContent = res.item || "object";

  const considered = res.considered || [];
  if (considered.length) {
    document.getElementById("meta-considered-wrap").style.display = "inline";
    document.getElementById("meta-considered").innerHTML =
      considered.map(c => `<span class="chip chip-considered">${c}</span>`).join("");
  } else {
    document.getElementById("meta-considered-wrap").style.display = "none";
  }
}