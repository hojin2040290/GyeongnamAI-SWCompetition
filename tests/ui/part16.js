// 16부: 처음 쓰는 사람 체험 안내 (새로 가입한 계정만, FIRST_GUIDE=true). 사람이 누르는 순서대로
// 일하는 중으로 가입 → 첫 안내(통합 미션 8개) → 예시 계약서로 등록 → 출퇴근 → 계약서 → 급여 → 지원 전 확인 → 상담 자료
// → 그만둔 날 → 받음 여부 → 완료 창과 설문
const { B, ck, browser, page, vis, summary, overflow } = require('./lib');
const until = async (p, fn, arg, ms=20000) => { try{ await p.waitForFunction(fn, arg, {timeout:ms}); return true; }catch(e){ return false; } };
const chip = p => p.evaluate(()=>{ const c=document.getElementById('guideChip'); return c.classList.contains('hidden')?'숨김':c.textContent; });
const shown = (p, sel) => p.evaluate(s=>!document.querySelector(s).classList.contains('hidden'), sel);
const idle = p => until(p, ()=>!document.querySelector('.live'), null, 60000);
const tab = async (p, v) => { await p.click(`#tabs [data-v="${v}"]`); await p.waitForTimeout(900); };
(async () => {
  const b = await browser(); const p = await page(b);
  // 1. 가입: 지금 일하는 중
  await p.goto(B+'/'); await p.waitForTimeout(600);
  await p.click('.mode[data-mode=work]'); await p.click('#modeNext');
  await p.fill('#regEmail','guide_ui@example.com'); await p.fill('#regPw','test1234'); await p.fill('#birth','2009-05-01'); await p.click('#regBtn');
  await until(p, ()=>!document.getElementById('guideIntro').classList.contains('hidden'));
  ck('[안내] 가입하면 첫 안내 창', await shown(p,'#guideIntro'), await vis(p));
  ck('[안내] 고른 상황과 상관없이 통합 미션 8개', (await p.$$('#guideIntroList li')).length===8 && (await p.textContent('#guideIntroTitle')).includes('8개')
     && (await p.textContent('#guideIntroList')).includes('지원 전 확인') && (await p.textContent('#guideIntroList')).includes('상담 사전 자료'),
     await p.textContent('#guideIntroList'));
  await p.screenshot({path:'p16_intro.png'});
  await p.click('#guideIntroGo'); await p.waitForTimeout(500);
  // 2. 예시 계약서로 일하는 곳 채우기
  ck('[안내] 등록 화면에 예시 계약서로 채우기', await shown(p,'#guideJobFill'), await vis(p));
  await p.click('#guideJobFillGo'); await p.waitForTimeout(300);
  ck('[안내] 예시 내용으로 칸이 채워짐', (await p.inputValue('#jobList .f-name'))==='가상카페 시험점', await p.inputValue('#jobList .f-name'));
  await p.click('#saveJobsBtn'); await until(p, ()=>!document.getElementById('app').classList.contains('hidden'));
  await until(p, ()=>document.getElementById('guideChip').textContent.includes('1/8'), null, 5000);
  ck('[안내] 일하는 곳을 등록하면 미션 버튼 1/8', (await chip(p)).includes('1/8'), await chip(p));
  ck('[안내] 390px에서 넘치는 곳 없음', !(await overflow(p)).length, (await overflow(p)).join(','));
  await p.click('#guideChip'); await p.waitForTimeout(300);
  ck('[안내] 미션 목록 8개와 남은 미션의 바로 가기', (await p.$$('#guideMissions li')).length===8 && (await p.$$('#guideMissions [data-guide-go]')).length===7);
  await p.screenshot({path:'p16_sheet.png'});
  await p.click('#guideSheetX');
  // 3. 출근, 퇴근
  await p.click('#punchBtn'); await p.waitForTimeout(1200); await p.click('#punchBtn'); await idle(p);
  ck('[안내] 출퇴근하면 2/8', await until(p, ()=>document.getElementById('guideChip').textContent.includes('2/8'), null, 8000), await chip(p));
  // 4. 계약서: 업로드를 누르면 내 기기 / 예시 자료
  await tab(p,'check'); await idle(p);
  await p.click('label.upload:has(#contractFile)'); await p.waitForTimeout(400);
  ck('[안내] 업로드 누르면 내 기기에서 고르기와 예시 자료', await shown(p,'#guideFiles') && await p.isVisible('#guideOwnFile') && (await p.$$('#guideFileList .guide-file')).length===2);
  await p.screenshot({path:'p16_files.png'});
  await p.click('[data-guide-file="0"]');
  await until(p, ()=>/읽었어요|저장했어요/.test(document.getElementById('ocrNote').textContent), null, 60000);
  ck('[안내] 예시 계약서가 올라가고 AI가 읽음', (await p.textContent('#ocrNote')).includes('읽었어요'), await p.textContent('#ocrNote'));
  await p.click('#checkRun'); await idle(p);
  ck('[안내] 계약서 점검하면 3/8', await until(p, ()=>document.getElementById('guideChip').textContent.includes('3/8'), null, 30000), await chip(p));
  // 5. 급여: 명세서 예시 올리고 저장하고 비교
  await tab(p,'pay'); await p.fill('#payMonth','2026-08').catch(()=>{});
  await p.evaluate(()=>{ const m=document.getElementById('payMonth'); m.value='2026-08'; m.dispatchEvent(new Event('change')); });
  await p.click('#payFile'); await p.waitForTimeout(400);
  ck('[안내] 급여 탭 업로드도 예시 자료', await shown(p,'#guideFiles'));
  await p.click('[data-guide-file="0"]');
  await until(p, ()=>/AI가 읽은|저장했어요/.test(document.getElementById('payOcr').textContent), null, 60000);
  if(!(await p.inputValue('#payAmount'))) await p.fill('#payAmount','557280');
  await p.click('#paySave'); await idle(p);
  ck('[안내] 급여를 저장하고 비교하면 4/8', await until(p, ()=>document.getElementById('guideChip').textContent.includes('4/8'), null, 10000), await chip(p));
  // 6. 지원 전 확인: 예시 공고로 칸을 채우고 확인
  await tab(p,'check'); await p.click('#seekEntry'); await p.waitForTimeout(600);
  await p.click('#seekUpload'); await p.waitForTimeout(400);
  await p.click('[data-guide-file="0"]'); await p.waitForTimeout(800);
  await p.click('#seekRun'); await idle(p);
  ck('[안내] 지원 전 확인하면 5/8', await until(p, ()=>document.getElementById('guideChip').textContent.includes('5/8'), null, 10000), await chip(p));
  await p.click('#seekClose'); await p.waitForTimeout(500);
  // 7. 상담 사전 자료 (다 만들면 자료 화면으로 넘어간다)
  await tab(p,'docs'); await p.click('#reportBtn');
  await until(p, ()=>!location.pathname.endsWith('/') || location.pathname.includes('report'), null, 60000);
  await p.waitForTimeout(800); await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1500);
  ck('[안내] 상담 사전 자료를 만들면 6/8', (await chip(p)).includes('6/8'), await chip(p));
  // 8. 홈의 '이곳을 그만뒀어요' → 그만둔 날 저장
  await tab(p,'home'); await idle(p);
  await p.click('#quitOpen'); await p.waitForTimeout(300);
  await p.fill('#quitDateMain','2026-09-27'); await p.click('#quitSave'); await idle(p); await p.waitForTimeout(800); await idle(p);
  ck('[안내] 그만둔 날을 저장하면 7/8', await until(p, ()=>document.getElementById('guideChip').textContent.includes('7/8'), null, 10000), await chip(p));
  // 9. 남은 임금을 받았는지 답하기
  // 그만둔 날 저장 뒤 퇴직 정산 점검이 도는 동안 누르면 '다른 점검 중'이라 기다린다 (part19): 점검이 끝날 때까지 다시 누른다
  for(let i=0;i<10;i++){
    await until(p, ()=>!document.querySelector('#v-home .live'), null, 30000); await p.waitForTimeout(700);
    if(await p.evaluate(()=>document.querySelector('#v-home .live'))) continue;
    await p.click('#paidNo'); await p.waitForTimeout(1500); await idle(p);
    if((await chip(p)).includes('완료') || await shown(p,'#guideDone')) break;
  }
  ck('[안내] 미션 8개를 다 하면 완료 창', await until(p, ()=>!document.getElementById('guideDone').classList.contains('hidden'), null, 15000), await chip(p));
  ck('[안내] 완료 창에 설문하기', await p.isVisible('#guideDoneSurvey'));
  await p.screenshot({path:'p16_done.png'});
  await p.click('#guideKeep'); await p.waitForTimeout(800);
  ck('[안내] 계속 쓰기를 누르면 미션 버튼이 사라짐', (await chip(p))==='숨김', await chip(p));
  await p.click('#jobSwitch'); await p.waitForTimeout(300);
  ck('[안내] 설문하기는 일하는 곳 선택 창에 남음', await p.isVisible('#surveyBtn'));
  await p.click('#pickerClose');
  await tab(p,'docs');
  const chooser = p.waitForEvent('filechooser', {timeout:3000}).then(()=>true).catch(()=>false);
  await p.click('label.upload:has(#evFile)');
  ck('[안내] 다 끝낸 뒤 업로드는 바로 내 파일 고르기', await chooser && !(await shown(p,'#guideFiles')));
  ck('[안내] 페이지 오류와 5xx 없음', !p.errs.length && !p.bad.length, [...p.errs, ...p.bad].join(' / '));

  // 6. 구하는 중으로 가입: 지원 전 확인 화면에서 시작, '안내 없이 쓸게요'로 끄기
  const q = await page(b);
  await q.goto(B+'/'); await q.waitForTimeout(600);
  await q.click('.mode[data-mode=seek]'); await q.click('#modeNext');
  await q.fill('#regEmail','guide_seek@example.com'); await q.fill('#regPw','test1234'); await q.fill('#birth','2009-05-01'); await q.click('#regBtn');
  await until(q, ()=>!document.getElementById('guideIntro').classList.contains('hidden'));
  ck('[안내] 구하는 중: 지원 전 확인 화면과 통합 미션', (await vis(q))==='obSeek' && (await q.textContent('#guideIntroList')).includes('지원 전 확인'), await vis(q));
  await q.click('#guideIntroSkip'); await q.waitForTimeout(600);
  ck('[안내] 안내 없이 쓸게요: 미션 버튼 없음', (await chip(q))==='숨김', await chip(q));
  const ch2 = q.waitForEvent('filechooser', {timeout:3000}).then(()=>true).catch(()=>false);
  await q.click('#seekUpload');
  ck('[안내] 끈 뒤 업로드는 바로 내 파일 고르기', await ch2 && !(await shown(q,'#guideFiles')));
  // 상황을 고르기 전에 가입된 계정 (시험 계정, app/demo_db.py): 시작 화면에서는 안내를 띄우지 않고, 고른 상황의 미션으로 안내한다
  const r = await page(b);
  await r.goto(B+'/'); await r.waitForTimeout(500);
  await r.evaluate(()=>fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:'guide_nomode@example.com',password:'test1234',birth_date:'2009-05-20'})}));
  await r.goto(B+'/'); await r.waitForTimeout(1500);
  ck('[안내] 상황 고르기 전 계정: 시작 화면에 안내 창과 미션 버튼 없음', (await vis(r))==='ob0' && !(await shown(r,'#guideIntro')) && (await chip(r))==='숨김', await vis(r));
  await r.click('.mode[data-mode="quit"]'); await r.click('#modeNext'); await r.waitForTimeout(600);
  ck('[안내] 기본 정보 화면에도 안내 창 없음', (await vis(r))==='ob1' && !(await shown(r,'#guideIntro')), await vis(r));
  await r.click('#regBtn');
  ck('[안내] 상황을 고른 뒤 첫 안내 (통합 미션 8개)', await until(r, ()=>!document.getElementById('guideIntro').classList.contains('hidden'))
     && (await r.$$('#guideIntroList li')).length===8, await r.textContent('#guideIntroList'));
  await b.close(); summary();
})().catch(e=>{ console.log('중단', e.message); summary(); process.exit(1); });
