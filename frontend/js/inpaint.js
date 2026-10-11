/* 칠한 곳 고치기 (10-11) — 칠하기 → A · B → 고르기 → 고른 걸로 이어서. 서버: /api/inpaint
   A · B 가 어느 모델인지는 고른 뒤에 밝힌다 (블라인드). VLM · 사용자 글은 textContent 로만 */
const $ = id => document.getElementById(id);
const base = $('ip-base'), cv = $('ip-mask'), ctx = cv.getContext('2d');
const statusEl = $('ip-status');
let fileId = null, preset = null, round = null, erasing = false, busy = false;

// 서버 inpainter.FLASH_PROMPT · KONTEXT_PROMPT 의 한국어 (결과엔 실제 프롬프트 번역을 같이 — 사용자 규칙)
const PROMPT_KO = {
  flash: '사진 1 에서 사진 3 의 흰 영역 안만 바꿔라: {note}. 사진 2(같은 물건의 원본 사진)에 맞춰라.',
  kontext: '{note}. 참고 사진(같은 물건의 원본)에 맞춰라.',
};
const MODEL_KO = { flash: 'flash (지금 생성 모델 + 칠한 곳만 붙이기)', kontext: 'Kontext 인페인팅 (마스크 + 원본 참고)' };

function load(fid, key) {
  fileId = fid; preset = key;
  round = null;                                  // 옛 사진의 A·B 는 치운다 (10-11 리뷰)
  $('ab-card').classList.add('hidden');
  history.replaceState(null, '', `?file_id=${encodeURIComponent(fid)}&preset=${encodeURIComponent(key)}`);
  base.onload = () => {
    cv.width = base.naturalWidth; cv.height = base.naturalHeight;
    ctx.clearRect(0, 0, cv.width, cv.height);
    $('paint-card').classList.remove('hidden');
    statusEl.textContent = '';
  };
  base.onerror = () => { statusEl.textContent = '사진을 불러오지 못했어요 — file_id · 결과 키를 확인해 주세요'; };
  base.src = `/storage/result/${fid}_${key}.jpg?t=${Date.now()}`;
  $('paint-card').classList.remove('hidden');
  loadStats();
}

// ---- 칠하기 ----
let drawing = false, last = null;
function pos(e) {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height];
}
function stroke(p) {
  const r = cv.getBoundingClientRect();
  ctx.globalCompositeOperation = erasing ? 'destination-out' : 'source-over';
  ctx.strokeStyle = '#ff2a2a';
  ctx.lineWidth = $('ip-brush').value * cv.width / r.width;
  ctx.lineCap = ctx.lineJoin = 'round';
  ctx.beginPath(); ctx.moveTo(...(last || p)); ctx.lineTo(...p); ctx.stroke();
  last = p;
}
cv.addEventListener('pointerdown', e => { drawing = true; last = null; cv.setPointerCapture(e.pointerId); stroke(pos(e)); });
cv.addEventListener('pointermove', e => { if (drawing) stroke(pos(e)); });
['pointerup', 'pointercancel'].forEach(t => cv.addEventListener(t, () => { drawing = false; last = null; }));
$('ip-erase').onclick = () => { erasing = !erasing; $('ip-erase').classList.toggle('on', erasing); };
$('ip-clear').onclick = () => ctx.clearRect(0, 0, cv.width, cv.height);

function painted() {
  const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
  for (let i = 3; i < d.length; i += 4) if (d[i]) return true;
  return false;
}

// ---- A · B ----
$('ip-run').onclick = async () => {
  if (busy || !fileId) return;
  const note = $('ip-note').value.trim();
  if (!painted()) { statusEl.textContent = '고칠 곳을 먼저 칠해 주세요'; return; }
  if (!note) { statusEl.textContent = '칠한 곳을 어떻게 고칠지 적어 주세요'; return; }
  busy = true; $('ip-run').disabled = true; $('ip-load').disabled = true;
  const fid = fileId;
  statusEl.textContent = 'A · B 만드는 중... (수십 초 걸려요)';
  try {
    const r = await fetch('/api/inpaint', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId, preset, mask: cv.toDataURL('image/png'), note }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `실패 (${r.status})`);
    if (fid !== fileId) return;
    round = { ...j, note, fid };
    for (const s of ['a', 'b']) {
      const img = $('ip-' + s), res = j.results[s];
      img.src = res ? res.url + '?t=' + Date.now() : '';
      img.alt = res ? s.toUpperCase() : `${s.toUpperCase()} 실패`;
      document.querySelector(`[data-pick="${s}"]`).disabled = !res;
      $(`ip-${s}-model`).textContent = res ? '' : '(실패)';
    }
    $('ip-prompts').classList.add('hidden');
    $('ab-card').classList.remove('hidden');
    document.querySelectorAll('[data-pick]').forEach(b => { if (b.dataset.pick === 'none' || j.results[b.dataset.pick]) b.disabled = false; });
    statusEl.textContent = j.failed.length ? `한쪽(${j.failed.join(', ').toUpperCase()})이 실패했어요` : '';
    $('ab-card').scrollIntoView({ behavior: 'smooth' });
  } catch (e) {
    statusEl.textContent = 'A · B 만들기 실패: ' + e.message;
  } finally {
    busy = false; $('ip-run').disabled = false; $('ip-load').disabled = false;
  }
};

document.querySelectorAll('[data-pick]').forEach(btn => btn.onclick = async () => {
  if (!round || busy) return;
  busy = true;
  document.querySelectorAll('[data-pick]').forEach(b => { b.disabled = true; });
  try {
    const r = await fetch('/api/inpaint/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: round.fid, round_id: round.round_id, pick: btn.dataset.pick }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.status);
    // 고른 뒤에 모델을 밝힌다 + 실제 프롬프트와 한국어 번역
    for (const s of ['a', 'b']) $(`ip-${s}-model`).textContent = j.slots[s] ? `— ${MODEL_KO[j.slots[s]]}` : '';
    $('ip-prompt-text').textContent = Object.entries(j.prompts).map(([m, p]) =>
      `[${MODEL_KO[m] || m}]\n${p}` + (PROMPT_KO[m] ? `\n→ ${PROMPT_KO[m].replace('{note}', round.note)}` : '')).join('\n\n');
    $('ip-prompts').classList.remove('hidden');
    const li = document.createElement('li');
    const picked = btn.dataset.pick === 'none' ? '둘 다 별로' : `${btn.dataset.pick.toUpperCase()} (${MODEL_KO[j.slots[btn.dataset.pick]]})`;
    li.textContent = `"${round.note}" → ${picked}`;
    $('ip-history').prepend(li);
    // 고른 사진이 다음 칠하기의 바탕
    load(round.fid, j.next);
    statusEl.textContent = '고른 사진으로 이어서 칠할 수 있어요';
    loadStats();
  } catch (e) {
    statusEl.textContent = '기록 실패: ' + e.message;
    document.querySelectorAll('[data-pick]').forEach(b => { b.disabled = false; });
  } finally {
    busy = false;
  }
});

async function loadStats() {
  try {
    const s = await (await fetch('/api/inpaint/stats')).json();
    $('ip-stats').textContent = `지금까지 고른 횟수 — flash ${s.flash} · Kontext ${s.kontext} · 둘 다 별로 ${s.none}`;
  } catch (_) { /* 통계는 없어도 된다 */ }
}

$('ip-load').onclick = () => {
  if (busy) return;
  const f = $('ip-file').value.trim(), k = $('ip-preset').value.trim();
  if (/^[a-f0-9]{32}$/.test(f) && k) load(f, k);
  else statusEl.textContent = 'file_id(32자리)와 결과 키를 넣어 주세요';
};
const q = new URLSearchParams(location.search);
if (q.get('file_id') && q.get('preset')) {
  $('ip-file').value = q.get('file_id'); $('ip-preset').value = q.get('preset');
  load(q.get('file_id'), q.get('preset'));
} else loadStats();
