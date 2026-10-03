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

let busy = false;                // 업로드·변환 중 — 다른 동작이 섞이지 않게 (10-01 리뷰)
let uploadToken = 0;             // 늦게 도착한 업로드 응답을 버리기 위한 세대

function setBusy(on) {
  busy = on;
  for (const el of [dropzone, addBtn, urlBtn]) {
    el.classList.toggle('disabled', on);
    if ('disabled' in el) el.disabled = on;
  }
  document.getElementById('item-photos').classList.toggle('locked', on);
  runBtn.disabled = on || !fileId;
}

let composition = null;          // 고른 정석 구도 키 (신발만 — 없으면 null)

function compOfPhoto(fid) {
  const c = (item?.compositions || []).find(c => c.photo_ids.includes(fid));
  return c ? c.key : null;
}

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
  beforeImg.src = p.url;
  beforeImg.hidden = false;
  afterImg.hidden = true;
  clearResults();
  renderItem(item, fileId, p => selectPhoto(p));
  renderComps(item, composition, pickComp);
  runBtn.disabled = false;
  statusEl.textContent = '이 구도로 변환하려면 변환하기를 누르세요';
}

function firstGoodPhoto(it) {
  // 대표컷 구도에 맞는 사진이 있으면 그것부터 (구도 목록의 첫 번째가 대표컷)
  const first = (it.compositions || []).find(c => c.available);
  if (first) return it.photos.find(p => p.file_id === first.photo_ids[0]);
  const bad = new Set(it.retake.map(r => r.file_id));
  return it.photos.find(p => !bad.has(p.file_id)) || it.photos[0];
}

async function handleFiles(files, append = false) {
  files = files.filter(Boolean);
  if (!files.length || busy) return;
  const token = ++uploadToken;
  const hasVideo = files.some(f => f.type.startsWith('video/'));
  statusEl.textContent = hasVideo ? '동영상에서 장면 고르는 중... (조금 걸려요)' : '업로드하고 각도 확인 중...';
  if (!append) {
    fileId = null; item = null; composition = null; clearResults(); hideItem();
    afterImg.hidden = true; beforeImg.hidden = true;
  }
  urlInput.value = '';
  setBusy(true);
  try {
    const res = await uploadItem(files, append && item ? item.item_id : null);
    if (token !== uploadToken) return;            // 그사이 다른 업로드가 시작됨
    item = res;
    const keep = append && item.photos.find(p => p.file_id === fileId);
    setBusy(false);
    selectPhoto(keep || firstGoodPhoto(item));
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

addBtn.onclick = () => { if (!busy) addInput.click(); };
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
  if (!fileId || busy) return;
  const fileId_ = fileId;        // 변환 중 사진을 바꿔도 이 결과 · 성적표 · 피드백은 이 사진에
  const comp_ = composition;
  setBusy(true);
  statusEl.textContent = '변환 중... (몇 초 걸려요)';
  try {
    const data = await requestTransform(fileId_, PRESET, comp_);

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