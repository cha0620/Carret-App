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

  // 4) 메타 칩 렌더링
  document.getElementById("meta-chips").style.display = "flex";
  document.getElementById("meta-item").textContent = res.item || "object";

  const considered = res.considered || [];
  if (considered.length) {
    document.getElementById("meta-considered-wrap").style.display = "inline";
    document.getElementById("meta-considered").innerHTML =
      considered.map(c => `<span class="chip chip-considered">${c}</span>`).join("");
  } else {
    document.getElementById("meta-considered-wrap").style.display = "none";
  }

  //5) bubbles/gate_passed 렌더링은 기존대로 (여기에 추가)
  renderBubbles(res.bubbles);
  renderGate(res.gate_passed);

  console.log(res);
  return res;  // await 불필요 (이미 객체)
}