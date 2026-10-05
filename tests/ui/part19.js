// 19부: 홈의 에이전트 진행 칸은 한 번에 하나 (그만둔 곳을 열면 퇴직 정산과 종합 점검이 동시에 돌아 진행 칸 두 개에 같은 단계가 겹쳐 보이던 문제)
// 진행 중에는 가운데 'AI 에이전트 판단' 칸을 숨기고, 끝나면 펼쳐 결과를 보여 준다. 두 점검은 차례로 다 돈다
const { B, ck, browser, page, api, summary } = require('./lib');
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'one@example.com',password:'test1234',birth_date:'2009-05-01'});
  await api(p,'POST','/api/jobs',{name:'QA 가상치킨',wage:10320,start_date:'2026-08-01',status:'quit',quit_date:'2026-08-29',schedule:{}});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)');
  let most=0, midShown=0, seen=0;
  for(let i=0;i<60;i++){ await p.waitForTimeout(500);
    const s=await p.evaluate(()=>({n:document.querySelectorAll('#v-home .live').length,
      mid:!!document.querySelector('#ovPanel .live') && getComputedStyle(document.getElementById('ovAiWrap')).display!=='none'}));
    most=Math.max(most,s.n); if(s.mid) midShown++; if(s.n) seen++; }
  ck('[홈] 진행 칸은 한 번에 하나', most===1, `최대 ${most}개`);
  ck('[홈] 진행 중에는 가운데 판단 칸을 숨김', seen>0 && midShown===0, `진행 ${seen}번 중 보임 ${midShown}번`);
  await p.waitForFunction(()=>!document.querySelector('#v-home .live'),null,{timeout:120000}).catch(()=>{});
  await p.waitForTimeout(800);
  ck('[홈] 끝나면 가운데 판단 칸이 펼쳐져 결과가 보임', await p.evaluate(()=>document.getElementById('ovAiWrap').open) && (await p.textContent('#ovAi')).includes('테스트 답변'));
  ck('[홈] 판단 칸은 가운데 하나만 펼쳐짐 (퇴직 정산 판단은 눌러서 봄)', !(await p.evaluate(()=>document.querySelector('#quitPanel .quit-ai').open)));
  const ev = await p.evaluate(async()=>{ const j=(await (await fetch('/api/jobs')).json())[0];
    const rs=await (await fetch(`/api/jobs/${j.id}/agent/runs?limit=20`)).json(); return rs.map(r=>r.event); });
  ck('[홈] 퇴직 정산과 종합 점검이 차례로 다 돎', ev.includes('quit_check') && ev.includes('overview'), ev.join(','));
  // 종합 점검이 도는 중에 받았어요를 누르면 기다려 달라고 하고 두 번째 진행 칸을 띄우지 않는다
  await p.click('#ovRun'); await p.waitForTimeout(400);
  await p.click('#paidNo'); await p.waitForTimeout(300);
  ck('[홈] 다른 점검 중에 누르면 기다려 달라는 안내', (await p.textContent('#toast')).includes('다른 점검') && (await p.$$('#v-home .live')).length===1);
  await p.waitForFunction(()=>!document.querySelector('#v-home .live'),null,{timeout:120000}).catch(()=>{});
  ck('[홈] 페이지 오류와 5xx 없음', !p.errs.length && !p.bad.length, [...p.errs, ...p.bad].join(' / '));
  await b.close(); summary();
})().catch(e=>{ console.log('중단', e.message); summary(); process.exit(1); });
