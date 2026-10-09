// 프론트 화면 확인 (10-09) — 프론트를 바꾸면 이걸로 실제 화면을 찍어 본다.
// AI 호출(/api/items · components · analyze · review)은 가짜 응답으로 가로채서 비용이 들지 않는다.
// 서버(localhost:8000)는 떠 있어야 한다 — 화면 파일과 /api/refs 는 진짜를 쓴다.
//
//   cd scripts/ui_check && npm install
//   CHROME=<chrome 실행 파일> node check.js [출력 폴더]     # 기본 ./out — full.png(넓은 화면) · mobile.png(390px)
//
// 페이지 오류(pageerror)와 콘솔 경고를 그대로 출력하고, 단계별 칸이 비어 있으면 실패로 끝난다.
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const IMG = path.join(ROOT, 'backend/eval/data/images/');
const OUT = path.resolve(process.argv[2] || path.join(__dirname, 'out'));
const BASE = process.env.CARRET_URL || 'http://localhost:8000';
const F = ['a', 'b', 'c'].map(c => c.repeat(32));
const files = ['none_lego_p01.webp', 'none_lego_p02.webp', 'none_lego_p03.webp'];
const box = (x1, y1, x2, y2) => ({ x1, y1, x2, y2 });
const objs = [
  { index: 0, what: 'LEGO scorpion vehicle', name_ko: '레고 본체', box: box(396, 116, 977, 856), for_sale: true, role: 'main', photo: 0 },
  { index: 1, what: 'minifigure', name_ko: '미니피규어', box: box(114, 611, 277, 882), for_sale: true, role: 'component', photo: 0 },
  { index: 2, what: 'minifigure', name_ko: '미니피규어', box: box(281, 604, 442, 842), for_sale: true, role: 'component', photo: 0 },
  { index: 3, what: 'minifigure', name_ko: '미니피규어', box: box(439, 542, 584, 856), for_sale: true, role: 'component', photo: 0 },
  { index: 4, what: 'instruction booklet', name_ko: '설명서', box: box(172, 5, 502, 417), for_sale: true, role: 'component', photo: 0 },
  { index: 5, what: 'instruction booklet', name_ko: '설명서', box: box(497, 17, 814, 413), for_sale: true, role: 'component', photo: 0 },
  { index: 6, what: 'box', name_ko: '박스', box: box(0, 0, 100, 100), for_sale: true, role: 'accessory', photo: 1 },
];
const item = {
  item_id: 'd'.repeat(32), item: 'lego set', category: 'pack', complete: true, views_failed: false,
  photos: F.map((f, i) => ({ file_id: f, url: `/fake/${i}`, object: 'o1', view: null, state: null, slot: 'product',
                            source: 'photo', view_label: null, state_label: null })),
  objects: [{ id: 'o1', kind: 'product', label: '레고 키마 스콜피온 세트', desc: '갈색 전갈 모양 탈것과 미니피겨 3개, 설명서 2권',
              category: 'pack', for_sale: true, count: 1, photo_ids: F, missing: [], compositions: [], proof_type: null, proof_for: null }],
  main_object: 'o1', missing: [], retake: [], compositions: [], user_edited: false, needs_review: false,
};
const review = {
  object_id: 'o1', ref_file: 'style_lego.jpg', needs: [],
  photos: [{ file_id: F[0], shows: '본체와 미니피규어 3개, 설명서 2권' }, { file_id: F[1], shows: '본체 옆면' }, { file_id: F[2], shows: '설명서 표지' }],
  missing: [{ what: '미니피규어 뒷면', hint: '피규어를 뒤집어 한 장' }], base_file_id: F[0], with_ids: [F[1], F[2]],
};

async function shoot(browser, name, width) {
  const p = await browser.newPage();
  await p.setViewport({ width, height: 1000 });
  const problems = [];
  p.on('pageerror', e => problems.push('pageerror: ' + e.message));
  // 네트워크 실패 줄(Failed to load resource)은 요청 상태로 따로 본다 — 가짜 물건의 변환 400 은 정상
  p.on('console', m => { if (['error', 'warn'].includes(m.type()) && !m.text().startsWith('Failed to load resource')) problems.push(`console.${m.type()}: ${m.text()}`); });
  await p.setRequestInterception(true);
  p.on('request', r => {
    const u = new URL(r.url());
    const j = d => r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(d) });
    if (u.pathname.startsWith('/fake/')) {
      return r.respond({ status: 200, contentType: 'image/webp', body: fs.readFileSync(IMG + files[+u.pathname.split('/')[2]]) });
    }
    if (u.pathname === '/api/items' && r.method() === 'POST') return j(item);
    if (u.pathname === '/api/components') return j({ objects: objs, contents_hidden: false, extras: [] });
    if (u.pathname === '/api/analyze') return j({ item: 'lego set', item_count: 1, objects: objs, route: 'generate', reason: null });
    if (u.pathname.endsWith('/review')) return j(review);
    // 변환은 서버의 요청 검증만 거치게 — 가짜 물건이라 400(같은 물건 아님)·404 는 정상, 422(형식 오류)는 실패 (10-09 accessories:null 422)
    if (u.pathname === '/api/transform') return r.continue();
    return r.continue();
  });
  await p.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await (await p.$('#file')).uploadFile(...files.map(f => IMG + f));
  await new Promise(res => setTimeout(res, 4000));
  // 단계마다 내용이 있어야 한다 — 10-09 처럼 제목만 있고 목록이 빈 걸 잡는다
  const counts = await p.evaluate(() => ({
    photos: document.querySelectorAll('#item-photos > *').length,
    objects: document.querySelectorAll('#sell-objects > *').length,
    refs: document.querySelectorAll('#ref-list > *').length,
    contents: document.querySelectorAll('#photo-contents li').length,
    descVisible: !document.getElementById('obj-wrap').classList.contains('hidden'),
  }));
  for (const [k, v] of Object.entries(counts)) if (!v) problems.push(`비어 있음: ${k}`);
  await p.screenshot({ path: path.join(OUT, name), fullPage: true });
  const tx = new Promise(res => p.on('response', async x => {
    if (x.url().endsWith('/api/transform')) res([x.status(), (await x.text()).slice(0, 300)]);
  }));
  await p.evaluate(() => document.getElementById('run').click());
  const [st, body] = await Promise.race([tx, new Promise(res => setTimeout(() => res([0, '변환 요청이 안 나감']), 5000))]);
  counts.transform = st;
  if (st === 0 || st === 422 || st >= 500) problems.push(`변환 요청 ${st}: ${body}`);
  await p.close();
  return { name, counts, problems };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  const results = [await shoot(browser, 'full.png', 1400), await shoot(browser, 'mobile.png', 390)];
  await browser.close();
  let bad = 0;
  for (const r of results) {
    console.log(`${r.name}: ${JSON.stringify(r.counts)}`);
    for (const x of r.problems) { console.log('  ✗ ' + x); bad++; }
  }
  console.log(bad ? `문제 ${bad}건 — 스크린샷: ${OUT}` : `문제 없음 — 스크린샷: ${OUT}`);
  process.exit(bad ? 1 : 0);
})();
