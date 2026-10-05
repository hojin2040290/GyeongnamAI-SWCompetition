// 기기 화면 캡처: 아이폰(393×852, 사파리 UA)과 갤럭시(360×780, 삼성 인터넷 UA) 크기에 실제 앱 글꼴로 화면을 찍는다.
// 진짜 사파리, 삼성 인터넷은 아니고 Chromium에 화면 크기, 해상도, 사용자 에이전트, 한국어를 맞춘 것이다 (삼성 인터넷은 Chromium 기반이라 거의 같다).
// 준비: bash tests/ui/fetch_fonts.sh (한 번)
// 사용: BASE=http://localhost:8080 EMAIL=test@example.com JOB=가상분식 OUT=/tmp/shots node tests/ui/device_shots.js [iphone|galaxy]
//   (알바가 있는 화면을 찍으려면 시험 데이터를 python -m app.demo_db --scenarios --force 로 만든다)
//   홈(위, 전체), 급여, 계약서, 자료, 보호 탭과 로그인 전 첫 화면을 찍는다. 화면 스타일을 바꾸면 찍어서 직접 열어 본다.
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const BASE = process.env.BASE || 'http://localhost:8080', EMAIL = process.env.EMAIL || 'test@example.com';
const PW = process.env.PW || 'test1234', JOB = process.env.JOB || '', OUT = process.env.OUT || 'device_shots';
const FONT = path.join(__dirname, '.fonts');
const DEV = {
  iphone: { viewport: { width: 393, height: 852 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'ko-KR',
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1' },
  galaxy: { viewport: { width: 360, height: 780 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'ko-KR',
    userAgent: 'Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36' },
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function useFonts(ctx) {  // 글꼴 요청에 미리 받아 둔 파일을 넘긴다 (없으면 기본 글꼴로 찍힌다고 알린다)
  if (!fs.existsSync(path.join(FONT, 'map.txt'))) { console.log('글꼴이 없어요: bash tests/ui/fetch_fonts.sh 를 먼저 실행해 주세요'); return; }
  const css = fs.readFileSync(path.join(FONT, 'plex.css'), 'utf8');
  const map = Object.fromEntries(fs.readFileSync(path.join(FONT, 'map.txt'), 'utf8').trim().split('\n').map(l => l.split(' ')));
  await ctx.route('https://fonts.googleapis.com/**', r => r.fulfill({ status: 200, contentType: 'text/css', body: css }));
  await ctx.route('https://fonts.gstatic.com/**', r => { const f = map[r.request().url()];
    return f ? r.fulfill({ status: 200, contentType: 'font/woff2', body: fs.readFileSync(path.join(FONT, f)), headers: { 'access-control-allow-origin': '*' } }) : r.abort(); });
}

async function shoot(browser, dev) {
  const ctx = await browser.newContext(DEV[dev]); await useFonts(ctx);
  const p = await ctx.newPage(), errs = []; p.on('pageerror', e => errs.push(e.message));
  const shot = async (name, full) => { await p.evaluate(() => document.fonts.ready); await sleep(400);
    await p.screenshot({ path: path.join(OUT, `${dev}_${name}.jpg`), type: 'jpeg', quality: 80, fullPage: !!full }); };
  const idle = () => p.waitForFunction(() => !document.querySelector('.live'), null, { timeout: 120000 }).catch(() => {});
  await p.goto(BASE); await sleep(900); await shot('0_start');
  await p.click('#toLogin'); await p.fill('#logEmail', EMAIL); await p.fill('#logPw', PW); await p.click('#logBtn');
  await p.waitForSelector('#app:not(.hidden)'); await sleep(1500);
  if (JOB) { await p.click('#jobSwitch'); await sleep(400); await p.click(`.place:has-text("${JOB}")`); await sleep(800); }
  await idle(); await sleep(600);
  await shot('1_home'); await shot('1_home_full', true);
  for (const t of ['pay', 'check', 'docs', 'guard']) { await p.click(`nav.tabs button[data-v=${t}]`); await sleep(1500); await idle(); await shot('2_' + t); }
  console.log(`${dev}: ${OUT}에 찍었어요. 페이지 오류: ${errs.length ? errs.join(' / ') : '없음'}`);
  await ctx.close();
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  for (const dev of process.argv[2] ? [process.argv[2]] : Object.keys(DEV)) await shoot(browser, dev);
  await browser.close();
})().catch(e => { console.log('중단', e.message); process.exit(1); });
