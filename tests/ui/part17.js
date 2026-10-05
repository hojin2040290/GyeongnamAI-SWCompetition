// 17부: 데모 모드 (DEMO_MODE=true). 체험 안내가 없는 계정도 업로드를 누르면 내 기기에서 고르기 + 데모 자료 전체 (그 칸에 맞는 것부터)
const { B, ck, browser, page, api, summary } = require('./lib');
const until = async (p, fn, arg, ms=30000) => { try{ await p.waitForFunction(fn, arg, {timeout:ms}); return true; }catch(e){ return false; } };
const shown = (p, sel) => p.evaluate(s=>!document.querySelector(s).classList.contains('hidden'), sel);
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'demo_mode@example.com',password:'test1234',birth_date:'2009-05-01'});
  await api(p,'POST','/api/jobs',{name:'QA 데모분식',wage:10320,start_date:'2026-08-01',schedule:{}});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  ck('[데모] 체험 안내 버튼은 없음 (안내가 없는 계정)', await p.evaluate(()=>document.getElementById('guideChip').classList.contains('hidden')));
  await p.click('#tabs [data-v="check"]'); await p.waitForTimeout(1200);
  await p.click('label.upload:has(#contractFile)'); await p.waitForTimeout(400);
  const n = (await p.$$('#guideFileList .guide-file')).length;
  ck('[데모] 업로드 누르면 내 기기에서 고르기와 데모 자료 30개', await shown(p,'#guideFiles') && await p.isVisible('#guideOwnFile') && n===30, n);
  ck('[데모] 계약서 칸은 계약서부터', (await p.textContent('#guideFileList .guide-file p')).includes('근로계약서'), await p.textContent('#guideFileList .guide-file p'));
  ck('[데모] 안내 문구가 데모 자료', (await p.textContent('#guideFilesSub')).includes('데모 자료'));
  await p.screenshot({path:'p17_demo_files.png'});
  await p.click('[data-guide-file="0"]');
  ck('[데모] 고른 데모 자료가 올라가고 AI가 읽음', await until(p, ()=>/읽었어요|저장했어요/.test(document.getElementById('ocrNote').textContent)), await p.textContent('#ocrNote'));
  await p.click('#tabs [data-v="docs"]'); await p.waitForTimeout(1200);
  await p.click('label.upload:has(#evFile)'); await p.waitForTimeout(400);
  ck('[데모] 자료 탭도 데모 자료 (맞는 것부터)', await shown(p,'#guideFiles') && /명세서|입금/.test(await p.textContent('#guideFileList .guide-file p')));
  await p.click('#guideFilesX');
  ck('[데모] 페이지 오류와 5xx 없음', !p.errs.length && !p.bad.length, [...p.errs, ...p.bad].join(' / '));
  await b.close(); summary();
})().catch(e=>{ console.log('중단', e.message); summary(); process.exit(1); });
