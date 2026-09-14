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

  $('wr-viewer').style.display = 'grid';
  $('wr-orig').src = `/storage/original/${selOrig}.jpg`;
  $('wr-after').src = d.result_url + '?t=' + Date.now();
  $('wr-overlay').innerHTML = '';
  drawBoxes($('wr-overlay'), d.bubbles || [], '#2ecc71');
  $('wr-metrics').textContent =
    `item=${d.item} gate_passed=${d.gate_passed} bubbles=${(d.bubbles || []).length}`;
  out.textContent = JSON.stringify(d, null, 2);
};