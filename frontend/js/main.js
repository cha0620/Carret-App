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
    
    afterImg.src = data.result_url + '?t=' + Date.now();
    afterImg.hidden = false;
    
    // 말풍선 + 게이트 (방어적 코딩)
    renderMetaChips(data)
    renderBubbles(data.bubbles || []);
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
    
    statusEl.textContent = '완료! 🎉';
  } catch (e) {
    statusEl.textContent = '변환 실패: ' + e.message;
  } finally {
    runBtn.disabled = false;
  }
};