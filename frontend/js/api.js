/* 통신 계층 — 서버랑만 대화. 화면 모름. */

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/images/upload', { method: 'POST', body: fd });
  if (!res.ok) throw new Error('업로드 실패: ' + (await res.text()));
  return res.json();
}

// 여러 각도 — 사진 여러 장 · 동영상 → 물건 묶음 (각도 · 빠진 면)
// proofFiles = 근거 칸 (보증서 · 정품 마크 · 영수증) — 서버가 칸대로 근거 사진으로 둔다
async function uploadItem(files, itemId = null, proofFiles = []) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  for (const f of proofFiles) fd.append('proof_files', f);
  const url = itemId ? `/api/items/${itemId}/files` : '/api/items';
  const res = await fetch(url, { method: 'POST', body: fd });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    // 422 는 detail 이 목록 — 그대로 문자열로 만들면 "[object Object]"
    const msg = typeof e.detail === 'string' ? e.detail : `업로드 실패 (${res.status})`;
    throw new Error(msg);
  }
  return res.json();
}

// 사용자가 확인 · 고친 물건 묶음 (10-04) — 물건 목록 전체 + 사진 전부의 물건 · 각도
async function updateObjects(itemId, body) {
  const res = await fetch(`/api/items/${itemId}/objects`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(typeof e.detail === 'string' ? e.detail : `저장 실패 (${res.status})`);
  }
  return res.json();
}

// 파는 물건 여러 개를 한 장에 (10-04) — 원본에서 오려 코드로 놓는다
async function arrangeObjects(itemId, objects, layout, photos = {}) {
  const res = await fetch(`/api/items/${itemId}/arrange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ objects, layout, photos }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(typeof e.detail === 'string' ? e.detail : `배치 실패 (${res.status})`);
  }
  return res.json();
}

async function uploadUrl(url) {
  const res = await fetch('/api/images/upload-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error('URL 실패: ' + (await res.text()));
  return res.json();
}

// 사진 미리 분석 — 사진 속 물건 목록(objects)으로 팔 물건을 고른다 (10-03). 결과는 서버가 저장해 변환 때 다시 쓴다
async function fetchReview(itemId, objectId, refFile) {
  const q = new URLSearchParams({ object_id: objectId });
  if (refFile) q.set('ref_file', refFile);
  const resp = await fetch(`/api/items/${itemId}/review?` + q.toString(), { method: 'POST' });
  if (!resp.ok) throw new Error('사진 검토 실패');
  return resp.json();
}

async function fetchComponents(fileId, extraViewIds = null, itemId = null) {
  const resp = await fetch('/api/components', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ file_id: fileId, extra_view_ids: extraViewIds, item_id: extraViewIds ? itemId : null }),
  });
  if (!resp.ok) throw new Error('구성품 빠른 분석 실패');
  return resp.json();
}

async function analyzePhoto(fileId) {
  const resp = await fetch('/api/analyze', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ file_id: fileId }),
  });
  if (!resp.ok) throw new Error('분석 실패');
  return resp.json();
}

// separate = 물건마다 따로 만드는 중 (sell 은 하나) — 서버가 결과 이름에 물건 번호를 붙인다
async function fetchRefs() {
  const resp = await fetch("/api/refs");
  if (!resp.ok) return [];
  return (await resp.json()).refs || [];
}

async function requestTransform(fileId, preset, composition = null, sell = null, separate = false, accessories = null,
                                extraViewIds = null, refFile = null, itemId = null, mains = null) {
  // 1) fetch → Response 객체 받기
  const resp = await fetch("/api/transform", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ file_id: fileId, preset: preset, composition, sell, separate, accessories,
                           extra_view_ids: extraViewIds, ref_file: refFile,
                           item_id: extraViewIds ? itemId : null, mains }),
  });

  // 2) 실패면 throw (Response 상태에서)
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e.detail || `변환 실패 (${resp.status})`);
  }

  // 3) 성공시에만 파싱 (이게 유일한 .json() 호출)
  const res = await resp.json();

  // 렌더링(메타 칩/말풍선/게이트)은 여기서 하지 않는다 — 통신 계층은 화면을 모른다.
  // 특히 말풍선은 새 결과 이미지가 실제로 로드된 "뒤"에 그려야 크기 계산이 맞으므로
  // main.js 의 img.onload 안에서 처리한다.
  return res;
}

async function submitFeedback(fileId, presetKey, rating, comment) {
  const resp = await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, preset_key: presetKey, rating, comment }),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e.detail || `피드백 전송 실패 (${resp.status})`);
  }
  return resp.json();
}

async function fetchFeedback(fileId, presetKey) {
  const resp = await fetch(`/api/feedback/${fileId}/${presetKey}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`피드백 조회 실패 (${resp.status})`);
  return resp.json();
}