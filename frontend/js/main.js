/* 조율 계층 — 상태 + 이벤트. api 와 render 를 연결. */

const PRESET = 'studio_white';   // 프리셋 선택 UI는 없앰 — 항상 화이트 스튜디오로 변환

let fileId = null;
let qualityPoll = null;          // 성적표 폴링 (변환마다 하나만)
let qualityToken = 0;            // 폴링 세대 — 끊긴 폴링의 늦은 응답을 버리기 위함
const QUALITY_POLL_MAX = 40;     // 3초 × 40 = 2분 — judge 가 실패하면 파일이 안 생긴다

function stopQualityPoll() {
  clearInterval(qualityPoll);
  qualityPoll = null;
  qualityToken++;                // 이미 날아간 fetch 응답도 무효화
}

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
  for (const el of [dropzone, addBtn, urlBtn]) {
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
  return !!item && (draftDirty || !item.user_edited || !!item.needs_review);
}

let composition = null;          // 고른 정석 구도 키 (신발만 — 없으면 null)
let sell = null;                 // 고른 팔 물건 번호 (analyze objects) — 목록이 없으면 null (분석 판단대로)
let sellDirty = false;           // 사용자가 기본 체크를 바꿨나 — 안 바꿨으면 서버에 보내지 않는다 (분석 판단대로)
let analyzing = false;           // 미리 분석 중 — 끝나기 전에 변환하면 서버가 같은 사진을 두 번 분석한다
let analyzeToken = 0;            // 사진을 바꾸면 늦게 온 분석 응답을 버린다

function resetSell() {
  ++analyzeToken;
  sell = null;
  sellDirty = false;
  analyzing = false;
  renderSell(null);
}

async function loadSellObjects(fid) {
  resetSell();
  const token = analyzeToken;
  analyzing = true;
  runBtn.disabled = true;
  try {
    const a = await analyzePhoto(fid);
    if (token !== analyzeToken || fid !== fileId) return;
    // 생성 대신 배경만 바꾸는 사진 — 이유와 어떻게 하면 정리까지 되는지 알려준다 (10-03)
    const NOTE = {
      cut_off: '물건이 사진에 다 안 나와서 배경만 바꿔요 — 물건 전체가 나오게 다시 찍으면 정리까지 해 드려요',
      multi_item: '물건이 여러 개라 배경만 바꿔요 (개수가 바뀌지 않게) — 한 개만 팔면 아래에서 하나만 고르세요',
    };
    if (NOTE[a.reason] && !busy) statusEl.textContent = NOTE[a.reason];
    const objs = a.objects || [];
    if (objs.length < 2) return;            // 하나뿐이면 고를 게 없다
    sell = objs.filter(o => o.for_sale).map(o => o.index);
    if (!sell.length) sell = objs.map(o => o.index);
    renderSell(objs);
  } catch (_) { /* 분석 실패 — 고르기 없이 변환 (서버가 판단) */ }
  finally {
    if (token === analyzeToken) {
      analyzing = false;
      runBtn.disabled = busy || !canRun();
    }
  }
}

function renderSell(objs) {
  const wrap = document.getElementById('sell-wrap');
  const list = document.getElementById('sell-objects');
  const boxes = document.getElementById('sell-boxes');
  list.innerHTML = '';
  boxes.innerHTML = '';
  wrap.classList.toggle('hidden', !objs);
  if (!objs) return;
  for (const o of objs) {
    const label = document.createElement('label');
    label.className = 'sell-obj';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'sell-check';
    box.checked = sell.includes(o.index);
    const mark = document.createElement('div');
    box.onchange = () => {
      const next = box.checked ? [...sell, o.index] : sell.filter(i => i !== o.index);
      if (!next.length) { box.checked = true; return; }   // 하나 이상은 남긴다
      sell = next.sort((a, b) => a - b);
      sellDirty = true;
      mark.classList.toggle('off', !box.checked);
    };
    label.append(box, document.createTextNode(` ${o.index + 1}. ${o.what}`));   // textContent — 이름은 VLM 답
    list.append(label);
    mark.className = 'sell-box' + (box.checked ? '' : ' off');
    mark.style.left = (o.box.x1 / 10) + '%';
    mark.style.top = (o.box.y1 / 10) + '%';
    mark.style.width = ((o.box.x2 - o.box.x1) / 10) + '%';
    mark.style.height = ((o.box.y2 - o.box.y1) / 10) + '%';
    mark.textContent = String(o.index + 1);
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
  move(fid, target) {
    const p = draft.photos.find(p => p.file_id === fid);
    const from = objOf(p.object);
    if (target === '__new') {
      let k = 1;
      while (objOf('o' + k)) k++;
      draft.objects.push({ id: 'o' + k, kind: from ? from.kind : 'product', label: '새 물건', desc: '', count: 1,
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
  objSaveBtn.textContent = draftDirty ? '고친 내용 저장'
    : item.needs_review ? '새로 올린 사진도 맞아요' : (item.user_edited ? '확인됨 ✓' : '이대로 맞아요');
  objStatus.textContent = objMsg || (draftDirty ? '저장해야 반영돼요'
    : item.needs_review ? '더 올린 사진을 AI 가 나눴어요 — 확인해 주세요' : '');
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
  renderItem({ ...item, missing: o && o.kind === 'product' ? o.missing : item.missing }, fileId, p => selectPhoto(p));
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
    stopQualityPoll();
    overlay.innerHTML = '';
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
  if (p.object) chosenPhoto[p.object] = p.file_id;
  beforeImg.hidden = false;
  beforeImg.src = p.url;
  beforeImg.hidden = false;
  afterImg.hidden = true;
  clearResults();
  drawItem();
  drawObjects();
  drawArrange();
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

async function handleFiles(files, append = false) {
  files = files.filter(Boolean);
  if (!files.length || busy) return;
  if (!append && draftDirty && !confirm('고친 물건 묶음을 저장하지 않았어요. 새 사진으로 시작할까요?')) return;
  const token = ++uploadToken;
  const hasVideo = files.some(f => f.type.startsWith('video/'));
  statusEl.textContent = hasVideo ? '동영상에서 장면 고르는 중... (조금 걸려요)' : '업로드하고 각도 확인 중...';
  if (!append) {
    fileId = null; item = null; composition = null; resetSell(); clearResults(); hideItem();
    afterImg.hidden = true; beforeImg.hidden = true;
  }
  urlInput.value = '';
  setBusy(true);
  try {
    const res = await uploadItem(files, append && item ? item.item_id : null);
    if (token !== uploadToken) return;            // 그사이 다른 업로드가 시작됨
    item = res;
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
runBtn.onclick = async () => {
  if (busy || !canRun()) return;
  const fileId_ = fileId;        // 변환 중 사진을 바꿔도 이 결과 · 성적표 · 피드백은 이 사진에
  const comp_ = composition;
  const sell_ = sellDirty ? sell : null;   // 기본 체크 그대로면 보내지 않는다 — 분석 판단대로
  setBusy(true);
  statusEl.textContent = '변환 중... (몇 초 걸려요)';
  try {
    const data = await requestTransform(fileId_, PRESET, comp_, sell_);

    // 이전 결과의 말풍선이 새 이미지 로드 전까지 잘못 남아있지 않도록 즉시 비움
    overlay.innerHTML = '';

    // 말풍선은 새 이미지가 실제로 로드된 뒤에 그려야 크기 계산(naturalWidth 등)이 맞음
    afterImg.onload = () => renderBubbles(data.bubbles || []);
    afterImg.onerror = () => { statusEl.textContent = '결과 이미지를 불러오지 못했습니다'; };
    afterImg.src = data.result_url + '?t=' + Date.now();
    afterImg.hidden = false;

    renderMetaChips(data);
    renderGate(data);

    // 성적표는 응답 뒤 백그라운드에서 채점 → judge_pending 일 때만 폴링.
    // 이전 변환의 폴링은 먼저 끊고(겹쳐 쌓임 방지), 횟수 상한을 둔다 (judge 실패 시 404 무한 폴링 방지)
    renderQuality(data.quality ?? null);
    stopQualityPoll();
    if (!data.quality && data.judge_pending) {
      const qUrl = `/api/quality/${fileId_}/${PRESET}`;
      const token = qualityToken;
      let tries = 0;
      qualityPoll = setInterval(async () => {
        if (++tries > QUALITY_POLL_MAX) { stopQualityPoll(); return; }
        try {
          const r = await fetch(qUrl + '?t=' + Date.now());
          if (token !== qualityToken) return;          // 새 업로드/변환으로 끊긴 폴링
          if (r.ok) {
            const q = await r.json();
            if (token !== qualityToken) return;
            renderQuality(q);
            stopQualityPoll();
          }
        } catch (_) { /* 네트워크 오류 — 다음 틱에 다시 */ }
      }, 3000);
    }

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

    statusEl.textContent = data.judge_pending ? '완료! 🎉 (성적표 채점 중…)' : '완료! 🎉';
  } catch (e) {
    stopQualityPoll();
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