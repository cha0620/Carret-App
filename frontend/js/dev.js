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

// ---- 파이프라인 결과 한눈에 보기 (storage/result) ----
let rsItems = [];
let rsShownItems = [];

// 피드백 태그 — 백엔드 store.FEEDBACK_TAGS 와 같은 키
const FB_TAGS = {
  defect_lost: '하자 사라짐', text_broken: '글자·로고 깨짐', shape_changed: '물건 모양 변형',
  color_changed: '색감 변함', framing: '구도·잘림', background: '배경 어색',
  bubble_wrong: '말풍선 위치 틀림', good: '좋음',
};
const fbOf = it => it.feedback || { user: null, agent: null };
const ratingOf = it => fbOf(it).user?.rating ?? fbOf(it).agent?.rating;

const num = v => (typeof v === 'number' ? v : null);
const fmt = (v, d = 2) => (num(v) == null ? '-' : v.toFixed(d));
const stars = n => {
  if (num(n) == null) return '-';
  const k = Math.max(0, Math.min(5, Math.round(n)));
  return '★'.repeat(k) + '☆'.repeat(5 - k);
};
const isNum = v => typeof v === 'number';
const RS_PAGE = 30;
let rsShown = RS_PAGE;

function rsFilterSort(items) {
  const f = $('rs-filter').value;
  const filtered = items.filter(it => {
    const ins = it.inspect || {};
    if (f === 'gate_fail') return ins.gate_passed === false;
    if (f === 'gate_pass') return ins.gate_passed === true;
    if (f === 'blocked') return ins.status === 'blocked';
    if (f === 'composite') return (ins.mode || 'generate') !== 'generate';
    if (f === 'mine_none') return !fbOf(it).user;
    if (f === 'mine_done') return !!fbOf(it).user;
    return true;
  });
  const key = {
    rating: ratingOf,
    fidelity: it => it.judge?.fidelity,
    dino: it => it.inspect?.visual_similarity,
  }[$('rs-sort').value];
  if (key) filtered.sort((a, b) => (key(a) ?? 99) - (key(b) ?? 99));   // 낮은 점수부터 = 문제부터
  return filtered;
}

function rsSummary(items) {
  const ins = items.map(it => it.inspect || {});
  const passed = ins.filter(i => i.gate_passed === true).length;
  const blocked = ins.filter(i => i.status === 'blocked').length;
  const avg = arr => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);
  const mine = items.map(it => fbOf(it).user?.rating).filter(isNum);
  const agent = items.map(it => fbOf(it).agent?.rating).filter(isNum);
  const fid = items.map(it => it.judge?.fidelity).filter(isNum);
  const dino = ins.map(i => i.visual_similarity).filter(isNum);
  return [
    chip(`총 ${items.length}장`),
    chip(`게이트 통과 ${passed}/${items.length}`, items.length ? passed === items.length : null),
    chip(`차단 ${blocked}`, blocked ? false : null),
    chip(`배경 교체 ${ins.filter(i => i.mode === 'composite').length}`),
    chip(`내 피드백 ${mine.length}/${items.length}`),
    chip(`내 평균 ★${fmt(avg(mine), 1)}`),
    chip(`에이전트 평균 ★${fmt(avg(agent), 1)}`),
    chip(`평균 fidelity ${fmt(avg(fid), 1)}`),
    chip(`평균 DINO ${fmt(avg(dino), 3)}`),
  ].join('');
}

function rsCard(it, idx) {
  const ins = it.inspect || {}, j = it.judge, { user: mine, agent } = fbOf(it);
  const name = it.name || it.file_id.slice(0, 8);
  const checks = ins.checks || [];
  const kept = checks.filter(c => c.preserved).length;
  const chips = [
    chip(ins.item || 'item ?'),
    chip(`게이트 ${ins.gate_passed == null ? '-' : ins.gate_passed ? '통과' : '실패'} (${kept}/${checks.length})`, ins.gate_passed),
    ins.status === 'blocked' ? chip('가드 차단', false) : '',
    ins.mode === 'composite' ? chip('배경 교체 모드 (원본 물건 픽셀)') : '',
    ins.mode === 'composite_failed' ? chip('배경 교체 실패', false) : '',
    ins.gate_retried ? chip('게이트 재생성 1회') : '',
    j ? chip(`F/R/T ${j.fidelity}/${j.realism}/${j.trust}`, j.fidelity >= 4) : chip('judge 없음'),
    chip(`DINO ${fmt(ins.visual_similarity, 3)}`),
    ins.gen_attempts ? chip(`생성 ${ins.gen_attempts}회`, ins.gen_attempts === 1 ? null : false) : '',
    agent ? chip(`에이전트 ${stars(agent.rating)}`, agent.rating >= 4) : '',
    mine ? chip(`나 ${stars(mine.rating)}`, mine.rating >= 4) : chip('내 피드백 없음'),
    isNum(it.db?.elapsed_s) ? chip(`${it.db.elapsed_s.toFixed(0)}s`) : '',
  ].join('');
  const checkList = checks.length
    ? checks.map(c => `<li class="${c.preserved ? 'ok' : 'bad'}">${c.preserved ? '✅' : '❌'} ${esc(c.what)}</li>`).join('')
    : '<li class="tc-empty">(앵커 없음)</li>';
  const guards = (ins.guard_report || []).filter(g => !g.passed)
    .map(g => chip(`${g.severity} ${g.name} ${fmt(g.value)}/${g.threshold}`, false)).join('');
  return `<div class="tc-item">
    <div class="tc-head"><b title="${esc(it.file_id)}">${esc(name)}</b>${chips}</div>
    <div class="rs-panes">
      <div class="pane"><span class="label">원본</span>
        ${it.orig ? `<a href="${esc(it.orig)}" target="_blank"><img src="${esc(it.orig)}" alt="원본" loading="lazy"></a>` : '<p class="tc-empty">원본 없음</p>'}</div>
      <div class="pane"><span class="label">${esc(it.preset)}</span>
        <a href="${esc(it.result)}" target="_blank"><img src="${esc(it.result)}" alt="결과" loading="lazy"></a>
        <div class="rs-overlay" data-idx="${idx}"></div></div>
    </div>
    ${guards ? `<div class="tc-head" style="margin-top:8px">${guards}</div>` : ''}
    <div class="tc-texts">
      <div><h4>하자 체크리스트 (verify)</h4><ul class="rs-checks">${checkList}</ul></div>
      <div>
        <h4>judge 분석</h4><div class="tc-summary" style="margin-top:0">${esc(j?.analysis) || '-'}</div>
        <h4 style="margin-top:8px">에이전트 코멘트</h4><div class="tc-summary" style="margin-top:0">${esc(agent?.comment) || '-'}</div>
      </div>
    </div>
    ${fbEditor(it)}
  </div>`;
}

function fbEditor(it) {
  const mine = fbOf(it).user;
  const rating = mine?.rating || 0;
  const tags = new Set(mine?.tags || []);
  const starBtns = [1, 2, 3, 4, 5].map(v =>
    `<button type="button" class="fb-star ${v <= rating ? 'on' : ''}" data-v="${v}">★</button>`).join('');
  const tagBtns = Object.entries(FB_TAGS).map(([k, label]) =>
    `<button type="button" class="fb-tag ${tags.has(k) ? 'on' : ''}" data-tag="${k}">${esc(label)}</button>`).join('');
  const when = mine ? `마지막 저장 ${esc(mine.updated_at || mine.created_at || '')}` : '아직 저장 안 함';
  return `<div class="fb-box" data-fid="${esc(it.file_id)}" data-preset="${esc(it.preset)}" data-rating="${rating}">
    <h4>내 피드백</h4>
    <div class="fb-row"><span class="fb-stars">${starBtns}</span><span class="fb-tags">${tagBtns}</span></div>
    <textarea class="fb-comment" rows="2" maxlength="2000" placeholder="무엇이 좋았고 무엇이 문제인지 적어주세요 (예: 왼쪽 소매 얼룩이 사라짐)">${esc(mine?.comment || '')}</textarea>
    <div class="btn-row"><button type="button" class="btn btn-primary fb-save">${mine ? '수정 저장' : '저장'}</button>
      <span class="fb-status">${when}</span></div>
  </div>`;
}

async function saveFeedback(box) {
  const status = box.querySelector('.fb-status');
  const rating = +box.dataset.rating;
  if (!rating) { status.textContent = '⚠️ 별점을 먼저 선택하세요'; return; }
  const tags = [...box.querySelectorAll('.fb-tag.on')].map(b => b.dataset.tag);
  const comment = box.querySelector('.fb-comment').value.trim() || null;
  status.textContent = '저장 중...';
  try {
    const r = await fetch('/api/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: box.dataset.fid, preset_key: box.dataset.preset, rating, comment, tags }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
    const saved = await r.json();
    const it = rsItems.find(x => x.file_id === box.dataset.fid && x.preset === box.dataset.preset);
    if (it) it.feedback = { ...fbOf(it), user: saved };
    $('rs-summary').innerHTML = rsSummary(rsItems);
    // 이 카드만 다시 그린다 — 다른 카드에 쓰던 코멘트가 날아가지 않게
    const card = box.closest('.tc-item');
    const idx = rsShownItems.indexOf(it);
    if (it && idx >= 0) {
      card.outerHTML = rsCard(it, idx);
      const fresh = $('rs-list').querySelector(`.rs-overlay[data-idx="${idx}"]`);
      if (fresh) drawBoxes(fresh, (it.inspect?.checks || []).filter(c => c.preserved), '#2ecc71');
      const newStatus = $('rs-list').querySelector(`.fb-box[data-fid="${it.file_id}"][data-preset="${it.preset}"] .fb-status`);
      if (newStatus) newStatus.textContent = '✅ 저장됨';
    }
  } catch (e) {
    status.textContent = `❌ 저장 실패: ${e.message}`;
  }
}

// 카드가 다시 그려져도 동작하도록 목록에 이벤트 위임
$('rs-list').addEventListener('click', e => {
  const box = e.target.closest('.fb-box');
  if (!box) return;
  const star = e.target.closest('.fb-star');
  if (star) {
    box.dataset.rating = star.dataset.v;
    box.querySelectorAll('.fb-star').forEach(b => b.classList.toggle('on', +b.dataset.v <= +star.dataset.v));
    return;
  }
  const tag = e.target.closest('.fb-tag');
  if (tag) { tag.classList.toggle('on'); return; }
  if (e.target.closest('.fb-save')) saveFeedback(box);
});

function renderResults() {
  const items = rsFilterSort(rsItems);
  $('rs-summary').innerHTML = rsSummary(rsItems);
  if (!items.length) {
    $('rs-list').innerHTML = '<p class="tc-empty">조건에 맞는 결과가 없습니다.</p>';
    return;
  }
  const shown = items.slice(0, rsShown);   // 이미지가 많으면 느려지니 나눠서 그린다
  rsShownItems = shown;
  $('rs-list').innerHTML = shown.map(rsCard).join('') + (items.length > shown.length
    ? `<button id="btn-rs-more" class="btn btn-ghost">더 보기 (${items.length - shown.length}장 남음)</button>` : '');
  const more = $('btn-rs-more');
  if (more) more.onclick = () => { rsShown += RS_PAGE; renderResults(); };
  // 보존된 하자 위치를 결과 위에 초록 박스로 (좌표 있는 것만)
  $('rs-list').querySelectorAll('.rs-overlay').forEach(el => {
    const it = shown[+el.dataset.idx];
    drawBoxes(el, (it.inspect?.checks || []).filter(c => c.preserved), '#2ecc71');
  });
}

async function loadResults() {
  try {
    const r = await fetch('/dev/results');
    if (!r.ok) throw new Error(r.status);
    rsItems = (await r.json()).items;
    renderResults();
  } catch (e) {
    $('rs-list').innerHTML = `<p class="tc-empty">불러오기 실패: ${esc(e.message)}</p>`;
  }
}
$('btn-rs-reload').onclick = loadResults;
$('rs-filter').onchange = $('rs-sort').onchange = () => { rsShown = RS_PAGE; renderResults(); };
loadResults();
