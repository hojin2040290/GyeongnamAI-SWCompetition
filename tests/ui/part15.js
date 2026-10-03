// 15부: 홈의 AI 에이전트 종합 점검 — 모든 일하는 곳에 카드, 처음 열면 한 번 자동 점검(진행 칸), 코드가 정리한 사실과
// AI 에이전트 판단(테스트 답변), 조언, 다시 열면 다시 점검하지 않음, 버튼으로 다시 점검 (가짜 AI 3초 대기)
const { B, ck, browser, page, api, overflow, summary } = require('./lib');
const PREFIX = '테스트 답변입니다';
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
  await p.evaluate(()=>document.querySelector('#ovPanel').scrollIntoView({block:'start'})); await p.waitForTimeout(1200);
  await p.screenshot({path:'p15_auto_live.png'});
  ck('[종합 점검] 처음 열면 자동 점검: 진행 칸(도는 표시)', live);
  ck('[종합 점검] 판단하는 동안 AI 에이전트가 판단 중', judging);
  await until(p, ()=>!document.querySelector('#ovLive .live')); await p.waitForTimeout(800);
  const txt = await p.textContent('#ovPanel');
  ck('[종합 점검] 코드가 정리한 사실 (계약서, 급여, 근무 기록, 증거)', ['계약서 점검','급여','근무 기록','증거 자료'].every(x=>txt.includes(x)), txt.slice(0,120));
  ck('[종합 점검] AI 에이전트 판단 칸에 결과, 이유(테스트 답변), 근거 조항', !!(await p.$('#ovAi .ai-judge')) && (await p.textContent('#ovAi')).includes(PREFIX) && (await p.textContent('#ovAi')).includes('근거 조항'));
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

  // 버튼: 누른 버튼 바로 아래 진행 칸
  await p.evaluate(()=>document.querySelector('#ovRun').scrollIntoView({block:'center'}));
  await p.click('#ovRun');
  const btnLive = await until(p, ()=>!!document.querySelector('#ovLive .live'), null, 10000);
  await p.waitForTimeout(1200);
  const pos = await p.evaluate(()=>{ const b=document.querySelector('#ovRun').getBoundingClientRect(), l=document.querySelector('#ovLive .live')?.getBoundingClientRect();
    const nav=document.querySelector('#tabs').getBoundingClientRect().top; return l?{gap:l.top-b.bottom, top:l.top, nav}:null; });
  await p.screenshot({path:'p15_button_live.png'});
  ck('[종합 점검] 버튼 바로 아래 진행 칸이 화면 안에', btnLive && pos && pos.gap>=0 && pos.gap<40 && pos.top+40<=pos.nav, JSON.stringify(pos));
  await until(p, ()=>!document.querySelector('#ovLive .live'));

  // 다른 일하는 곳: 그곳에도 카드가 있고 처음이면 자동 점검
  await p.evaluate(id=>{ state.current=id; }, j2.id); await p.evaluate(()=>loadHome()); 
  const live2 = await until(p, ()=>!!document.querySelector('#ovLive .live'), null, 10000);
  await until(p, ()=>!document.querySelector('#ovLive .live'));
  ck('[종합 점검] 다른 일하는 곳에도 카드와 자동 점검', live2 && (await p.textContent('#ovAi')).includes(PREFIX));
  ck('콘솔 오류 없음', p.errs.length===0, p.errs.join(' / '));
  ck('5xx 응답 없음', p.bad.length===0, p.bad.join(' / '));
  summary(); await b.close();
})().catch(e=>{ console.log('중단:', e.message); summary(); process.exit(1); });
