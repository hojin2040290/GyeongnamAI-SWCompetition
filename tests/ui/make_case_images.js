// 시험 데이터 서류 사진 만들기 (개발자용): app/demo_db.py --html 이 쓴 HTML을 PNG로 찍는다.
// 사용: python -m app.demo_db --html /tmp/docs && node tests/ui/make_case_images.js /tmp/docs 테스트자료/알바5개
const fs = require('fs'), path = require('path');
const { chromium } = require('playwright');
(async () => {
  const [src, out] = process.argv.slice(2);
  if (!src || !out) { console.error('사용: node make_case_images.js <HTML 폴더> <사진 폴더>'); process.exit(1); }
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const p = await b.newPage({ viewport: { width: 1200, height: 800 } });
  for (const f of fs.readdirSync(src).filter(f => f.endsWith('.html')).sort()) {
    await p.goto('file://' + path.resolve(src, f)); await p.waitForTimeout(200);
    const el = await p.$('body > div');  // 서류 영역만
    await el.screenshot({ path: path.join(out, f.replace('.html', '.png')) });
    console.log('만듦:', f.replace('.html', '.png'));
  }
  await b.close();
})().catch(e => { console.error('중단', e); process.exit(1); });
