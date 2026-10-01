const path = require('path');
// LLM을 부르는 모든 기능: 누르면 그 화면에 머물며 진행 칸이 보이는지 (가짜 AI 3초 대기)
const { B, ck, browser, page, api, summary } = require('./lib');
const { execSync } = require('child_process');
const fs = require('fs');
const tab = async (p, v) => { await p.evaluate(v=>document.querySelector(`#tabs [data-v="${v}"]`).click(), v); await p.waitForTimeout(900); };
const img = fs.readFileSync(path.join(__dirname, '..', '..', '테스트자료', '02_근로계약서.png'));
async function watch(p, name, box, action, shot) {
  const url0 = p.url();
  await action();
  let seen=false, inView=false;
  for (let i=0;i<30 && !seen;i++){ await p.waitForTimeout(50); seen = !!(await p.$(box+' .live')); }
  await p.waitForTimeout(1500);  // 첫 단계가 붙고 칸이 자리 잡은 뒤 위치를 잰다
  // 위 머리줄과 아래 메뉴에 가리지 않고 진행 칸 윗부분(제목과 첫 단계)이 보이는지
  const r = await p.evaluate(b=>{ const e=document.querySelector(b+' .live'); if(!e) return null; const k=e.getBoundingClientRect();
    const nav=document.querySelector('#tabs'); const navTop=nav&&!nav.classList.contains('hidden')?nav.getBoundingClientRect().top:innerHeight;
    const head=document.querySelector('.topbar, header'); const headBottom=head?head.getBoundingClientRect().bottom:0;
    return {top:k.top, bottom:k.bottom, navTop, headBottom}; }, box);
  inView = !!r && r.top >= r.headBottom-2 && r.top + 40 <= r.navTop;
  if (shot) await p.screenshot({path:shot});
  const stayed = p.url()===url0;
  await p.waitForFunction(b=>!document.querySelector(b+' .live'), box, {timeout:120000}).catch(()=>{});
  ck(`${name}: 진행 칸이 보임`, seen && inView, `${box} 보임=${seen} 화면 안=${inView} ${JSON.stringify(r)}`);
  ck(`${name}: 진행 중에는 화면 그대로`, stayed);
}
(async () => {
  const b = await browser(); const p = await page(b);
  p.removeAllListeners('dialog'); p.answer='';
  p.on('dialog', d=>{ d.type()==='prompt' ? d.accept(p.answer).catch(()=>{}) : d.accept().catch(()=>{}); });
  await p.goto(B+'/'); await api(p,'POST','/api/auth/register',{email:'live@example.com',password:'test1234',birth_date:'2009-05-01'});
  const job = (await api(p,'POST','/api/jobs',{name:'QA 가상분식',wage:9000,start_date:'2026-08-01',schedule:{'월':{start:'17:00',end:'22:30',brk:'없음'}}})).body;
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  // 홈: 퇴근
  require('child_process').execSync(`python3 ${path.join(__dirname,'addrec.py')} ${process.env.DB} ${job.id}`);  // 5시간 전 출근 (쉬는 시간 점검 대상)
  await p.goto(B+'/'); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  await watch(p, '퇴근하기(근무 점검)', '#punchLive', ()=>p.click('#punchBtn'), 'p7_punch.png');
  // 계약서
  await tab(p,'check');
  await watch(p, '계약서 사진 읽기', '#ocrLive', ()=>p.setInputFiles('#contractFile',{name:'계약서.png',mimeType:'image/png',buffer:img}), 'p7_ocr.png');
  await watch(p, '계약서 점검', '#checkTrace', ()=>p.click('#checkRun'));
  // 지원 전 확인
  await p.click('#seekEntry'); await p.waitForTimeout(400); await p.fill('#seekForm .f-wage','9000');
  await watch(p, '지원 전 확인', '#seekLive', ()=>p.click('#seekRun'), 'p7_seek.png');
  ck('지원 전 확인: 끝나면 결과 화면', !(await p.$eval('#seekResult',e=>e.classList.contains('hidden'))));
  await p.click('#seekClose'); await p.waitForTimeout(400);
  // 급여
  await tab(p,'pay'); await p.fill('#payMonth','2026-10');
  await watch(p, '급여 지금 점검', '#payTrace', ()=>p.click('#payRun'));
  await watch(p, '명세서 사진 읽기', '#payOcrLive', ()=>p.setInputFiles('#payFile',{name:'명세서.png',mimeType:'image/png',buffer:img}));
  await p.fill('#payAmount','300000');
  await p.evaluate(()=>window.scrollTo(0, document.body.scrollHeight));
  await watch(p, '받은 금액 저장하고 비교', '#payTrace', ()=>p.click('#paySave'), 'p7_paysave.png');
  p.answer='310000'; await p.evaluate(()=>window.scrollTo(0, document.body.scrollHeight));
  await watch(p, '받은 금액 고치기', '#payTrace', ()=>p.click('#payslips [data-edit]'));
  // 자료: 상담 사전 자료
  await tab(p,'docs');
  const docsUrl = p.url();
  await watch(p, '상담 사전 자료 만들기', '#reportTrace', ()=>p.click('#reportBtn'), 'p7_report_live.png');
  await p.waitForURL(/\/api\/reports\//, {timeout:15000}).catch(()=>{});
  ck('상담 사전 자료: 다 만든 뒤 자료로 넘어감', /\/api\/reports\//.test(p.url()), p.url());
  ck('상담 사전 자료: 돌아가기 링크', !!(await p.$('a[href="/"]')));
  await p.screenshot({path:'p7_report.png'});
  await p.goBack(); await p.waitForSelector('#app:not(.hidden)'); await p.waitForTimeout(800);
  ck('상담 사전 자료: 뒤로 가기로 앱에 돌아옴', p.url().startsWith(B) && !(/reports/.test(p.url())));
  // 보호
  await tab(p,'guard');
  await watch(p, '신고했어요 켜기', '#reportedLive', ()=>p.click('label:has(#reported)'), 'p7_guard.png');
  await p.fill('#postUrl','https://example.invalid/live/1');
  await watch(p, '게시물 보존', '#guardTrace', ()=>p.click('#postAdd'));
  await watch(p, '공개 게시물 검색', '#guardTrace', ()=>p.click('#postSearch'));
  // 홈: 그만둔 날, 받았어요, 질문 답하기
  await tab(p,'home');
  execSync(`python3 ${path.join(__dirname,'addq.py')} ${process.env.DB} ${job.id}`);
  await tab(p,'pay'); await tab(p,'home');
  const ask = await p.$('#askList [data-a]');
  if (ask) await watch(p, '에이전트 질문에 답하기', '#askTrace', ()=>ask.click()); else ck('에이전트 질문에 답하기: 질문 카드', false);
  await p.click('#quitOpen'); await p.fill('#quitDateMain', new Date().toISOString().slice(0,10));
  await watch(p, '그만둔 날 저장(퇴직 정산 점검)', '#quitLive', ()=>p.click('#quitSave'), 'p7_quit.png');
  await watch(p, '남은 임금 받았어요', '#quitLive', ()=>p.click('#paidYes'));
  ck('오류 없음', !p.errs.length && !p.bad.length, [...p.errs,...p.bad].join('|'));
  summary(); await b.close();
})().catch(e=>{console.error('중단',e);process.exit(1);});
