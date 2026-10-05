/* 테스트 랩 — 눈으로 보고, 눈으로 검증 */
const $ = id => document.getElementById(id);
const out = $('out');
let selOrig = null;

async function load() {
  const g = await fetch('/dev/gallery').then(r => r.json());

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
  $('wr-metrics').textContent =
    `item=${d.item} gate_passed=${d.gate_passed}`;
  out.textContent = JSON.stringify(d, null, 2);
};
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function chip(label, ok) {
  return `<span class="chip ${ok === true ? 'ok' : ok === false ? 'bad' : ''}">${esc(label)}</span>`;
}

// ---- 파이프라인 결과 한눈에 보기 (storage/result) ----
let rsItems = [];
let rsShownItems = [];

// 피드백 태그 — 백엔드 store.FEEDBACK_TAGS 와 같은 키
const FB_TAGS = {
  defect_lost: '하자 사라짐', text_broken: '글자·로고 깨짐', shape_changed: '물건 모양 변형',
  color_changed: '색감 변함', framing: '구도·잘림', background: '배경 어색',
  good: '좋음',
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
    if (f === 'composite') return (ins.mode || 'generate') !== 'generate';
    if (f === 'document' || f === 'inside_view') return ins.photo_type === f;
    if (f === 'mine_none') return !fbOf(it).user;
    if (f === 'mine_done') return !!fbOf(it).user;
    return true;
  });
  const key = {
    rating: ratingOf,
    fidelity: it => it.judge?.fidelity,
    dino: it => it.inspect?.visual_similarity,
    item: it => it.inspect?.item_similarity,
  }[$('rs-sort').value];
  if (key) filtered.sort((a, b) => (key(a) ?? 99) - (key(b) ?? 99));   // 낮은 점수부터 = 문제부터
  return filtered;
}

function rsSummary(items) {
  const ins = items.map(it => it.inspect || {});
  const passed = ins.filter(i => i.gate_passed === true).length;
  const avg = arr => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);
  const mine = items.map(it => fbOf(it).user?.rating).filter(isNum);
  const agent = items.map(it => fbOf(it).agent?.rating).filter(isNum);
  const fid = items.map(it => it.judge?.fidelity).filter(isNum);
  const dino = ins.map(i => i.visual_similarity).filter(isNum);
  const itemSim = ins.map(i => i.item_similarity).filter(isNum);
  return [
    chip(`총 ${items.length}장`),
    chip(`게이트 통과 ${passed}/${items.length}`, items.length ? passed === items.length : null),
    chip(`배경 교체 ${ins.filter(i => i.mode === 'composite').length}`),
    chip(`내 피드백 ${mine.length}/${items.length}`),
    chip(`내 평균 ★${fmt(avg(mine), 1)}`),
    chip(`에이전트 평균 ★${fmt(avg(agent), 1)}`),
    chip(`평균 fidelity ${fmt(avg(fid), 1)}`),
    chip(`평균 DINO ${fmt(avg(dino), 3)}`),
    chip(`평균 누끼 DINO ${fmt(avg(itemSim), 3)} (n=${itemSim.length})`),
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
    ins.photo_type ? chip(`종류 ${ins.photo_type}`) : '',
    ins.wear_level ? chip(`하자 ${ins.wear_level}`, ins.wear_level === 'heavy' ? false : null) : '',
    ins.watermark && ins.watermark !== 'none' ? chip(`워터마크 ${ins.watermark}`) : '',
    ins.mode === 'composite' ? chip(`배경 교체 (${ins.composite_reason || '?'})`) : '',
    ins.mode === 'original' ? chip(`원본 그대로 (${ins.composite_reason || '?'})`) : '',
    ins.mode === 'composite_failed' ? chip('배경 교체 실패', false) : '',
    ins.gate_retried ? chip('게이트 재생성 1회') : '',
    j ? chip(`F/R/T ${j.fidelity}/${j.realism}/${j.trust}`, j.fidelity >= 4) : chip('judge 없음'),
    chip(`DINO ${fmt(ins.visual_similarity, 3)}`),
    isNum(ins.item_similarity) ? chip(`누끼 DINO ${fmt(ins.item_similarity, 3)}`) : '',
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
        <a href="${esc(it.result)}" target="_blank"><img src="${esc(it.result)}" alt="결과" loading="lazy"></a></div>
    </div>
    ${guards ? `<div class="tc-head" style="margin-top:8px">${guards}</div>` : ''}
    <div class="tc-texts">
      <div><h4>마크 체크리스트 (verify)</h4><ul class="rs-checks">${checkList}</ul></div>
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
      const newStatus = $('rs-list').querySelector(
        `.fb-box[data-fid="${CSS.escape(it.file_id)}"][data-preset="${CSS.escape(it.preset)}"] .fb-status`);
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
}

// 결과가 쌓이면 한 번에 받기가 느리다 — 첫 페이지를 먼저 그리고 나머지는 이어 받아 덧붙인다.
// 다시 불러오기를 누르면 이전 차례는 버린다 (rsLoadSeq).
const RS_FETCH = 50;
let rsLoadSeq = 0;
async function loadResults() {
  const seq = ++rsLoadSeq;
  rsItems = [];
  rsShown = RS_PAGE;
  // 페이지 사이에 새 결과가 생기면 목록이 밀려 같은 항목이 또 온다 — 키로 거른다.
  // 카드는 첫 페이지와 끝에서만 다시 그린다 (받는 동안 쓰던 피드백이 날아가지 않게).
  const seen = new Set();
  let total = 0;
  try {
    for (let offset = 0; ; offset += RS_FETCH) {
      const r = await fetch(`/dev/results?offset=${offset}&limit=${RS_FETCH}`);
      if (!r.ok) throw new Error(r.status);
      const body = await r.json();
      if (seq !== rsLoadSeq) return;
      total = body.total;
      for (const it of body.items) {
        const k = `${it.file_id}_${it.preset}`;
        if (!seen.has(k)) { seen.add(k); rsItems.push(it); }
      }
      if (offset === 0) renderResults();
      else $('rs-summary').innerHTML = rsSummary(rsItems) + ` <span class="tc-empty">받는 중 ${rsItems.length}/${total}</span>`;
      if (!body.items.length || offset + RS_FETCH >= total) break;
    }
    renderResults();
  } catch (e) {
    if (seq !== rsLoadSeq) return;
    if (!rsItems.length) {
      $('rs-list').innerHTML = `<p class="tc-empty">불러오기 실패: ${esc(e.message)}</p>`;
      return;
    }
    renderResults();   // 받은 만큼은 보여 주되, 일부라는 걸 알린다
    $('rs-summary').innerHTML += ` <span class="tc-empty">⚠ ${rsItems.length}/${total} 만 받음 (${esc(e.message)}) — 다시 불러오기</span>`;
  }
}
$('btn-rs-reload').onclick = loadResults;
$('rs-filter').onchange = $('rs-sort').onchange = () => { rsShown = RS_PAGE; renderResults(); };
loadResults();
