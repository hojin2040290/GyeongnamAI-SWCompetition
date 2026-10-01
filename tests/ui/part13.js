// 13부: '판단 대기'가 남지 않는지 — 퇴직 정산과 급여 비교가 판단 대기인 채로 화면을 열면 AI가 있을 때 바로 다시 점검,
// 다음 날(남은 날 수가 바뀐 뒤)에도 저장된 판단을 그대로 보여 줌 (가짜 AI 3초 대기)
const { execSync } = require('child_process');
const { B, ck, browser, page, api, summary } = require('./lib');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
const until = async (p, fn, arg, ms=90000) => p.waitForFunction(fn, arg, {timeout:ms}).then(()=>true).catch(()=>false);
(async () => {
  const b = await browser(); const p = await page(b);
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'rejudge@example.com',password:'test1234',birth_date:'2009-05-01'});
  const job=(await api(p,'POST','/api/jobs',{name:'QA 가상대기',wage:12000,start_date:'2026-08-01',schedule:{'월':{start:'16:00',end:'22:00',brk:'없음'}}})).body;
  // 저장만 하고 점검은 하지 않음 → 판단 대기
  await api(p,'POST',`/api/jobs/${job.id}/quit`,{quit_date:'2026-08-30', check:false});
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)');
  const started = await until(p, ()=>!!document.querySelector('#quitLive .live'), null, 10000);
  ck('[퇴직 정산] 판단 대기면 화면을 열 때 바로 다시 점검', started);
  await until(p, ()=>!document.querySelector('#quitLive .live')); await p.waitForTimeout(500);
  ck('[퇴직 정산] 다시 점검 뒤 AI 판단', (await p.textContent('#quitAi')).includes('테스트 답변입니다') && !(await p.textContent('#quitTag')).includes('대기'));
  // 다음 날처럼: 저장된 판단의 남은 날 수를 바꿔 둔다
  execSync(`python3 -c "import sqlite3,json; c=sqlite3.connect('${process.env.DB}'); r=c.execute(\\"select id,results_json from checkrun where kind='quit' order by id desc limit 1\\").fetchone(); d=json.loads(r[1]); d['left']=d['left']+1; c.execute('update checkrun set results_json=? where id=?',(json.dumps(d,ensure_ascii=False),r[0])); c.commit()"`);
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(1500);
  ck('[퇴직 정산] 다음 날에도 저장된 판단 (판단 대기로 돌아가지 않음)', !(await p.textContent('#quitTag')).includes('대기') && !(await p.$('#quitLive .live')));
  await p.screenshot({path:'p13_quit.png'});
  // 급여: 이번 달 근무 기록과 받은 금액만 저장(점검 안 함) → 급여 탭을 열면 다시 점검
  const job2=(await api(p,'POST','/api/jobs',{name:'QA 가상급여대기',wage:12000,start_date:'2026-08-01',schedule:{'월':{start:'16:00',end:'22:00',brk:'없음'}}})).body;
  execSync(`python3 ${require('path').join(__dirname,'addrec.py')} ${process.env.DB} ${job2.id}`);
  await api(p,'POST',`/api/jobs/${job2.id}/punch`,{confirm:true, check:false});
  const month = await p.evaluate(()=>{ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; });
  await p.evaluate(async ([id,m])=>{ const fd=new FormData(); fd.append('month',m); fd.append('amount','1000'); fd.append('check','false');
    await fetch(`/api/jobs/${id}/payslip`,{method:'POST',body:fd}); }, [job2.id, month]);
  await p.reload(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  await p.click('#jobSwitch'); await p.waitForTimeout(400);  // 사람이 하듯 일하는 곳을 바꾼다
  await p.click('text=QA 가상급여대기'); await p.waitForTimeout(800);
  await tab(p,'pay');  // 이번 달이 기본으로 열린다
  const payStarted = await until(p, ()=>!!document.querySelector('#payTrace .live'), null, 10000);
  await p.dispatchEvent('#payMonth','change');  // 도는 중에 달을 다시 골라도 진행 칸과 '판단 중'이 남는지
  ck('[급여] 점검 중에 달을 다시 골라도 진행 칸이 남음', !!(await p.$('#payTrace .live')) && (await p.textContent('#payBody')).includes('판단 중'));
  ck('[급여] 판단 대기면 탭을 열 때 바로 다시 점검', payStarted);
  await until(p, ()=>!document.querySelector('#payTrace .live')); await p.waitForTimeout(500);
  ck('[급여] 다시 점검 뒤 AI 판단', ((await p.textContent('#payBody .ai-judge'))||'').includes('테스트 답변입니다'));
  await tab(p,'home'); await tab(p,'pay'); await p.waitForTimeout(800);
  ck('[급여] 다시 열어도 또 점검하지 않음', !(await p.$('#payTrace .live')));
  await p.screenshot({path:'p13_pay.png'});
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
