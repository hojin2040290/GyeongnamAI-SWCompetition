// 14부: AI 버튼을 누르면 서버가 뒤에서 실행하고(X-Long-Task), 화면은 작업 번호로 결과를 물어 받는지 (cloudflared 100초 대비, 가짜 AI 3초)
const { B, ck, browser, page, api, summary } = require('./lib');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
const until = async (p, fn, arg, ms=90000) => p.waitForFunction(fn, arg, {timeout:ms}).then(()=>true).catch(()=>false);
(async () => {
  const b = await browser(); const p = await page(b);
  const reqs = [];
  p.on('request', r=>{ if(r.url().includes('/api/')) reqs.push({m:r.method(), u:r.url().replace(B,''), long:r.headers()['x-long-task']||''}); });
  const tasks = []; p.on('response', r=>{ if(r.url().includes('/api/tasks/')) tasks.push(r.status()); });
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'longtask@example.com',password:'test1234',birth_date:'2009-05-01'});
  await api(p,'POST','/api/jobs',{name:'QA 가상뒤실행',wage:12000,start_date:'2026-08-01',schedule:{'월':{start:'16:00',end:'22:00',brk:'없음'}}});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(500);

  // 계약서 점검
  await tab(p,'check'); reqs.length=0; tasks.length=0;
  await p.click('#checkRun');
  const seen = await until(p, ()=>!!document.querySelector('#checkTrace .live .wait-note'), null, 10000);
  await p.waitForTimeout(1200); await p.screenshot({path:'p14_check_live.png'});
  await until(p, ()=>!document.querySelector('#checkTrace .live'));
  await p.waitForTimeout(300);
  const post = reqs.find(r=>r.m==='POST' && /\/check$/.test(r.u));
  ck('[계약서] 점검 요청에 뒤에서 실행 표시(X-Long-Task)', post && post.long==='1', JSON.stringify(post));
  ck('[계약서] 작업 번호로 결과를 물어 받음 (기다리는 동안 202, 끝나면 200)', tasks.includes(202) && tasks[tasks.length-1]===200, JSON.stringify(tasks));
  ck('[계약서] 기다리는 동안 진행 칸(도는 표시)', seen);
  ck('[계약서] 결과가 화면에 나옴', (await p.$$('#checkItems .result .ai-judge')).length>0);
  ck('[계약서] 진행 단계 묻기(GET)는 그대로 바로 처리', reqs.filter(r=>r.u.startsWith('/api/agent/live')).every(r=>!r.long));

  // 상담 사전 자료 (다 만든 뒤 자료로 넘어감)
  await tab(p,'docs'); reqs.length=0; tasks.length=0;
  await p.click('#reportBtn');
  await p.waitForURL(/\/api\/reports\//, {timeout:60000}).catch(()=>{});
  const rp = reqs.find(r=>r.m==='POST' && /\/report$/.test(r.u));
  ck('[상담 자료] 뒤에서 실행하고 다 만든 뒤 자료로 넘어감', rp && rp.long==='1' && /\/api\/reports\//.test(p.url()), `${JSON.stringify(rp)} ${p.url()}`);

  // AI 버튼이 아닌 저장 요청은 그대로 바로 처리
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); reqs.length=0;
  await api(p,'PUT','/api/me/prefs',{});
  ck('진행 칸 밖의 요청은 뒤에서 실행하지 않음', reqs.every(r=>!r.long), JSON.stringify(reqs.filter(r=>r.long)));
  ck('콘솔 오류 없음', p.errs.length===0, p.errs.join(' / '));
  ck('5xx 응답 없음', p.bad.length===0, p.bad.join(' / '));
  summary(); await b.close();
})().catch(e=>{ console.log('중단:', e.message); summary(); process.exit(1); });
