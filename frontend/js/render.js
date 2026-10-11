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
  if (res.mode === 'original') {   // 배경 교체(오리기)마저 실패했을 때만 (10-11: 생성 전 판단은 지웠다)
    return show('ℹ️ 배경을 바꾸지 못해 원본 사진을 그대로 보여드려요', '#5d6d7e');
  }
  // 검사 자체를 못 함(원본 분석 실패 / 결과 보존 검사 호출 실패) — 통과도 실패도 아님
  const unchecked = res.detect_failed || res.verify_failed;
  if (res.mode === 'composite') {
    return unchecked
      ? show(res.detect_failed
          ? '⚠️ 원본 물건 사진에 배경만 바꿨어요 (사진 분석은 하지 못했어요)'
          : '⚠️ 원본 물건 사진에 배경만 바꿨어요 (결과 검사는 하지 못했어요)', '#b9770e')
      : show('🛡️ 원본 물건 사진에 배경만 바꿨어요', '#2a7f2a');
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

// 피드백 태그 (10-11) — "원본에 이 구도 사진이 없음"은 모델이 아니라 사진 탓으로 따로 모은다
function setFeedbackTags(tags) {
  for (const b of document.querySelectorAll('#feedback-tags [data-tag]')) b.classList.toggle('on', (tags || []).includes(b.dataset.tag));
}
function feedbackTags() {
  return [...document.querySelectorAll('#feedback-tags .on')].map(b => b.dataset.tag);
}
document.querySelectorAll('#feedback-tags [data-tag]').forEach(b => b.onclick = () => b.classList.toggle('on'));

function resetFeedbackBox() {
  feedbackBox.classList.remove('hidden');
  setFeedbackTags([]);
  setStars(0);
  feedbackComment.value = '';
  feedbackStatus.textContent = '';
}

function renderFeedback(fb) {
  feedbackBox.classList.remove('hidden');
  setStars(fb.rating);
  setFeedbackTags(fb.tags);
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
// withIds: 대표와 같이 생성에 넣는 사진 · canWith(p): 같이 넣을 수 있는 사진인가 · onWith(p): 같이 넣기 켜고 끄기 (10-09)
function renderItem(item, selectedId, onSelect, withIds = [], canWith = () => false, onWith = null) {
  const box = document.getElementById('item-box');
  const list = document.getElementById('item-photos');
  const miss = document.getElementById('item-missing');
  const summary = document.getElementById('item-summary');
  box.classList.remove('hidden');
  const retake = new Map(item.retake.map(r => [r.file_id, r.reason]));

  summary.textContent = item.views_failed
    ? `사진 ${item.photos.length}장 — 사진을 묶지 못했어요`
    : `${((item.objects || []).find(o => o.id === item.main_object) || {}).label || item.item} · 사진 ${item.photos.length}장`
      ;

  list.innerHTML = '';
  for (const p of item.photos) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'item-photo' + (p.file_id === selectedId ? ' selected' : '') + (retake.has(p.file_id) ? ' warn' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', p.file_id === selectedId ? 'true' : 'false');
    const img = document.createElement('img');
    img.src = p.url;
    img.alt = p.view_label || '상품 사진';
    const tag = document.createElement('span');
    tag.className = 'item-view';
    const o = (item.objects || []).find(o => o.id === p.object);
    const who = o ? (o.kind === 'proof' ? '근거' : (o.for_sale ? '' : '안 팔아요')) : '';
    // 각도 대신 AI 가 본 "보이는 것" (view_label 자리) — 아직 없으면 비워 둔다 (10-09 사용자: 각도 내용은 지우기)
    tag.textContent = (o && o.kind === 'proof' ? (o.label || '근거 사진') : (p.view_label || ''))
      + (who && o.kind !== 'proof' ? ' · ' + who : '') + (p.source === 'video' ? ' · 🎞' : '');
    b.append(img, tag);
    if (retake.has(p.file_id)) {
      const w = document.createElement('span');
      w.className = 'item-retake';
      w.textContent = '⚠ ' + retake.get(p.file_id);
      b.append(w);
    }
    b.onclick = () => onSelect(p);
    if (p.file_id === selectedId) {
      const star = document.createElement('span');
      star.className = 'item-role main';
      star.textContent = '★ 기준';
      b.append(star);
    } else if (onWith && canWith(p)) {
      // 같이 넣기 — 대표와 같이 생성에 들어간다 (최대 2장). 버튼 안의 버튼이 안 되니 span + 클릭 가로채기
      const w = document.createElement('span');
      const on = withIds.includes(p.file_id);
      w.className = 'item-role' + (on ? ' with' : '');
      w.textContent = on ? '✓ 같이 넣기' : '＋ 같이 넣기';
      w.setAttribute('role', 'checkbox');
      w.setAttribute('aria-checked', String(on));
      w.tabIndex = 0;
      const toggle = e => { e.stopPropagation(); e.preventDefault(); onWith(p); };
      w.onclick = toggle;
      w.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') toggle(e); };
      b.append(w);
    }
    list.append(b);
  }

  miss.innerHTML = '';
  miss.classList.toggle('hidden', !item.missing.length);
  if (item.missing.length) {
    const t = document.createElement('p');
    t.className = 'item-missing-title';
    t.textContent = item.missing_title || '더 올리면 좋은 사진 — 구매자가 믿고 사요';
    const ul = document.createElement('ul');
    for (const m of item.missing) {
      const li = document.createElement('li');
      li.textContent = `${m.label}: ${m.hint}`;
      ul.append(li);
    }
    // 요청을 보고 바로 올리게 — 아래 "사진 · 동영상 더 올리기" 와 같은 동작 (묶음을 고쳤으면 저장 먼저 안내)
    const up = document.createElement('button');
    up.type = 'button';
    up.className = 'btn btn-ghost item-missing-upload';
    up.textContent = '사진 더 올리기';
    up.onclick = () => document.getElementById('item-add').click();
    miss.append(t, ul, up);
  }
}

function hideItem() {
  document.getElementById('item-box').classList.add('hidden');
}


// ===== 사진 속 물건 (10-04): AI 가 묶은 물건 · 설명을 보여 주고 사용자가 고친다 =====
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
// 상품 설명 (10-09 사용자: "사진 속 물건 확인"은 지우고 상품 설명만) — 지금 고른 상품의 이름 · 설명만 고친다.
// 사진 묶기 · 각도 · 근거 지정 같은 칸은 없앴다 (AI 판단 · 구성품 고르기가 대신한다)
function renderObjects(item, draft, h, dirty) {
  const wrap = document.getElementById('obj-wrap');
  const list = document.getElementById('obj-list');
  list.innerHTML = '';
  const o = item && h.current ? h.current() : null;
  wrap.classList.toggle('hidden', !o || o.kind !== 'product');
  if (!o || o.kind !== 'product') return;
  const label = document.createElement('input');
  label.className = 'obj-label';
  label.maxLength = 40;
  label.value = o.label || '';          // value — 이름은 VLM 답이라 HTML 로 넣지 않는다
  label.placeholder = '상품 이름 (예: 레고 키마 스콜피온 세트)';
  label.onchange = () => h.setField(o.id, 'label', label.value.trim());
  const desc = document.createElement('textarea');
  desc.className = 'obj-desc';
  desc.maxLength = 200;
  desc.rows = 3;
  desc.value = o.desc || '';
  desc.placeholder = '설명 (색 · 소재 · 상태)';
  desc.onchange = () => h.setField(o.id, 'desc', desc.value.trim());
  list.append(label, desc);
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
