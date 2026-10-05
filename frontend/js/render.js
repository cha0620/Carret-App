/* 렌더링 계층 — 화면만 그림. 서버 모름. */

const gateBadge = document.getElementById('gate-badge');

const feedbackBox     = document.getElementById('feedback-box');
const feedbackStars   = document.getElementById('feedback-stars');
const feedbackComment = document.getElementById('feedback-comment');
const feedbackStatus  = document.getElementById('feedback-status');

// res = transform 응답. 무엇을 보여주는지(원본/합성/생성)에 따라 문구가 달라야 정직하다.
function renderGate(res) {
  const show = (text, color) => {
    gateBadge.hidden = false;
    gateBadge.textContent = text;
    gateBadge.style.color = color;
  };
  if (res.mode === 'original') {
    if (res.composite_reason === 'document') {
      return show('ℹ️ 인쇄된 글자·그림이 곧 상품이라 바뀌지 않게 원본 사진을 그대로 두었어요', '#5d6d7e');
    }
    if (res.composite_reason === 'wear_heavy') {
      return show('ℹ️ 사용감이 많은 물건이라 상태를 그대로 보여드리려고 원본 사진을 그대로 두었어요', '#5d6d7e');
    }
    return res.composite_reason === 'inside_view'
      ? show('ℹ️ 물건 일부·내부를 찍은 사진이라 배경을 바꾸지 않고 원본을 그대로 보여드려요', '#5d6d7e')
      : show('ℹ️ 배경을 바꾸지 않고 원본 사진을 그대로 보여드려요', '#5d6d7e');
  }
  // 검사 자체를 못 함(원본 분석 실패 / 결과 보존 검사 호출 실패) — 통과도 실패도 아님
  const unchecked = res.detect_failed || res.verify_failed;
  if (res.mode === 'composite') {
    return unchecked
      ? show(res.detect_failed
          ? '⚠️ 원본 물건 사진에 배경만 바꿨어요 (사진 분석은 하지 못했어요)'
          : '⚠️ 원본 물건 사진에 배경만 바꿨어요 (결과 검사는 하지 못했어요)', '#b9770e')
      : show(res.composite_reason === 'document'
          ? '🛡️ 인쇄된 글자·그림이 곧 상품이라 원본 물건 사진에 배경만 바꿨어요'
          : '🛡️ 하자·글자를 지키려고 원본 물건 사진에 배경만 바꿨어요', '#2a7f2a');
  }
  if (unchecked) {
    return show('⚠️ 보존 검사를 하지 못했습니다 — 생성 이미지에서 로고·글자가 바뀌었을 수 있어요', '#c0392b');
  }
  // 생성본: 로고·주요 글자만 검사한다. 흠집·얼룩은 자동으로 검사하지 않는다 — 과장하지 않게 같이 알린다
  const wearNote = ' · 흠집·얼룩은 자동 검사하지 않아요, 원본 사진으로 확인해 주세요';
  const passed = res.gate_passed;
  if (passed === null || passed === undefined) {
    return show('ℹ️ AI가 다시 그린 사진이에요' + wearNote, '#5d6d7e');
  }
  gateBadge.hidden = false;
  gateBadge.textContent = (passed
    ? '🛡️ 주요 로고·글자 보존됨'
    : '⚠️ 일부 로고·글자가 달라졌을 수 있음') + wearNote;
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
  gateBadge.hidden = true;
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


// ===== 여러 각도: 사진 목록 · 빠진 면 =====
function renderComps(item, selectedKey, onPick) {
  const wrap = document.getElementById('comp-wrap');
  const box = document.getElementById('item-comps');
  wrap.classList.toggle('hidden', !item.compositions.length);
  box.innerHTML = '';
  for (const c of item.compositions) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'comp' + (c.key === selectedKey ? ' selected' : '') + (c.available ? '' : ' missing');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', c.key === selectedKey ? 'true' : 'false');
    const img = document.createElement('img');
    img.src = c.image;
    img.alt = '';
    const name = document.createElement('span');
    name.className = 'comp-label';
    name.textContent = c.label;
    const desc = document.createElement('span');
    desc.className = 'comp-desc';
    desc.textContent = c.available ? c.desc : c.hint;
    b.append(img, name, desc);
    b.onclick = () => onPick(c);
    box.append(b);
  }
}

function renderItem(item, selectedId, onSelect) {
  const box = document.getElementById('item-box');
  const list = document.getElementById('item-photos');
  const miss = document.getElementById('item-missing');
  const summary = document.getElementById('item-summary');
  box.classList.remove('hidden');
  const retake = new Map(item.retake.map(r => [r.file_id, r.reason]));

  summary.textContent = item.views_failed
    ? `사진 ${item.photos.length}장 — 각도를 확인하지 못했어요`
    : `${((item.objects || []).find(o => o.id === item.main_object) || {}).label || item.item} · 사진 ${item.photos.length}장`
      + (item.complete ? ' — 필요한 면이 다 있어요 👍' : '');

  list.innerHTML = '';
  for (const p of item.photos) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'item-photo' + (p.file_id === selectedId ? ' selected' : '') + (retake.has(p.file_id) ? ' warn' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', p.file_id === selectedId ? 'true' : 'false');
    const img = document.createElement('img');
    img.src = p.url;
    img.alt = p.view_label || '사진';
    const tag = document.createElement('span');
    tag.className = 'item-view';
    const o = (item.objects || []).find(o => o.id === p.object);
    const who = o ? (o.kind === 'proof' ? '근거' : (o.for_sale ? '' : '안 팔아요')) : '';
    tag.textContent = (o && o.kind === 'proof' ? (o.label || '근거 사진') : (p.view_label || '각도 모름') + (p.state_label ? ' · ' + p.state_label : ''))
      + (who && o.kind !== 'proof' ? ' · ' + who : '') + (p.source === 'video' ? ' · 🎞' : '');
    b.append(img, tag);
    if (retake.has(p.file_id)) {
      const w = document.createElement('span');
      w.className = 'item-retake';
      w.textContent = '⚠ ' + retake.get(p.file_id);
      b.append(w);
    }
    b.onclick = () => onSelect(p);
    list.append(b);
  }

  miss.innerHTML = '';
  miss.classList.toggle('hidden', !item.missing.length);
  if (item.missing.length) {
    const t = document.createElement('p');
    t.className = 'item-missing-title';
    t.textContent = '이 면이 없어요 — 더 찍어 올리면 구매자가 믿고 사요';
    const ul = document.createElement('ul');
    for (const m of item.missing) {
      const li = document.createElement('li');
      li.textContent = `${m.label}: ${m.hint}`;
      ul.append(li);
    }
    miss.append(t, ul);
  }
}

function hideItem() {
  document.getElementById('item-box').classList.add('hidden');
}


// ===== 사진 속 물건 (10-04): AI 가 묶은 물건 · 설명을 보여 주고 사용자가 고친다 =====
const OBJ_VIEWS = {   // services/coverage.py VIEWS 와 같게
  front: '정면', front_34: '앞쪽 비스듬히', side: '옆면', back: '뒷면', rear_34: '뒤쪽 비스듬히',
  top: '위에서', bottom: '바닥 · 밑창', inside: '안쪽', label: '라벨 · 택', detail: '가까이 (하자 · 디테일)',
};
const OBJ_CATEGORIES = { shoes: '신발', clothing: '옷', bag: '가방', electronics: '전자기기', vehicle: '차량', watch: '시계', media: '책 · 게임 · 음반', pack: '세트 (레고 · 보드게임)', other: '기타' };
const OBJ_PROOF_TYPES = { document: '설명서 · 보증서 · 영수증', mark: '정품 마크 · 시리얼', internals: '내부 · 회로', screen: '작동 · 상태 화면', other: '그 밖의 근거' };
const OBJ_ROLES = { sell: '팔 물건', keep: '같이 찍힌 것 (안 팔아요)', proof: '근거 사진 (상태 · 정품 보증)' };

function objRole(o) {
  return o.kind === 'proof' ? 'proof' : (o.for_sale ? 'sell' : 'keep');
}

function _select(options, value, onChange, cls) {
  const s = document.createElement('select');
  if (cls) s.className = cls;
  for (const [v, label] of options) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = label;
    s.append(opt);
  }
  s.value = value;
  s.onchange = () => onChange(s.value);
  return s;
}

// item: 서버 응답(사진 url · 빠진 면), draft: 고치는 중인 {objects, photos}, h: 고칠 때 부르는 함수들
function renderObjects(item, draft, h, dirty) {
  const wrap = document.getElementById('obj-wrap');
  const list = document.getElementById('obj-list');
  // 묶기에 실패해도 이미 나눈 물건이 있으면 고칠 수 있게 둔다
  wrap.classList.toggle('hidden', !item || !draft.photos.length || (item.views_failed && !draft.objects.length));
  list.innerHTML = '';
  if (!item) return;
  const url = new Map(item.photos.map(p => [p.file_id, p.url]));
  const saved = new Map((item.objects || []).map(o => [o.id, o]));
  const used = draft.objects.filter(o => draft.photos.some(p => p.object === o.id));
  const moveOpts = [...used.map(o => [o.id, '→ ' + (o.label || o.id)]), ['__new', '→ 새 물건으로'], ['', '→ 어느 것도 아님']];

  const photoTile = (p, kind) => {
    const t = document.createElement('div');
    t.className = 'obj-photo';
    const img = document.createElement('img');
    img.src = url.get(p.file_id);
    img.alt = '';
    t.append(img);
    if (kind === 'product') {
      t.append(_select([['', '각도 모름'], ...Object.entries(OBJ_VIEWS)], p.view || '',
        v => h.setView(p.file_id, v || null), 'obj-view'));
    }
    t.append(_select(moveOpts, p.object || '', v => h.move(p.file_id, v), 'obj-move'));
    return t;
  };

  for (const o of used) {
    const card = document.createElement('div');
    card.className = 'obj-card obj-' + objRole(o);
    const head = document.createElement('div');
    head.className = 'obj-head';
    head.append(_select(Object.entries(OBJ_ROLES), objRole(o), v => h.setRole(o.id, v), 'obj-role'));
    const label = document.createElement('input');
    label.className = 'obj-label';
    label.maxLength = 40;
    label.value = o.label;          // value — 이름은 VLM 답이라 HTML 로 넣지 않는다
    label.placeholder = '이름';
    label.onchange = () => h.setField(o.id, 'label', label.value.trim());
    head.append(label);
    if (o.kind === 'product') {
      head.append(_select(Object.entries(OBJ_CATEGORIES), o.category || 'other', v => h.setField(o.id, 'category', v), 'obj-cat'));
      const count = document.createElement('input');
      count.type = 'number';
      count.min = 1;
      count.max = 99;
      count.className = 'obj-count';
      count.title = '같은 물건 몇 개';
      count.value = o.count || 1;
      count.onchange = () => {
        const n = Math.min(99, Math.max(1, parseInt(count.value, 10) || 1));
        h.setField(o.id, 'count', n);
      };
      const unit = document.createElement('span');
      unit.className = 'obj-unit';
      unit.textContent = '개';
      head.append(count, unit);
    } else {
      head.append(_select(Object.entries(OBJ_PROOF_TYPES), o.proof_type || 'other', v => h.setField(o.id, 'proof_type', v), 'obj-cat'));
      const products = used.filter(x => x.kind === 'product');
      const link = products.some(x => x.id === o.proof_for) ? o.proof_for : '';
      head.append(_select([['', '어느 물건의 근거인지 모름'], ...products.map(x => [x.id, (x.label || x.id) + ' 의 근거'])],
        link, v => h.setField(o.id, 'proof_for', v || null), 'obj-cat'));
    }
    const desc = document.createElement('input');
    desc.className = 'obj-desc';
    desc.maxLength = 200;
    desc.value = o.desc || '';
    desc.placeholder = '설명 (색 · 소재 · 상태)';
    desc.onchange = () => h.setField(o.id, 'desc', desc.value.trim());
    const photos = document.createElement('div');
    photos.className = 'obj-photos';
    for (const p of draft.photos.filter(p => p.object === o.id)) photos.append(photoTile(p, o.kind));
    card.append(head, desc, photos);
    const s = saved.get(o.id);
    if (!dirty && o.kind === 'product' && o.for_sale && s && s.missing && s.missing.length) {   // 저장 전엔 옛 값이라 숨긴다
      const m = document.createElement('p');
      m.className = 'obj-missing';
      m.textContent = '빠진 면: ' + s.missing.map(x => x.label).join(' · ');
      card.append(m);
    }
    list.append(card);
  }

  const loose = draft.photos.filter(p => !p.object);
  if (loose.length) {
    const card = document.createElement('div');
    card.className = 'obj-card obj-loose';
    const t = document.createElement('p');
    t.className = 'obj-loose-title';
    t.textContent = '어느 물건인지 모르는 사진';
    const photos = document.createElement('div');
    photos.className = 'obj-photos';
    for (const p of loose) photos.append(photoTile(p, null));
    card.append(t, photos);
    list.append(card);
  }
}


// ===== 대표컷 만들 물건 · 여러 물건 배치 (10-04) =====
const LAYOUTS = { row: '나란히', grid: '격자', overlap: '살짝 겹쳐서' };

function sellingObjects(item) {
  return (item?.objects || []).filter(o => o.kind === 'product' && o.for_sale && o.photo_ids.length);
}

function renderTargets(item, selectedId, onPick) {
  const wrap = document.getElementById('target-wrap');
  const box = document.getElementById('targets');
  const objs = sellingObjects(item);
  wrap.classList.toggle('hidden', objs.length < 2);   // 파는 물건이 하나면 고를 게 없다
  box.innerHTML = '';
  for (const o of objs) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'target' + (o.id === selectedId ? ' selected' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', o.id === selectedId ? 'true' : 'false');
    b.textContent = o.label + (o.count > 1 ? ` ×${o.count}` : '');
    b.onclick = () => onPick(o);
    box.append(b);
  }
}

function renderArrange(item, picked, layout, h) {
  const wrap = document.getElementById('arrange-wrap');
  const objs = sellingObjects(item);
  wrap.classList.toggle('hidden', objs.length < 2);
  const list = document.getElementById('arrange-objs');
  const lays = document.getElementById('arrange-layouts');
  list.innerHTML = '';
  lays.innerHTML = '';
  for (const o of objs) {
    const label = document.createElement('label');
    label.className = 'sell-obj';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'arrange-check';
    box.checked = picked.includes(o.id);
    box.onchange = () => h.toggle(o.id, box.checked);
    const pos = picked.indexOf(o.id);
    label.append(box, document.createTextNode(' ' + (pos >= 0 ? `${pos + 1}. ` : '') + o.label));   // 놓는 순서 (왼쪽부터)
    list.append(label);
  }
  for (const [key, name] of Object.entries(LAYOUTS)) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'layout layout-' + key + (key === layout ? ' selected' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', key === layout ? 'true' : 'false');
    const pic = document.createElement('span');
    pic.className = 'layout-pic';
    for (let i = 0; i < 3; i++) pic.append(document.createElement('i'));
    const t = document.createElement('span');
    t.textContent = name;
    b.append(pic, t);
    b.onclick = () => h.layout(key);
    lays.append(b);
  }
  document.getElementById('arrange-run').disabled = picked.length < 2;
}
