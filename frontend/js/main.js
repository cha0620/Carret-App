/* 조율 계층 — 상태 + 이벤트. api 와 render 를 연결. */

let fileId = null;

const dropzone  = document.getElementById('dropzone');
const fileInput = document.getElementById('file');
const urlInput  = document.getElementById('url-input');
const urlBtn    = document.getElementById('url-run');
const presetSel = document.getElementById('preset');
const runBtn    = document.getElementById('run');
const statusEl  = document.getElementById('status');
const beforeImg = document.getElementById('before');
const afterImg  = document.getElementById('after');

const feedbackSubmitBtn = document.getElementById('feedback-submit');
let currentRating = 0;
let resetZoomScale = null;   // initZoom() 이 채움 — render.js 의 resetZoom() 에서 호출

// ---- 업로드: 파일 ----
dropzone.onclick = () => fileInput.click();
fileInput.onchange = () => handleFile(fileInput.files[0]);
dropzone.ondragover  = e => { e.preventDefault(); dropzone.classList.add('over'); };
dropzone.ondragleave = () => dropzone.classList.remove('over');
dropzone.ondrop = e => {
  e.preventDefault();
  dropzone.classList.remove('over');
  handleFile(e.dataTransfer.files[0]);
};

async function handleFile(file) {
  if (!file) return;
  statusEl.textContent = '업로드 중...';
  beforeImg.src = URL.createObjectURL(file);
  beforeImg.hidden = false;
  afterImg.hidden = true;
  clearResults();
  urlInput.value = '';

  try {
    const data = await uploadFile(file);
    fileId = data.file_id;
    statusEl.textContent = '업로드 완료! 변환하기를 누르세요';
    runBtn.disabled = false;
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

// ---- 업로드: URL ----
urlBtn.onclick = async () => {
  const url = urlInput.value.trim();
  if (!url) return;
  urlBtn.disabled = true;
  statusEl.textContent = 'URL 다운로드 중...';
  try {
    const data = await uploadUrl(url);
    fileId = data.file_id;
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
  if (!fileId) return;
  runBtn.disabled = true;
  statusEl.textContent = '변환 중... (몇 초 걸려요)';
  try {
    const data = await requestTransform(fileId, presetSel.value);

    // 이전 결과의 말풍선이 새 이미지 로드 전까지 잘못 남아있지 않도록 즉시 비움
    overlay.innerHTML = '';

    // 말풍선은 새 이미지가 실제로 로드된 뒤에 그려야 크기 계산(naturalWidth 등)이 맞음
    afterImg.onload = () => renderBubbles(data.bubbles || []);
    afterImg.onerror = () => { statusEl.textContent = '결과 이미지를 불러오지 못했습니다'; };
    afterImg.src = data.result_url + '?t=' + Date.now();
    afterImg.hidden = false;

    renderMetaChips(data);
    renderGate(data.gate_passed ?? null);
    
    // 성적표는 백그라운드 → 폴링으로 뒤따름
    renderQuality(null);  // 일단 숨김
    const qUrl = `/storage/quality/${fileId}_${presetSel.value}.json`;
    const poll = setInterval(async () => {
      const r = await fetch(qUrl + '?t=' + Date.now());
      if (r.ok) {
        renderQuality(await r.json());
        clearInterval(poll);
      }
    }, 3000);

    // 피드백: 우선 빈 박스 표시, 기존 피드백 있으면 채워넣기
    currentRating = 0;
    resetFeedbackBox();
    try {
      const fb = await fetchFeedback(fileId, presetSel.value);
      if (fb) {
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
    runBtn.disabled = false;
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
    await submitFeedback(fileId, presetSel.value, currentRating, feedbackComment.value.trim() || null);
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