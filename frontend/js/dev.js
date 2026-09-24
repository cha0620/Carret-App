/* 테스트 랩 — 눈으로 보고, 눈으로 검증 */
const $ = id => document.getElementById(id);
const out = $('out');
let sel = null, selOrig = null;

function drawBoxes(container, items, color) {
  items.forEach(it => {
    if (it.x2 == null) return;
    const b = document.createElement('div');
    b.className = 'box';
    b.style.borderColor = color;
    b.style.left = it.x1 / 10 + '%';
    b.style.top = it.y1 / 10 + '%';
    b.style.width = (it.x2 - it.x1) / 10 + '%';
    b.style.height = (it.y2 - it.y1) / 10 + '%';
    const t = document.createElement('span');
    t.className = 'tag';
    t.style.background = color;
    t.style.left = it.x1 / 10 + '%';
    t.style.top = it.y1 / 10 + '%';
    t.textContent = it.what;
    container.append(b, t);
  });
}

async function load() {
  const g = await fetch('/dev/gallery').then(r => r.json());

  $('gallery').innerHTML = g.pairs.map(p => `
    <div class="card" data-name="${p.name}">
      <img src="${p.orig}"><img src="${p.after}">
      <span>${p.name}</span>
    </div>`).join('');
  $('gallery').querySelectorAll('.card').forEach(c => c.onclick = () => {
    $('gallery').querySelectorAll('.card').forEach(x => x.classList.remove('sel'));
    c.classList.add('sel');
    sel = c.dataset.name;
    const p = g.pairs.find(x => x.name === sel);
    $('v-orig').src = p.orig;
    $('v-after').src = p.after;
    $('o-after').innerHTML = '';
    $('metrics').textContent = '';
    $('pair-viewer').classList.add('show');
  });

  $('origs').innerHTML = g.originals.map(o => `
    <div class="card" data-name="${o.name}">
      <img src="${o.url}"><span>${o.name.slice(0, 8)}</span>
    </div>`).join('');
  $('origs').querySelectorAll('.card').forEach(c => c.onclick = () => {
    $('origs').querySelectorAll('.card').forEach(x => x.classList.remove('sel'));
    c.classList.add('sel');
    selOrig = c.dataset.name;
  });
}
load();

$('btn-anchor').onclick = async () => {
  if (!sel) { out.textContent = '⚠️ 페어를 선택하세요'; return; }
  out.textContent = '실행 중... (수 초)';
  const r = await fetch('/dev/eval-anchor', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: sel }),
  });
  const d = await r.json();
  $('o-after').innerHTML = '';
  drawBoxes($('o-after'), d.matched.map(m => m.result), '#2ecc71'); // 생존=초록
  drawBoxes($('o-after'), d.new, '#e67e22');                        // 환각=주황
  $('metrics').textContent =
    `recall=${d.recall} precision=${d.precision} | ` +
    `✅${d.matched.length} ❌${d.missed.length} ⚠️${d.new.length}`;
  out.textContent = JSON.stringify(d, null, 2);
};

$('btn-all').onclick = async () => {
  out.textContent = '전체 페어 실행 중... (느림)';
  const r = await fetch('/dev/eval-all');
  out.textContent = JSON.stringify(await r.json(), null, 2);
};

$('btn-detect').onclick = async () => {
  if (!selOrig) { out.textContent = '⚠️ 원본을 선택하세요'; return; }
  out.textContent = '실행 중...';
  const r = await fetch('/dev/detect', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: selOrig }),
  });
  out.textContent = JSON.stringify(await r.json(), null, 2);
};

$('btn-with-result').onclick = async () => {
  const file = $('result-file').files[0];
  if (!selOrig) { out.textContent = '⚠️ 위에서 원본을 먼저 선택하세요'; return; }
  if (!file) { out.textContent = '⚠️ 결과로 쓸 이미지를 선택하세요'; return; }

  out.textContent = '실행 중... (generate 없이 verify+judge만)';
  const fd = new FormData();
  fd.append('file_id', selOrig);
  fd.append('preset', 'studio_white');
  fd.append('result', file);

  const r = await fetch('/dev/transform-with-result', { method: 'POST', body: fd });
  if (!r.ok) { out.textContent = `❌ 실패 (${r.status})\n` + await r.text(); return; }
  const d = await r.json();

  $('wr-viewer').classList.add('show');
  $('wr-orig').src = `/storage/original/${selOrig}.jpg`;
  $('wr-after').src = d.result_url + '?t=' + Date.now();
  $('wr-overlay').innerHTML = '';
  drawBoxes($('wr-overlay'), d.bubbles || [], '#2ecc71');
  $('wr-metrics').textContent =
    `item=${d.item} gate_passed=${d.gate_passed} bubbles=${(d.bubbles || []).length}`;
  out.textContent = JSON.stringify(d, null, 2);
};
// ---- 텍스트/로고 비교 (storage/text_check) ----
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function tokens(list, other, cls) {
  if (!list || !list.length) return '<span class="tc-empty">(없음)</span>';
  const set = new Set(other || []);
  return list.map(t => `<span class="tok ${set.has(t) ? '' : cls}">${esc(t)}</span>`).join('');
}

function chip(label, ok) {
  return `<span class="chip ${ok === true ? 'ok' : ok === false ? 'bad' : ''}">${esc(label)}</span>`;
}

function renderTextCheck(items) {
  if (!items.length) {
    $('tc-list').innerHTML = '<p class="tc-empty">storage/text_check/input/ 에 사진이 없습니다.</p>';
    return;
  }
  $('tc-list').innerHTML = items.map(it => {
    const r = it.report;
    const chips = r ? [
      chip(`text_recall ${r.text_recall}`, r.guard_ocr_match === 'pass'),
      chip(`ocr_match ${r.guard_ocr_match}`, r.guard_ocr_match === 'pass'),
      chip(`no_added_text ${r.guard_no_added_text}`, r.guard_no_added_text === 'pass'),
      chip(`VLM 판정 ${r.vlm_compare?.overall ?? '-'}`, r.vlm_compare ? r.vlm_compare.overall === 'intact' : null),
      chip(`생성 ${r.gen_seconds}s`),
    ].join('') : chip('아직 실행 안 됨');
    const panes = [`<div class="pane"><span class="label">원본</span>
        <a href="${it.orig}" target="_blank"><img src="${it.orig}" alt="원본"></a></div>`]
      .concat(it.results.map(res => `<div class="pane"><span class="label">${esc(res.preset)}</span>
        <a href="${res.url}" target="_blank"><img src="${res.url}" alt="결과"></a></div>`))
      .join('');
    const texts = r ? `
      <div class="tc-texts">
        <div><h4>원본에서 읽은 글자</h4>${tokens(r.texts_before, r.texts_after, 'missing')}</div>
        <div><h4>결과에서 읽은 글자</h4>${tokens(r.texts_after, r.texts_before, 'added')}</div>
      </div>
      <div class="tc-summary">${esc(r.vlm_compare?.summary)}</div>` : '';
    return `<div class="tc-item"><div class="tc-head"><b>${esc(it.name)}</b>${chips}</div>
      <div class="tc-panes">${panes}</div>${texts}</div>`;
  }).join('');
}

async function loadTextCheck() {
  try {
    const r = await fetch('/dev/text-check');
    if (!r.ok) throw new Error(r.status);
    renderTextCheck((await r.json()).items);
  } catch (e) {
    $('tc-list').innerHTML = `<p class="tc-empty">불러오기 실패: ${esc(e.message)}</p>`;
  }
}
$('btn-tc-reload').onclick = loadTextCheck;
loadTextCheck();
