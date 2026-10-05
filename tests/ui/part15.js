// 15부: 홈의 AI 에이전트 종합 점검 — 모든 일하는 곳에 카드, 처음 열면 한 번 자동 점검(진행 칸), 코드가 정리한 사실과
// AI 에이전트 판단(테스트 답변), 조언, 다시 열면 다시 점검하지 않음, 버튼으로 다시 점검 (가짜 AI 3초 대기),
// 진행 중 화면 이동 (자동 점검은 안 움직임, 손대면 따라가기 멈춤, 진행 칸 높이 고정)
const { B, ck, browser, page, api, overflow, summary } = require('./lib');
const PREFIX = '테스트 답변입니다';
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
// 진행 중인 동안 화면 위치(scrollY)를 모은다
const scrolls = async (p, sel, ms=2400) => { const out=[]; const t0=Date.now();
  while(Date.now()-t0<ms){ out.push(await p.evaluate(s=>document.querySelector(s+' .live')?Math.round(scrollY):null, sel)); await p.waitForTimeout(200); }
  return out.filter(x=>x!==null); };
const until = async (p, fn, arg, ms=90000) => p.waitForFunction(fn, arg, {timeout:ms}).then(()=>true).catch(()=>false);
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'overview@example.com',password:'test1234',birth_date:'2009-05-01'});
  const j1=(await api(p,'POST','/api/jobs',{name:'QA 가상종합',wage:12000,start_date:'2026-08-01',schedule:{'월':{start:'16:00',end:'22:00',brk:'없음'}}})).body;
  const j2=(await api(p,'POST','/api/jobs',{name:'QA 가상종합 둘',wage:11000,start_date:'2026-08-01',schedule:{'토':{start:'10:00',end:'15:00',brk:'30분'}}})).body;
  await api(p,'POST',`/api/jobs/${j1.id}/payslip`,{}).catch(()=>{});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)');

  // 처음 열면 자동으로 한 번 종합 점검 (판단하는 동안 판단 중 + 진행 칸)
  const live = await until(p, ()=>!!document.querySelector('#ovLive .live .wait-note'), null, 10000);
  const judging = (await p.textContent('#ovPanel')).includes('AI 에이전트가 판단 중');
  const autoY = await scrolls(p, '#ovLive');
  ck('[화면 이동] 홈을 열 때 자동 점검은 화면을 움직이지 않음', autoY.length>0 && autoY.every(y=>y===0), autoY.join(','));
  await p.evaluate(()=>document.querySelector('#ovPanel').scrollIntoView({block:'start'})); await p.waitForTimeout(1200);
  await p.screenshot({path:'p15_auto_live.png'});
  ck('[종합 점검] 처음 열면 자동 점검: 진행 칸(도는 표시)', live);
  ck('[종합 점검] 판단하는 동안 AI 에이전트가 판단 중', judging);
  await until(p, ()=>!document.querySelector('#ovLive .live')); await p.waitForTimeout(800);
  const txt = await p.textContent('#ovPanel');
  ck('[종합 점검] 코드가 정리한 사실 (계약서, 급여, 근무 기록, 증거)', ['계약서 점검','급여','근무 기록','증거 자료'].every(x=>txt.includes(x)), txt.slice(0,120));
  ck('[종합 점검] AI 에이전트 판단 칸에 결과, 이유(테스트 답변), 근거 조항', !!(await p.$('#ovLive .trace-judge .ai-judge')) && (await p.textContent('#ovLive .trace-judge')).includes(PREFIX) && (await p.textContent('#ovLive .trace-judge')).includes('근거 조항'));
  ck('[종합 점검] 판단만 따로 보는 칸은 없음 (동작 보기 끝에 붙음)', !(await p.isVisible('#ovAi')) && !(await p.textContent('#ovPanel')).includes('AI 에이전트 판단 보기'));
  ck('[종합 점검] 조언(테스트 답변)이 같은 카드에', (await p.textContent('#ovPanel #caseAdvice')).includes(PREFIX));
  ck('[종합 점검] 점검 시각', /\d{2}:\d{2}/.test(await p.textContent('#ovNote')), await p.textContent('#ovNote'));
  ck('[종합 점검] 태그가 판단 결과', ['정상','확인 필요','위반 의심'].includes((await p.textContent('#ovTag')).trim()), await p.textContent('#ovTag'));
  ck('[홈] 진행 상황 카드에는 조언이 두 번 나오지 않음', !(await p.$('.case-card #caseAdvice')));
  await p.screenshot({path:'p15_result.png'});
  ck('[종합 점검] 390px에서 넘치는 칸 없음', (await overflow(p)).length===0, JSON.stringify(await overflow(p)));

  // 새로고침: 다시 자동 점검하지 않고, 마지막 에이전트 동작 보기가 남음
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(2500);
  ck('[종합 점검] 다시 열면 또 점검하지 않음', !(await p.$('#ovLive .live')));
  ck('[종합 점검] 다시 열어도 에이전트 동작 보기', (await p.textContent('#ovLive')).includes('에이전트 동작 보기'));
  const traceTxt = await p.evaluate(()=>document.querySelector('#ovLive').textContent);
  ck('[동작 보기] AI가 고른 도구는 판단 글과 따로 태그로 (도구 선택 글 없음)', !!(await p.$('#ovLive .log-ai .chip.tool')) && !traceTxt.includes('도구 선택'));
  ck('[동작 보기] 계획은 마지막 단계까지 (finish로 끝내기)', /계획[^]*finish로 끝내기\)/.test(traceTxt));

  // 버튼: 누른 버튼 바로 아래 진행 칸
  await p.evaluate(()=>document.querySelector('#ovRun').scrollIntoView({block:'center'}));
  await p.click('#ovRun');
  const btnLive = await until(p, ()=>!!document.querySelector('#ovLive .live'), null, 10000);
  await p.waitForTimeout(1200);
  const pos = await p.evaluate(()=>{ const b=document.querySelector('#ovRun').getBoundingClientRect(), l=document.querySelector('#ovLive .live')?.getBoundingClientRect();
    const nav=document.querySelector('#tabs').getBoundingClientRect().top; return l?{gap:l.top-b.bottom, top:l.top, nav}:null; });
  await p.screenshot({path:'p15_button_live.png'});
  ck('[종합 점검] 버튼 바로 아래 진행 칸이 화면 안에', btnLive && pos && pos.gap>=0 && pos.gap<40 && pos.top+40<=pos.nav, JSON.stringify(pos));
  let steps=0;  // 끝날 때까지 가장 컸던 진행 칸 높이
  while(await p.$('#ovLive .live')){ steps=Math.max(steps, await p.evaluate(()=>Math.round(document.querySelector('#ovLive .live-steps')?.getBoundingClientRect().height||0))); await p.waitForTimeout(200); }
  ck('[화면 이동] 단계가 늘어도 진행 칸 높이는 고정 (칸 안에서 스크롤)', steps>0 && steps<=170, steps+'px');

  // 사용자가 직접 화면을 움직이면 그 실행 동안은 진행 칸을 따라가지 않는다
  await p.evaluate(()=>document.querySelector('#ovRun').scrollIntoView({block:'center'}));
  await p.click('#ovRun'); await until(p, ()=>!!document.querySelector('#ovLive .live'), null, 10000); await p.waitForTimeout(400);
  await p.mouse.move(195,400); await p.mouse.wheel(0,-3000); await p.waitForTimeout(300);
  const handY = await scrolls(p, '#ovLive');
  ck('[화면 이동] 진행 중 사용자가 위로 올리면 다시 끌어내리지 않음', handY.length>0 && handY.every(y=>y===0), handY.join(','));
  await until(p, ()=>!document.querySelector('#ovLive .live'));

  // 다른 화면의 진행 칸 (같은 규칙): 계약서 점검
  await tab(p,'check'); await p.evaluate(()=>document.querySelector('#checkRun').scrollIntoView({block:'center'}));
  await p.click('#checkRun'); await until(p, ()=>!!document.querySelector('#checkTrace .live'), null, 10000); await p.waitForTimeout(2000);
  await p.screenshot({path:'p15_check_live.png'});
  const cpos = await p.evaluate(()=>{ const l=document.querySelector('#checkTrace .live')?.getBoundingClientRect();
    return l?{top:Math.round(l.top), h:Math.round(document.querySelector('#checkTrace .live-steps').getBoundingClientRect().height), nav:Math.round(document.querySelector('#tabs').getBoundingClientRect().top)}:null; });
  ck('[화면 이동] 계약서 점검: 누르면 진행 칸이 보이는 곳에, 높이 고정', cpos && cpos.top+40<=cpos.nav && cpos.h<=170, JSON.stringify(cpos));
  await until(p, ()=>!document.querySelector('#checkTrace .live'));
  await tab(p,'home');

  // 다른 일하는 곳: 그곳에도 카드가 있고 처음이면 자동 점검
  // (계약서를 점검해 기록이 바뀌었으므로 첫 곳은 홈에 오자마자 다시 자동 점검 중일 수 있다: 그 진행 칸이 둘째 곳에 남으면 안 된다)
  const firstRunning = !!(await p.$(`#ovLive .live[data-job="${j1.id}"]`));
  await p.evaluate(id=>{ state.current=id; }, j2.id); await p.evaluate(()=>loadHome());
  const live2 = await until(p, id=>!!document.querySelector(`#ovLive .live[data-job="${id}"]`), j2.id, 10000);
  const noOld = !(await p.$(`#ovLive .live[data-job="${j1.id}"]`));
  await until(p, ()=>!document.querySelector('#ovLive .live'));
  const judged2 = await until(p, x=>(document.querySelector('#ovLive .trace-judge')?.textContent||'').includes(x), PREFIX, 10000);  // 진행 칸이 닫힌 뒤 결과를 불러온다
  ck('[종합 점검] 다른 일하는 곳에도 카드와 자동 점검', live2 && judged2, `첫 곳 점검 중이었음: ${firstRunning}, 판단 칸: ${(await p.textContent('#ovAi')).slice(0,80)}, 진행 칸: ${(await p.textContent('#ovLive')).slice(0,80)}`);
  ck('[종합 점검] 바꾸기 전 일하는 곳의 진행 칸이 남지 않음', noOld);
  ck('콘솔 오류 없음', p.errs.length===0, p.errs.join(' / '));
  ck('5xx 응답 없음', p.bad.length===0, p.bad.join(' / '));
  summary(); await b.close();
})().catch(e=>{ console.log('중단:', e.message); summary(); process.exit(1); });
