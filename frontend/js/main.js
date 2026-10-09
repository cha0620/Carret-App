/* 조율 계층 — 상태 + 이벤트. api 와 render 를 연결. */

const PRESET = 'studio_white';   // 프리셋 선택 UI는 없앰 — 항상 화이트 스튜디오로 변환

let fileId = null;

const dropzone  = document.getElementById('dropzone');
const fileInput = document.getElementById('file');
const urlInput  = document.getElementById('url-input');
const urlBtn    = document.getElementById('url-run');
const runBtn    = document.getElementById('run');
const statusEl  = document.getElementById('status');
const beforeImg = document.getElementById('before');
const afterImg  = document.getElementById('after');

const feedbackSubmitBtn = document.getElementById('feedback-submit');
let currentRating = 0;
let resetZoomScale = null;   // initZoom() 이 채움 — render.js 의 resetZoom() 에서 호출

// ---- 업로드: 근거 칸 (보증서 · 정품 마크 · 영수증) ----
// 물건이 이미 있으면 바로 더하고, 없으면 모아 뒀다가 상품 사진과 같이 보낸다
const proofZone = document.getElementById('proof-zone');
const proofInput = document.getElementById('proof-file');
const proofPendingEl = document.getElementById('proof-pending');
let pendingProof = [];

function setPendingProof(files) {
  pendingProof = files;
  proofPendingEl.replaceChildren();
  if (!files.length) return;
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'proof-cancel';
  cancel.textContent = '취소';
  cancel.onclick = e => { e.stopPropagation(); setPendingProof([]); };   // 칸을 눌러 파일 창이 열리지 않게
  proofPendingEl.append(`근거 사진 ${files.length}장 — 상품 사진을 올리면 같이 보내요 `, cancel);
}

function handleProof(files) {
  if (!files.length || busy) return;
  if (item) {
    if (draftDirty) { statusEl.textContent = '고친 물건 묶음을 먼저 저장해 주세요'; return; }
    handleFiles([], true, files);
  } else {
    setPendingProof([...pendingProof, ...files]);
  }
}

proofZone.onclick = () => { if (!busy) proofInput.click(); };
proofZone.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); proofZone.click(); } };
proofInput.onchange = () => { handleProof([...proofInput.files]); proofInput.value = ''; };
proofZone.ondragover  = e => { e.preventDefault(); proofZone.classList.add('over'); };
proofZone.ondragleave = () => proofZone.classList.remove('over');
proofZone.ondrop = e => {
  e.preventDefault();
  proofZone.classList.remove('over');
  handleProof([...e.dataTransfer.files]);
};

// ---- 업로드: 파일 ----
dropzone.onclick = () => { if (!busy) fileInput.click(); };
fileInput.onchange = () => handleFiles([...fileInput.files]);
dropzone.ondragover  = e => { e.preventDefault(); dropzone.classList.add('over'); };
dropzone.ondragleave = () => dropzone.classList.remove('over');
dropzone.ondrop = e => {
  e.preventDefault();
  dropzone.classList.remove('over');
  handleFiles([...e.dataTransfer.files]);
};

// ---- 업로드: 사진 여러 장 · 동영상 → 물건 묶음 ----
let item = null;                 // 지금 물건 묶음 (/api/items 응답)
const addBtn = document.getElementById('item-add');
const addInput = document.getElementById('item-add-file');

const objSaveBtn = document.getElementById('obj-save');
const objStatus = document.getElementById('obj-status');
let draft = { objects: [], photos: [] };   // 사진 속 물건 — 고치는 중인 묶음 (저장하면 서버 응답으로 다시 만든다)
let draftDirty = false;
let objMsg = '';                 // 저장 결과 · 오류 — 다시 그려도 지우지 않는다

let busy = false;                // 업로드·변환 중 — 다른 동작이 섞이지 않게 (10-01 리뷰)
let uploadToken = 0;             // 늦게 도착한 업로드 응답을 버리기 위한 세대

function setBusy(on) {
  busy = on;
  for (const el of [dropzone, proofZone, addBtn, urlBtn]) {
    el.classList.toggle('disabled', on);
    if ('disabled' in el) el.disabled = on;
  }
  document.getElementById('item-photos').classList.toggle('locked', on);
  for (const b of document.querySelectorAll('.sell-check')) b.disabled = on;   // 변환 중엔 고르기 잠금
  for (const el of document.querySelectorAll('#obj-list select, #obj-list input')) el.disabled = on;
  objSaveBtn.disabled = on || !needsSave();
  runBtn.disabled = on || !canRun();
  if (item) drawArrange();
}

// 변환할 수 있나 — 근거 사진 · 안 파는 물건 사진은 원본 그대로 둔다 (서버는 묶음을 모르니 여기서 지킨다)
function canRun() {
  if (!fileId || analyzing) return false;
  const role = photoRole(fileId);
  return role !== 'proof' && role !== 'keep';
}

function needsSave() {
  return !!item && draftDirty;   // 설명을 고쳤을 때만 (묶음 확인 단계는 없앴다, 10-09)
}

let composition = null;          // 고른 정석 구도 키 (신발만 — 없으면 null)
let sell = null;                 // 고른 팔 물건 번호 (analyze objects) — 목록이 없으면 null (분석 판단대로)
let contentsHidden = false;      // 세트인데 안의 구성품이 어느 사진에도 안 보인다 (10-09)
let analysisObjects = null;      // 분석이 준 사진 속 물건 (따로 만들 때 이름 표시용)
let accessories = [];            // 부가품으로 고른 번호 — 같이 팔지만 대표사진에선 뺀다 (10-09)
let sellDirty = false;           // 사용자가 기본 체크를 바꿨나 — 안 바꿨으면 서버에 보내지 않는다 (분석 판단대로)
let analyzing = false;           // 미리 분석 중 — 끝나기 전에 변환하면 서버가 같은 사진을 두 번 분석한다
let analyzeToken = 0;            // 사진을 바꾸면 늦게 온 분석 응답을 버린다

function resetSell() {
  ++analyzeToken;
  ++compToken;
  fullPending = false;
  compPending = false;
  compApplied = false;
  sell = null;
  accessories = [];
  mains = [];
  contentsHidden = false;
  refFile = null;                // 새 사진이면 정답 구도도 처음부터 (10-09 리뷰)
  refPicked = false;
  analysisObjects = null;
  sellDirty = false;
  analyzing = false;
  renderSell(null);
  renderMultiResults([]);
  if (cancelAsk) cancelAsk();
}

// 사진을 고르는 순간 두 호출을 같이 보낸다 (10-09): 게시글 구성품(빠름 — 목록 · 담길 것을 먼저 보여 줌)과
// 전체 분석(배경만 바꾸는 이유 안내 · 변환 준비). 둘은 토큰을 따로 둔다 — "같이 넣기"를 바꿔 구성품만 다시 받아도
// 진행 중인 전체 분석은 끊지 않는다. 변환 버튼은 둘 다 끝나야 열린다 (10-09 리뷰)
let compToken = 0;
let fullPending = false;
let compPending = false;
let compApplied = false;

function syncAnalyzing() {
  analyzing = fullPending || compPending;
  renderContents();
  runBtn.disabled = busy || !canRun();
}

function applyObjects(objs, hidden = false) {
  analysisObjects = objs;
  contentsHidden = hidden;
  // 기본값은 분석 판단 — 본품 · 구성품은 사진에, 부가품(상자 · 충전기)은 부가품으로 (서버 기본 동작과 같다)
  sell = objs.filter(o => o.for_sale && o.role !== 'accessory').map(o => o.index);
  accessories = objs.filter(o => o.for_sale && o.role === 'accessory').map(o => o.index);
  if (!sell.length) { sell = objs.map(o => o.index); accessories = []; }
  mains = objs.filter(o => sell.includes(o.index) && o.role === 'main').map(o => o.index);
  if (!mains.length && sell.length) mains = [sell[0]];
  sellDirty = false;
  renderSell(objs.length ? objs : null);   // 보이는 물건은 하나여도 나열한다 (10-09 사용자)
  if (draft) drawObjects();                      // 묶음 화면의 "구성품:" 줄도 (10-09)
  renderRefs();
  renderContents();
}

function loadComponents(fid) {
  const ct = ++compToken;
  compPending = true;
  compApplied = false;
  sell = null; accessories = []; mains = []; sellDirty = false; analysisObjects = null; contentsHidden = false;
  renderSell(null);
  syncAnalyzing();
  return fetchComponents(fid, extraViewIds(), item && item.item_id)
    .then(r => {
      if (ct !== compToken || fid !== fileId) return;
      compApplied = true;
      applyObjects(r.objects || [], !!r.contents_hidden);
    })
    .catch(e => console.warn('구성품 목록 실패', e))   // 조용히 삼키지 않는다 — 10-09 빈 목록 버그가 안 보였다
    .finally(() => {
      if (ct !== compToken) return;
      compPending = false;
      syncAnalyzing();
    });
}

async function loadSellObjects(fid, refreshOnly = false) {
  if (refreshOnly) {             // 같이 넣을 사진만 바뀌었다 — 구성품 목록만 다시 (전체 분석은 그대로)
    loadComponents(fid);
    return;
  }
  resetSell();
  const token = analyzeToken;
  fullPending = true;
  const quick = loadComponents(fid);
  try {
    const a = await analyzePhoto(fid);
    if (token !== analyzeToken || fid !== fileId) return;
    // 생성 대신 배경만 바꾸는 사진 — 이유와 어떻게 하면 정리까지 되는지 알려준다 (10-03)
    const NOTE = {
      cut_off: '물건이 사진에 다 안 나와서 배경만 바꿔요 — 물건 전체가 나오게 다시 찍으면 정리까지 해 드려요',
      multi_item: '물건이 여러 개라 배경만 바꿔요 (개수가 바뀌지 않게) — 한 개만 팔면 아래에서 하나만 고르세요',
    };
    if (NOTE[a.reason] && !busy) statusEl.textContent = NOTE[a.reason];
    await quick;
    // 빠른 목록이 실패했을 때만 분석 목록으로 (서버가 분석 objects 를 구성품 목록으로 맞춰 주므로 번호가 같다)
    if (!compApplied && !compPending && fid === fileId) applyObjects(a.objects || [], !!a.contents_hidden);
  } catch (_) { /* 분석 실패 — 고르기 없이 변환 (서버가 판단) */ }
  finally {
    if (token === analyzeToken) {
      fullPending = false;
      syncAnalyzing();
    }
  }
}

// 구성품 종류 — 서버 set_pieces 와 같은 규칙 (끝 번호를 뗀 이름). 본품(main)은 따로 한 줄
function kindOf(o) {
  return o.role === 'main' || !o.role ? `main:${o.what}` : o.what.trim().toLowerCase().replace(/[\s#]*\d+$/, '');
}

// 화면 이름 — 분석의 한국어 이름(name_ko), 옛 분석이면 영어 이름에서 끝 번호만 뗀다 (10-09)
function nameOf(o) {
  return (o.name_ko || o.what).trim().replace(/[\s#]*\d+$/, '');
}

// ---- 정답 구도 고르기 · 같이 넣을 사진 (10-09) ----
let refs = null;                 // GET /api/refs — 선 그림이 있는 정답 구도
let refFile = null;              // 고른 정답 (null = 고르지 않음)
let refPicked = false;           // 판매자가 직접 골랐나 — 아니면 품목에 맞는 정답이 기본 (10-09 사용자)

// 품목에 맞는 정답 — 물건 이름(묶음 · 분석의 본품)에 정답의 품목 낱말이 있으면 그것, 없으면 같은 종류의 첫 정답
function defaultRef(list) {
  const o = currentObject();
  const main = (analysisObjects || []).find(x => x.role === 'main') || {};
  const text = [o && o.label, item && item.item, main.what, main.name_ko].filter(Boolean).join(' ').toLowerCase();
  const hit = list.find(r => (r.items || []).some(k => text.includes(String(k).toLowerCase())));
  if (hit) return hit.file;
  const same = o && o.category ? list.find(r => r.category === o.category) : null;
  return same ? same.file : null;
}

function currentObject() {
  const p = draft && draft.photos.find(x => x.file_id === fileId);
  return p && p.object ? objOf(p.object) : null;
}

let review = null;               // AI 사진 검토 {object_id, ref_file, photos:[{file_id, shows}], missing:[{what, hint}]} (10-09)
let reviewKey = '';
let withUser = false;            // "같이 넣기"를 판매자가 직접 바꿨나 — 아니면 AI 추천 + 빈자리는 자동으로 채운다
let basePicked = false;          // 판매자가 기준 사진을 직접 눌렀나 — 아니면 AI 추천으로 바꿔 준다 (10-09)
let withIds = null;              // 판매자가 고른 "같이 넣기" 사진 (null = 자동: 각도가 다른 것 2장) (10-09)
let refsToken = 0;
async function renderRefs() {
  const wrap = document.getElementById('ref-wrap');
  const list = document.getElementById('ref-list');
  const token = ++refsToken;
  if (refs === null) {
    const got = await fetchRefs().catch(() => null);
    if (got) refs = got;                       // 실패면 다음에 다시 받는다
  }
  if (token !== refsToken) return;             // 그사이 사진이 바뀌었다 — 늦은 그리기는 버린다
  if (!refs) { wrap.classList.add('hidden'); return; }
  const o = currentObject();
  const mine = o && o.category ? refs.filter(r => r.category === o.category) : [];
  const shown = mine.length ? mine : refs;      // 종류가 맞는 게 없으면 전부
  if (refFile && !shown.some(r => r.file === refFile)) { refFile = null; refPicked = false; }
  if (!refPicked) refFile = defaultRef(refs);   // 품목에 맞는 정답이 기본 — 이름이 맞으면 종류가 달라도 (보드게임 → 세트)
  loadReview();   // 정답이 정해졌으니 그 기준으로 사진 검토
  const shownAll = refFile && !shown.some(r => r.file === refFile) ? [refs.find(r => r.file === refFile), ...shown] : shown;
  list.innerHTML = '';
  wrap.querySelectorAll('.ref-note').forEach(x => x.remove());
  wrap.classList.toggle('hidden', !fileId || !shown.length);
  const card = (file, src, text) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ref-card';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(refFile === file));
    if (src) {
      const img = document.createElement('img');
      img.src = src;
      img.alt = '';
      b.append(img);
    }
    const t = document.createElement('span');
    t.textContent = text;
    b.append(t);
    b.onclick = () => { refFile = file; refPicked = true; renderRefs(); };
    list.append(b);
  };
  for (const r of shownAll) card(r.file, r.sketch_url, r.note);
  card(null, null, '고르지 않음');
  // 세트(사진에 넣을 구성품이 여럿)는 정답 사진의 배치를 모델이 구성품에 맞춰 문장으로 바꿔 넣는다 (10-09)
  if (sell && sell.length >= 2) {
    const n = document.createElement('p');
    n.className = 'ref-note';
    n.textContent = '구성품이 여럿이라, 고른 정답 사진의 배치를 보고 모델이 이 구성품에 맞는 배치를 정해서 만들어요';
    list.after(n);
  }
}

// 상세 사진칸 — 이 물건의 근거 사진(인증서 · 영수증 같은 문서)은 생성에 넣지 않고 따로 보여 준다 (10-09 사용자)
function renderDetailPhotos() {
  const wrap = document.getElementById('detail-wrap');
  const box = document.getElementById('detail-photos');
  box.innerHTML = '';
  const o = currentObject();
  const proofs = o && item && draft ? draft.objects.filter(x => x.kind === 'proof' && x.proof_for === o.id).map(x => x.id) : [];
  const url = new Map((item ? item.photos : []).map(p => [p.file_id, p.url]));
  const photos = draft ? draft.photos.filter(p => proofs.includes(p.object) && url.get(p.file_id)) : [];
  wrap.classList.toggle('hidden', !photos.length);
  for (const p of photos) {
    const img = document.createElement('img');
    img.src = url.get(p.file_id);
    img.alt = '';
    box.append(img);
  }
}

// AI 사진 검토 — 물건 · 정답이 바뀔 때만 부른다 (서버도 같은 조합이면 저장해 둔 걸 준다)
async function loadReview() {
  const o = currentObject();
  if (!item || !o || o.kind !== 'product') return;
  const key = `${item.item_id}|${o.id}|${refFile || ''}|${item.photos.length}`;
  if (key === reviewKey) return;
  reviewKey = key;
  try {
    const r = await fetchReview(item.item_id, o.id, refFile);
    if (key !== reviewKey) return;
    review = r;
    // 기준 사진 · 같이 넣을 사진도 AI 추천으로 — 판매자가 이미 직접 고른 건 바꾸지 않는다 (10-09 사용자: 규칙 각도 대신)
    // AI 가 추천을 비워 주면 "같이 넣지 않음"으로 받지 않는다 — 자동(같은 물건 다른 사진 2장)으로 둔다
    const aiWith = (r.with_ids || []).filter(id => id !== fileId);
    if (withIds === null && aiWith.length) withIds = aiWith;
    if (!basePicked && r.base_file_id && r.base_file_id !== fileId) {
      const p = item.photos.find(x => x.file_id === r.base_file_id);
      if (p) { selectPhoto(p); return; }
    }
    drawItem();
    drawObjects();
    renderContents();
  } catch (_) { /* 실패 — 규칙 라벨 · 경고 그대로 */ }
}

// 같은 물건의 다른 상품 사진 (근거 사진 제외) — 메인 포함 3장까지, 각도가 다른 것 먼저
function extraViewIds() {
  const o = currentObject();
  if (!o || o.kind !== 'product' || !draft) return null;
  const me = draft.photos.find(x => x.file_id === fileId);
  if (!me) return null;
  const rest = draft.photos.filter(x => x.object === o.id && x.file_id !== fileId);
  if (withIds !== null) {
    const ok = withIds.filter(id => draft.photos.some(x => x.file_id === id && x.object === o.id) && id !== fileId);
    if (withUser) return ok.length ? ok.slice(0, 2) : null;   // 판매자가 고른 그대로
    // AI 추천을 앞에 두고, 2장이 안 되면 같은 물건의 다른 사진으로 채운다 (기준 포함 3장을 다 쓰게)
    const fill = [...ok, ...rest.map(x => x.file_id).filter(id => !ok.includes(id))].slice(0, 2);
    return fill.length ? fill : null;
  }
  rest.sort((a, b) => (a.view === me.view) - (b.view === me.view));
  const ids = rest.slice(0, 2).map(x => x.file_id);
  return ids.length ? ids : null;
}

function canWith(p) {
  const o = currentObject();
  return !!(o && o.kind === 'product' && draft && draft.photos.some(x => x.file_id === p.file_id && x.object === o.id));
}

function toggleWith(p) {
  if (busy) return;
  const cur = extraViewIds() || [];
  withUser = true;
  if (cur.includes(p.file_id)) withIds = cur.filter(id => id !== p.file_id);
  else if (cur.length >= 2) { statusEl.textContent = '같이 넣을 사진은 2장까지예요 (기준 사진 포함 3장)'; return; }
  else withIds = [...cur, p.file_id];
  drawItem();
  loadSellObjects(fileId, true);   // 참고 사진에 찍힌 구성품까지 목록을 다시 (10-09)
}

// 원본 칸 — 생성에 들어가는 사진 전부(★대표 + 같이 넣기)와 대표사진에 담길 구성품을 한 줄에 하나씩 (10-09 사용자)
function renderContents() {
  const title = document.getElementById('before-title');
  const strip = document.getElementById('before-extras');
  const el = document.getElementById('photo-contents');
  const extras = extraViewIds() || [];
  const url = new Map((item ? item.photos : []).map(p => [p.file_id, p.url]));
  title.textContent = fileId ? `원본 — 생성에 들어가는 사진 ${1 + extras.length}장 (★기준 + 참고 ${extras.length}장)` : '원본';
  strip.innerHTML = '';
  strip.classList.toggle('hidden', !extras.length);
  for (const id of extras) {
    if (!url.get(id)) continue;
    const f = document.createElement('figure');
    const img = document.createElement('img');
    img.src = url.get(id);
    img.alt = '';
    const cap = document.createElement('figcaption');
    cap.textContent = '참고';
    f.append(img, cap);
    strip.append(f);
  }
  el.innerHTML = '';
  el.classList.toggle('hidden', !fileId);
  if (!fileId) return;
  const head = document.createElement('p');
  head.className = 'contents-title';
  head.textContent = '대표사진에 담길 것';
  el.append(head);
  const objs = (analysisObjects || []).filter(o => sell && sell.includes(o.index))
    .map(o => ({ ...o, role: mains.includes(o.index) ? 'main' : 'component' }));
  if (!objs.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = analyzing ? '분석 중…' : '분석이 끝나면 여기에 나와요';
    el.append(p);
    return;
  }
  const groups = new Map();
  for (const o of objs) {
    const k = kindOf(o);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(o);
  }
  const ul = document.createElement('ul');
  for (const [k, ms] of groups) {
    for (const [j, o] of ms.entries()) {
      const li = document.createElement('li');
      li.textContent = (k.startsWith('main:') ? '본품 · ' : '') + nameOf(o) + (ms.length > 1 ? ` ${j + 1}` : '')
        + ((o.photo || 0) !== 0 ? ` — 참고 사진 ${o.photo}` : '');   // textContent — VLM 이름
      ul.append(li);
    }
  }
  el.append(ul);
  if (contentsHidden) {
    // 안 보이는 건 지어내지 않는다 — 목록에도 대표사진에도 넣지 않고, 펼친 사진을 달라고 한다 (10-09 사용자)
    const w = document.createElement('p');
    w.className = 'contents-hidden';
    w.textContent = '⚠ 안의 구성품이 사진에 안 보여요 — 구성품을 펼쳐 찍은 사진을 기준이나 "같이 넣기"로 넣어 주세요';
    el.append(w);
  }
  const acc = (analysisObjects || []).filter(o => accessories.includes(o.index));
  if (acc.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = '부가품 (같이 팔지만 사진엔 안 나와요): ' + acc.map(nameOf).join(', ');
    el.append(p);
  }
}

// 묶음 화면의 물건 카드용 — "본품 레고 본체 · 미니피규어 1, 2, 3 · 설명서 1, 2 · 박스(부가품)" (10-09 사용자)
// 지금 분석한 사진이 그 물건의 사진일 때만 (구성품은 사진 한 장의 분석에서 나온다)
function componentSummary(objectId) {
  if (!analysisObjects || !analysisObjects.length || !draft) return '';
  const p = draft.photos.find(x => x.file_id === fileId);
  if (!p || p.object !== objectId) return '';
  const groups = new Map();
  for (const o of analysisObjects.filter(o => o.for_sale)) {
    const k = kindOf(o);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(o);
  }
  return [...groups].map(([k, ms]) => (k.startsWith('main:') ? '본품 ' : '') + nameOf(ms[0])
    + (ms.length > 1 ? ' ' + ms.map((_, j) => j + 1).join(', ') : '')
    + (ms[0].role === 'accessory' ? '(부가품)' : '')).join(' · ');
}

// 보이는 물건마다 네 가지 (10-09 사용자): 본품 · 구성품(대표사진에 같이) · 부가품(같이 팔지만 사진에선 뺌) · 안 팖.
// AI 추천이 기본으로 골라져 있다
const SELL_CHOICES = [['main', '본품'], ['component', '구성품'], ['accessory', '부가품'], ['none', '안 팔아요']];
let mains = [];                  // 본품으로 고른 번호 (sell 안)

function choiceOf(o) {
  if (mains.includes(o.index)) return 'main';
  if (sell.includes(o.index)) return 'component';
  return accessories.includes(o.index) ? 'accessory' : 'none';
}

function setChoice(i, key) {
  const drop = a => a.filter(x => x !== i);
  sell = drop(sell); accessories = drop(accessories); mains = drop(mains);
  if (key === 'main' || key === 'component') sell = [...sell, i].sort((x, y) => x - y);
  if (key === 'main') mains = [...mains, i].sort((x, y) => x - y);
  if (key === 'accessory') accessories = [...accessories, i].sort((x, y) => x - y);
}

// 보이는 물건을 한 줄에 하나씩 — "미니피규어 1 · 2 · 3"처럼 같은 종류는 번호로 (10-09 사용자)
function renderSell(objs) {
  const wrap = document.getElementById('sell-wrap');
  const list = document.getElementById('sell-objects');
  const boxes = document.getElementById('sell-boxes');
  list.innerHTML = '';
  boxes.innerHTML = '';
  wrap.classList.toggle('hidden', !objs);
  if (!objs) return;
  const byKind = new Map();
  for (const o of objs) {
    const k = nameOf(o);
    if (!byKind.has(k)) byKind.set(k, []);
    byKind.get(k).push(o);
  }
  const label = o => {
    const same = byKind.get(nameOf(o));
    return nameOf(o) + (same.length > 1 ? ` ${same.indexOf(o) + 1}` : '');
  };
  for (const o of objs) {
    const row = document.createElement('div');
    row.className = 'sell-obj';
    const name = document.createElement('span');
    name.textContent = label(o) + ((o.photo || 0) !== 0 ? ` (참고 사진 ${o.photo})` : '');   // textContent — VLM 이름
    const group = document.createElement('span');
    group.className = 'segmented sell-choices';
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', label(o));
    const cur = choiceOf(o);
    for (const [key, text] of SELL_CHOICES) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'sell-check';
      b.textContent = text;
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(cur === key));
      b.onclick = () => {
        const prev = [sell, accessories, mains].map(a => [...a]);
        setChoice(o.index, key);
        if (!mains.length) {           // 본품은 하나 이상 남긴다
          [sell, accessories, mains] = prev;
          statusEl.textContent = '본품을 하나 이상 골라 주세요';
          return;
        }
        sellDirty = true;
        renderSell(objs);
        renderRefs();
        renderContents();
      };
      group.append(b);
    }
    row.append(name, group);
    list.append(row);
    if ((o.photo || 0) !== 0) continue;   // 참고 사진에서 보인 건 기준 사진 위에 박스를 그리지 않는다
    const mark = document.createElement('div');
    mark.className = 'sell-box' + (cur === 'main' || cur === 'component' ? '' : cur === 'accessory' ? ' acc' : ' off');
    mark.style.left = (o.box.x1 / 10) + '%';
    mark.style.top = (o.box.y1 / 10) + '%';
    mark.style.width = ((o.box.x2 - o.box.x1) / 10) + '%';
    mark.style.height = ((o.box.y2 - o.box.y1) / 10) + '%';
    mark.textContent = label(o);
    boxes.append(mark);
  }
}

// ---- 사진 속 물건 (10-04): AI 가 묶은 걸 고쳐서 저장 ----
function draftFromItem(it) {
  draft = {
    objects: (it.objects || []).map(o => ({
      id: o.id, kind: o.kind, label: o.label, desc: o.desc || '', category: o.category,
      for_sale: o.for_sale, count: o.count || 1, proof_type: o.proof_type, proof_for: o.proof_for,
    })),
    photos: it.photos.map(p => ({ file_id: p.file_id, object: p.object || null, view: p.view || null, state: p.state || null })),
  };
  draftDirty = false;
}

function objOf(id) { return draft.objects.find(o => o.id === id); }

function changed() {
  draftDirty = true;
  objMsg = '';
  drawObjects();
  drawArrange();                 // 저장 전엔 배치를 막는다 (저장된 묶음 기준이라)
}

const objHandlers = {
  setRole(id, role) {
    const o = objOf(id);
    if (role === 'proof') {
      Object.assign(o, { kind: 'proof', for_sale: false, proof_type: o.proof_type || 'other', proof_for: null });
      for (const p of draft.photos) if (p.object === id) p.view = null;   // 근거 사진엔 각도가 없다
    } else {
      Object.assign(o, { kind: 'product', for_sale: role === 'sell', category: o.category || 'other',
                         count: o.count || 1, proof_type: null, proof_for: null });
    }
    for (const x of draft.objects) {                 // 가리키던 상품이 근거로 바뀌면 연결을 푼다
      if (x.kind === 'proof' && x.proof_for && objOf(x.proof_for)?.kind !== 'product') x.proof_for = null;
    }
    changed();
  },
  setField(id, key, value) { objOf(id)[key] = value; changed(); },
  setView(fid, view) { draft.photos.find(p => p.file_id === fid).view = view; changed(); },
  current() { return currentObject(); },   // 상품 설명 칸 — 지금 고른 상품 (10-09)
  shows(fid) {   // AI 사진 검토의 "보이는 것" (10-09)
    const x = review && review.photos.find(r => r.file_id === fid);
    return x ? x.shows : '';
  },
  move(fid, target) {
    const p = draft.photos.find(p => p.file_id === fid);
    const from = objOf(p.object);
    if (target === '__new') {
      let k = 1;
      while (objOf('o' + k)) k++;
      draft.objects.push({ id: 'o' + k, kind: from ? from.kind : 'product', label: '', desc: '', count: 1,
        category: from && from.kind === 'product' ? from.category : 'other', for_sale: from ? from.for_sale : true,
        proof_type: from && from.kind === 'proof' ? from.proof_type : null, proof_for: null });
      target = 'o' + k;
    }
    p.object = target || null;
    const to = objOf(p.object);
    if (!to || to.kind !== 'product') p.view = null;
    changed();
  },
};

function drawObjects() {
  renderObjects(item, draft, objHandlers, draftDirty);
  if (!item) return;
  objSaveBtn.disabled = busy || !needsSave();
  objSaveBtn.textContent = draftDirty ? '설명 저장' : '저장됨 ✓';
  objStatus.textContent = objMsg || (draftDirty ? '저장해야 반영돼요' : '');
}

objSaveBtn.onclick = async () => {
  if (!item || busy) return;
  const used = new Set(draft.photos.map(p => p.object).filter(Boolean));
  const body = {
    objects: draft.objects.filter(o => used.has(o.id)).map(o => ({
      ...o, proof_for: o.proof_for && used.has(o.proof_for) ? o.proof_for : null,
    })),
    photos: draft.photos,
  };
  const wasRunnable = canRun();
  setBusy(true);
  objMsg = '저장 중...';
  drawObjects();
  try {
    item = await updateObjects(item.item_id, body);
    draftFromItem(item);
    objMsg = '저장했어요';
  } catch (e) {
    objMsg = e.message;            // 서버 검증 메시지 그대로 — 무엇이 틀렸는지
  } finally {
    setBusy(false);
  }
  const p = item.photos.find(p => p.file_id === fileId);
  resetArrange(true);
  if (p && wasRunnable !== canRun()) selectPhoto(p);  // 변환 가능 여부가 바뀐 때만 — 결과 · 고른 구도는 둔다
  else {
    if (composition && !compsForPhoto(fileId).some(c => c.key === composition)) composition = compOfPhoto(fileId);
    drawItem();
    drawObjects();
    drawArrange();
  }
};

// 사진 목록 · 빠진 면 · 구도 · 대표컷 물건 — 고른 사진의 물건 기준
function drawItem() {
  const o = objectOfPhoto(fileId);
  document.getElementById('comp-title').textContent = o && sellingObjects(item).length > 1
    ? `예시 구도 고르기 — ${o.label}` : '예시 구도 고르기';
  // AI 사진 검토가 있으면 각도 라벨 · 빠진 면을 그걸로 (10-09 사용자: "위에서 본 사진" 같은 규칙 라벨이 애매)
  const rv = review && o && review.object_id === o.id ? review : null;
  const shows = new Map(rv ? rv.photos.map(x => [x.file_id, x.shows]) : []);
  const view = rv
    ? { ...item, photos: item.photos.map(p => shows.get(p.file_id) ? { ...p, view_label: shows.get(p.file_id), state_label: null } : p),
        missing: rv.missing.map(m => ({ label: m.what, hint: m.hint })) }
    : { ...item, missing: o && o.kind === 'product' ? o.missing : item.missing };
  renderItem(view, fileId, p => { basePicked = true; selectPhoto(p); }, extraViewIds() || [], canWith, toggleWith);
  renderComps({ compositions: compsForPhoto(fileId) }, composition, pickComp);
  renderTargets(item, o ? o.id : null, pickTarget);
}

// 변환할 수 있는 사진 — 파는 물건의 사진만 (근거 사진 · 안 파는 물건은 원본 그대로 둔다)
function photoRole(fid) {
  const p = item?.photos.find(p => p.file_id === fid);
  const o = p && (item.objects || []).find(o => o.id === p.object);
  return o ? objRole(o) : null;
}

function objectOfPhoto(fid) {
  const p = item?.photos.find(p => p.file_id === fid);
  return p ? (item.objects || []).find(o => o.id === p.object) || null : null;
}

// 구도 후보는 고른 사진의 물건 기준 (10-04) — 물건이 여럿이면 물건마다 구도가 다르다
function compsForPhoto(fid) {
  const o = objectOfPhoto(fid);
  return o && o.kind === 'product' ? (o.compositions || []) : [];
}

// 사진에 맞는 구도를 자동으로 — 종류가 기타면 하지 않는다 (기타의 구도는 시계 · 책 · 음반이라 상자 사진에 "시계" 문장이 붙는다)
function compOfPhoto(fid) {
  const o = objectOfPhoto(fid);
  if (!o || o.category === 'other') return null;
  const c = compsForPhoto(fid).find(c => c.photo_ids.includes(fid));
  return c ? c.key : null;
}

const chosenPhoto = {};          // 물건 id → 그 물건으로 고른 사진 (배치에도 이 사진을 쓴다)

function pickTarget(o) {
  if (busy) return;
  const c = (o.compositions || []).find(c => c.available);
  const fid = chosenPhoto[o.id] && o.photo_ids.includes(chosenPhoto[o.id]) ? chosenPhoto[o.id]
    : c ? c.photo_ids[0] : o.photo_ids[0];
  const p = item.photos.find(p => p.file_id === fid);
  if (p) selectPhoto(p, c && fid === c.photo_ids[0] && o.category !== 'other' ? c.key : undefined);
}

// ---- 여러 물건을 한 장에 (10-04) — 원본에서 오려 코드로 놓는다 ----
let arrangePicked = [];          // 놓을 물건 id (놓는 순서)
let arrangeLayout = 'row';
const arrangeBtn = document.getElementById('arrange-run');

const ARRANGE_MAX = 6;            // 서버 ArrangeRequest 와 같게

let arrangeSeen = new Set();     // 한 번 보여 준 물건 — 사용자가 뺀 건 다시 체크하지 않는다

// keep: 고른 것을 지키고 없어진 물건은 빼고, 새로 생긴 파는 물건은 더한다 (저장 · 더 올리기). 새 업로드면 앞에서부터 6개
function resetArrange(keep = false) {
  const ids = sellingObjects(item).map(o => o.id);
  if (!keep) arrangeSeen = new Set();
  const want = new Set(keep ? arrangePicked : []);
  for (const id of ids) if (!arrangeSeen.has(id)) want.add(id);
  arrangePicked = ids.filter(id => want.has(id)).slice(0, ARRANGE_MAX);
  arrangeSeen = new Set(ids);
}

function drawArrange() {
  renderArrange(item, arrangePicked, arrangeLayout, {
    toggle(id, on) {
      if (on && arrangePicked.length >= ARRANGE_MAX) {
        statusEl.textContent = `한 장에는 ${ARRANGE_MAX}개까지 놓을 수 있어요`;
        drawArrange();
        return;
      }
      const order = sellingObjects(item).map(o => o.id);      // 놓는 순서 = 목록 순서 (왼쪽부터)
      arrangePicked = order.filter(x => x === id ? on : arrangePicked.includes(x));
      drawArrange();
    },
    layout(key) { arrangeLayout = key; drawArrange(); },
  });
  arrangeBtn.disabled = busy || arrangePicked.length < 2 || draftDirty;
  for (const el of document.querySelectorAll('.arrange-check, .layout')) el.disabled = busy;
}

arrangeBtn.onclick = async () => {
  if (busy || !item || arrangePicked.length < 2) return;
  if (draftDirty) { statusEl.textContent = '고친 물건 묶음을 먼저 저장해 주세요'; return; }
  const picked = [...arrangePicked], layout = arrangeLayout;   // 요청 중에 바뀌어도 보낸 값으로 안내
  const photos = Object.fromEntries(picked.filter(id => chosenPhoto[id]).map(id => [id, chosenPhoto[id]]));
  setBusy(true);
  statusEl.textContent = '물건을 오려 한 장에 놓는 중...';
  try {
    const r = await arrangeObjects(item.item_id, picked, layout, photos);
    clearResults();
    document.getElementById('meta-chips').classList.add('hidden');   // 앞 변환의 물건 이름이 남지 않게
    beforeImg.hidden = true;                                          // 원본 한 장과 나란히 두면 그 사진의 결과처럼 보인다
    afterImg.onload = null;
    afterImg.src = r.result_url + '?t=' + Date.now();
    afterImg.hidden = false;
    const how = { row: '나란히', grid: picked.length === 2 ? '위아래로' : '격자로', overlap: '살짝 겹쳐서' }[layout];
    statusEl.textContent = `${picked.length}개를 ${how} 놓았어요 — 사진마다 원본에서 오려 그대로 놓았어요`;
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    setBusy(false);
    drawArrange();
  }
};

function pickComp(c) {
  if (busy) return;
  if (!c.available) {                    // 그 각도 사진이 없음 → 찍어 올리게
    statusEl.textContent = c.hint;
    addInput.click();
    return;
  }
  const p = item.photos.find(p => p.file_id === c.photo_ids[0]);
  selectPhoto(p, c.key);
}

function selectPhoto(p, comp) {
  if (busy) return;
  composition = comp !== undefined ? comp : compOfPhoto(p.file_id);
  fileId = p.file_id;
  if (withIds) withIds = withIds.filter(id => id !== fileId);   // 기준이 된 사진은 같이 넣기에서 뺀다
  if (p.object) chosenPhoto[p.object] = p.file_id;
  beforeImg.hidden = false;
  beforeImg.src = p.url;
  beforeImg.hidden = false;
  afterImg.hidden = true;
  clearResults();
  drawItem();
  drawObjects();
  drawArrange();
  renderRefs();   // 정답 구도 — 물건 종류에 맞는 것 (10-09)
  renderDetailPhotos();
  renderContents();
  const role = photoRole(p.file_id);
  if (role === 'proof' || role === 'keep') {          // 변환하지 않는 사진 — 분석도 안 한다 (setBusy 가 canRun 으로 지킨다)
    resetSell();
    runBtn.disabled = true;
    statusEl.textContent = role === 'proof'
      ? '근거 사진(설명서 · 회로 등)은 원본 그대로 올려요 — 변환하지 않아요'
      : '팔지 않는 물건으로 표시된 사진이에요 — 팔 물건이면 위에서 바꿔 주세요';
    return;
  }
  loadSellObjects(p.file_id);
  runBtn.disabled = !canRun();
  statusEl.textContent = '이 구도로 변환하려면 변환하기를 누르세요';
}

function firstGoodPhoto(it) {
  // 대표컷 구도에 맞는 사진이 있으면 그것부터 (구도 목록의 첫 번째가 대표컷)
  const first = (it.compositions || []).find(c => c.available);
  if (first) return it.photos.find(p => p.file_id === first.photo_ids[0]);
  const bad = new Set(it.retake.map(r => r.file_id));
  const mine = it.main_object ? it.photos.filter(p => p.object === it.main_object) : it.photos;   // 대표 물건 사진부터
  return mine.find(p => !bad.has(p.file_id)) || mine[0] || it.photos[0];
}

async function handleFiles(files, append = false, proof = null) {
  if (!append && proof === null) proof = pendingProof;   // 먼저 골라 둔 근거 사진은 새 물건과 같이
  files = files.filter(Boolean);
  if ((!files.length && !(proof && proof.length)) || busy) return;
  if (!append && draftDirty && !confirm('고친 물건 묶음을 저장하지 않았어요. 새 사진으로 시작할까요?')) return;
  const token = ++uploadToken;
  const hasVideo = files.some(f => f.type.startsWith('video/'));
  statusEl.textContent = hasVideo ? '동영상에서 장면 고르는 중... (조금 걸려요)' : '업로드하고 사진 확인 중...';
  if (!append) {
    fileId = null; item = null; composition = null; resetSell(); clearResults(); hideItem();
    afterImg.hidden = true; beforeImg.hidden = true;
  }
  urlInput.value = '';
  setBusy(true);
  try {
    const res = await uploadItem(files, append && item ? item.item_id : null, proof || []);
    if (token !== uploadToken) return;            // 그사이 다른 업로드가 시작됨
    if (!append) setPendingProof([]);
    item = res;
    review = null; reviewKey = '';   // 사진이 바뀌었으니 AI 검토도 다시
    if (!append) { withIds = null; withUser = false; basePicked = false; }   // 새 물건이면 같이 넣기 · 기준 사진도 AI 추천부터
    draftFromItem(item);
    objMsg = '';
    resetArrange(append);
    const keep = append && item.photos.find(p => p.file_id === fileId);
    setBusy(false);
    const keepComp = keep && compsForPhoto(keep.file_id).some(c => c.key === composition) ? composition : undefined;
    selectPhoto(keep || firstGoodPhoto(item), keepComp);
    if (item.missing.length || item.retake.length) {
      statusEl.textContent = '구도를 고르세요 — 빠진 면이나 다시 찍을 사진이 있으면 더 올려도 돼요';
    }
  } catch (e) {
    if (token === uploadToken) statusEl.textContent = e.message;
  } finally {
    if (token === uploadToken) setBusy(false);
    fileInput.value = '';
    addInput.value = '';
  }
}

addBtn.onclick = () => {
  if (busy) return;
  if (draftDirty) {                // 더 올리면 서버가 저장된 묶음 기준으로 다시 나눈다 — 고친 걸 먼저 저장
    statusEl.textContent = '고친 물건 묶음을 먼저 저장해 주세요';
    return;
  }
  addInput.click();
};
addInput.onchange = () => handleFiles([...addInput.files], true);

// ---- 업로드: URL ----
urlBtn.onclick = async () => {
  const url = urlInput.value.trim();
  if (!url || busy) return;
  ++uploadToken;                 // 진행 중이던 묶음 업로드 응답은 버린다
  urlBtn.disabled = true;
  statusEl.textContent = 'URL 다운로드 중...';
  try {
    const data = await uploadUrl(url);
    setPendingProof([]);         // 한 장 업로드는 물건 묶음이 아니라 근거를 붙일 곳이 없다
    fileId = data.file_id;
    item = null;
    composition = null;
    resetSell();                 // 이전 사진의 팔 물건 고르기를 남기지 않는다
    hideItem();
    beforeImg.src = url;
    beforeImg.hidden = false;
    afterImg.hidden = true;
    clearResults();
    fileInput.value = '';
    statusEl.textContent = '업로드 완료! 변환하기를 누르세요';
    runBtn.disabled = false;
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    urlBtn.disabled = false;
  }
};

// ---- 변환 ----
// 팔 물건이 여러 개면 한 장에 같이 / 물건마다 따로를 묻는다 (10-05) — 'together' | 'separate' | null(취소)
let cancelAsk = null;             // 묻는 중이면 질문을 닫는 함수 (사진을 바꾸면 취소)

function askMulti(n) {
  const box = document.getElementById('multi-choice');
  document.getElementById('multi-q').textContent = `팔 물건이 ${n}개예요. 어떻게 만들까요?`;
  box.classList.remove('hidden');
  return new Promise(resolve => {
    const done = v => { box.classList.add('hidden'); cancelAsk = null; resolve(v); };
    cancelAsk = () => done(null);
    document.getElementById('multi-together').onclick = () => done('together');
    document.getElementById('multi-separate').onclick = () => done('separate');
    document.getElementById('multi-cancel').onclick = () => done(null);
  });
}

function showResult(data) {
  afterImg.onload = null;
  afterImg.onerror = () => { statusEl.textContent = '결과 이미지를 불러오지 못했습니다'; };
  afterImg.src = data.result_url + '?t=' + Date.now();
  afterImg.hidden = false;
  renderMetaChips(data);
  renderGate(data);
}

// 물건별 결과 — 누르면 위 큰 화면에 (VLM 이 준 물건 이름은 textContent 로만)
function renderMultiResults(list) {
  const wrap = document.getElementById('multi-results');
  wrap.replaceChildren();
  wrap.classList.toggle('hidden', !list.length);
  list.forEach(({ label, data }, k) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'multi-result' + (k === 0 ? ' on' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', k === 0 ? 'true' : 'false');
    const img = document.createElement('img');
    img.src = data.result_url + '?t=' + Date.now();
    img.alt = label;
    const cap = document.createElement('span');
    cap.textContent = label;
    b.append(img, cap);
    b.onclick = () => {
      for (const x of wrap.children) { x.classList.remove('on'); x.setAttribute('aria-checked', 'false'); }
      b.classList.add('on');
      b.setAttribute('aria-checked', 'true');
      showResult(data);
    };
    wrap.append(b);
  });
}

async function runSeparate(fileId_, comp_, picked) {
  const names = new Map((analysisObjects || []).map(o => [o.index, o.what || `물건 ${o.index + 1}`]));
  const ok = [], failed = [];
  for (const [k, i] of picked.entries()) {
    statusEl.textContent = `물건마다 따로 만드는 중... (${k + 1}/${picked.length})`;
    const label = names.get(i) || `물건 ${i + 1}`;
    try {
      // 따로: 다른 사진은 다른 구성품이 섞여 넣지 않는다 (10-09 리뷰)
      ok.push({ label, data: await requestTransform(fileId_, PRESET, comp_, [i], true, null, null, refFile, null) });
    } catch (e) {
      failed.push(label);
    }
  }
  if (fileId_ !== fileId) return;            // 그사이 다른 사진으로 바뀌었다 — 새 사진 위에 그리지 않는다
  renderMultiResults(ok);
  if (ok.length) showResult(ok[0].data);
  // 피드백은 사진 하나 · 무드 하나 기준이라 물건별 결과에는 달지 않는다
  hideFeedbackBox();
  statusEl.textContent = failed.length ? `물건 ${picked.length}개 중 ${ok.length}개 완료 — 실패: ${failed.join(', ')}`
                                       : `완료! 🎉 물건 ${ok.length}개`;
}

runBtn.onclick = async () => {
  if (busy || !canRun()) return;
  const fileId_ = fileId;        // 변환 중 사진을 바꿔도 이 결과 · 피드백은 이 사진에
  const comp_ = composition;
  const sell_ = sellDirty ? sell : null;   // 기본 체크 그대로면 보내지 않는다 — 분석 판단대로
  const acc_ = sellDirty && accessories.length ? [...accessories] : null;
  const mains_ = sellDirty && mains.length ? [...mains] : null;
  const picked = sell ? [...sell] : [];
  let how = 'together';
  // 본품 하나의 구성품(피규어 · 설명서)이면 따로 만들지 묻지 않는다 — 본품이 둘 이상일 때만 (10-09)
  // role 이 없는 옛 분석이면 예전처럼 묻는다. 본품이 0개(VLM 이 본품을 구성품으로 봄)면 한 물건으로 본다
  const objs_ = analysisObjects || [];
  const hasRoles = objs_.some(o => o.role);
  const mainsPicked = mains.length ? mains.filter(i => picked.includes(i))
    : objs_.filter(o => picked.includes(o.index) && o.role === 'main').map(o => o.index);
  if (picked.length >= 2 && (!hasRoles || mainsPicked.length >= 2)) {
    setBusy(true);                 // 묻는 동안 사진 · 팔 물건 고르기 · 변환 버튼을 잠근다 (질문 버튼만 살아 있다)
    how = await askMulti(picked.length);
    setBusy(false);
    if (!how || fileId_ !== fileId) return;   // 취소했거나 그사이 사진이 바뀌었다
  }
  renderMultiResults([]);
  setBusy(true);
  if (how === 'separate') {
    try { await runSeparate(fileId_, comp_, picked); } finally { setBusy(false); }
    return;
  }
  statusEl.textContent = '변환 중... (몇 초 걸려요)';
  try {
    const data = await requestTransform(fileId_, PRESET, comp_, sell_, false, acc_, extraViewIds(), refFile, item && item.item_id, mains_);
    showResult(data);

    // 피드백: 우선 빈 박스 표시, 기존 피드백 있으면 채워넣기
    currentRating = 0;
    resetFeedbackBox();
    try {
      const fb = await fetchFeedback(fileId_, PRESET);
      if (fb && fb.source === 'user') {
        // source가 'agent'(합성 피드백)인 건 "내가 남긴 피드백"으로 보여주면 안 됨
        currentRating = fb.rating;
        renderFeedback(fb);
      }
    } catch (_) {
      // 피드백 조회 실패는 변환 결과 표시를 막지 않음
    }

    statusEl.textContent = '완료! 🎉';
  } catch (e) {
    statusEl.textContent = '변환 실패: ' + e.message;
  } finally {
    setBusy(false);
  }
};

// ---- 피드백 ----
feedbackStars.onclick = (e) => {
  const star = e.target.closest('.star');
  if (!star) return;
  currentRating = Number(star.dataset.value);
  setStars(currentRating);
};

feedbackSubmitBtn.onclick = async () => {
  if (!fileId) return;
  if (!currentRating) {
    feedbackStatus.textContent = '별점을 선택해주세요';
    return;
  }
  feedbackSubmitBtn.disabled = true;
  feedbackStatus.textContent = '전송 중...';
  try {
    await submitFeedback(fileId, PRESET, currentRating, feedbackComment.value.trim() || null);
    feedbackStatus.textContent = '피드백 감사합니다! 🙏';
  } catch (e) {
    feedbackStatus.textContent = e.message;
  } finally {
    feedbackSubmitBtn.disabled = false;
  }
};

// ===== 이미지 확대 (Zoom on Scroll) =====
function initZoom() {
  const viewport = document.getElementById('after-wrap');
  const canvas = document.getElementById('result-canvas');
  if (!viewport || !canvas) return;

  let scale = 1;
  const MIN = 1, MAX = 5, STEP = 0.3;

  viewport.addEventListener('wheel', (e) => {
    e.preventDefault(); // 페이지 스크롤 방지

    const rect = viewport.getBoundingClientRect();
    const cx = e.clientX - rect.left; // 뷰포트 기준 커서 X
    const cy = e.clientY - rect.top;  // 뷰포트 기준 커서 Y

    const prev = scale;
    scale = Math.min(MAX, Math.max(MIN,
            scale + (e.deltaY < 0 ? STEP : -STEP)));

    if (scale === 1) {
      // 1배면 원위치
      canvas.style.transform = '';
      viewport.classList.remove('zoomed');
      return;
    }

    viewport.classList.add('zoomed');

    // 🎯 커서가 화면에서 움직이지 않게 translate 보정
    // 공식: offset = cursor_pos - (cursor_pos / prev_scale) * new_scale
    const dx = cx - (cx / prev) * scale;
    const dy = cy - (cy / prev) * scale;

    canvas.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
  }, { passive: false });

  // 더블클릭으로 1배 복귀
  viewport.addEventListener('dblclick', () => {
    scale = 1;
    canvas.style.transform = '';
    viewport.classList.remove('zoomed');
  });

  // render.js 의 clearResults() 가 화면을 리셋할 때, 여기 내부 배율도 같이 되돌린다
  // (안 그러면 다음 스크롤 때 배율 계산이 실제 화면과 어긋남)
  resetZoomScale = () => { scale = 1; };
}

// #after-wrap / #result-canvas 는 초기 HTML에 이미 있으므로 이미지 로드를 기다릴 필요 없음
initZoom();