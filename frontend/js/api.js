/* 통신 계층 — 서버랑만 대화. 화면 모름. */

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/images/upload', { method: 'POST', body: fd });
  if (!res.ok) throw new Error('업로드 실패: ' + (await res.text()));
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

async function requestTransform(fileId, preset) {
  // 1) fetch → Response 객체 받기
  const resp = await fetch("/api/transform", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ file_id: fileId, preset: preset }),
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