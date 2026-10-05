// 제출물 PDF 만들기: 이 폴더의 HTML을 PDF로 (보고서·기술설명서는 A4, 발표자료는 16:9)
// 준비: bash tests/ui/fetch_fonts.sh (한 번, 앱과 같은 IBM Plex Sans KR 글꼴)
// 사용: node 제출물/원본/build.js  → 제출물/*.pdf (원본 HTML은 이 폴더), 쪽마다 확인용 PNG(제출물/원본/미리보기/, pdftoppm이 있을 때. 저장소에는 올리지 않음)
const path = require('path'), fs = require('fs');
const { chromium } = require(path.join(__dirname, '..', '..', 'tests', 'ui', 'node_modules', 'playwright'));
const FONT = path.join(__dirname, '..', '..', 'tests', 'ui', '.fonts');
const OUT_DIR = path.join(__dirname, '..');  // PDF는 제출물/ 에
const DOCS = [
  { html: '개발완료보고서.html', pdf: '개발완료보고서.pdf', a4: true },
  { html: 'AI_Agent_기술설명서.html', pdf: 'AI_Agent_기술설명서.pdf', a4: true, onePage: true },
  { html: '발표자료.html', pdf: '발표자료.pdf', a4: false, maxPages: 10 },
];

async function useFonts(ctx) {  // Google 글꼴 요청에 미리 받아 둔 파일을 넘긴다 (서버의 Chromium은 Google 글꼴을 받지 못함)
  if (!fs.existsSync(path.join(FONT, 'map.txt'))) throw new Error('글꼴이 없어요: bash tests/ui/fetch_fonts.sh 를 먼저 실행해 주세요');
  const css = fs.readFileSync(path.join(FONT, 'plex.css'), 'utf8');
  const map = Object.fromEntries(fs.readFileSync(path.join(FONT, 'map.txt'), 'utf8').trim().split('\n').map(l => l.split(' ')));
  await ctx.route('https://fonts.googleapis.com/**', r => r.fulfill({ status: 200, contentType: 'text/css', body: css }));
  await ctx.route('https://fonts.gstatic.com/**', r => { const f = map[r.request().url()];
    return f ? r.fulfill({ status: 200, contentType: 'font/woff2', body: fs.readFileSync(path.join(FONT, f)), headers: { 'access-control-allow-origin': '*' } }) : r.abort(); });
}

(async () => {
  const b = await chromium.launch();
  const preview = path.join(__dirname, '미리보기');
  fs.mkdirSync(preview, { recursive: true });
  let failed = false;
  for (const d of DOCS) {
    const ctx = await b.newContext(d.a4 ? { viewport: { width: 794, height: 1123 } } : { viewport: { width: 1920, height: 1080 } });
    await useFonts(ctx);
    const p = await ctx.newPage();
    await p.goto('file://' + path.join(__dirname, d.html));
    await p.evaluate(() => document.fonts.ready);
    const out = path.join(OUT_DIR, d.pdf);
    await p.pdf(d.a4 ? { path: out, format: 'A4', printBackground: true, preferCSSPageSize: true }
                     : { path: out, width: '1920px', height: '1080px', printBackground: true });
    const pages = (fs.readFileSync(out, 'latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
    const limit = d.onePage ? 1 : d.maxPages || 6;  // 보고서: 표지 1 + 본문 5
    console.log(`${d.pdf}: ${pages}쪽 (한도 ${limit}쪽)${pages > limit ? '  ← 넘침' : ''}`);
    if (pages > limit) failed = true;
    // 확인용 PNG: PDF 쪽 그대로 (poppler의 pdftoppm이 있으면)
    try { require('child_process').execFileSync('pdftoppm', ['-png', '-r', '60', out, path.join(preview, d.pdf.replace('.pdf', ''))]); } catch (e) {}
    await ctx.close();
  }
  await b.close();
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error('중단', e.message); process.exit(1); });
