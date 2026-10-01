// 화면 확인(회귀 시험) 공용 도구: 390px 휴대폰 화면, 콘솔 오류와 5xx 응답을 모은다
const { chromium } = require('playwright');
const B = process.env.BASE || 'http://localhost:8897';
const results = [];
function ck(name, ok, detail='') { results.push({name, ok: !!ok, detail: String(detail).slice(0,200)}); console.log(`${ok?'통과':'실패'} | ${name}${detail?' | '+String(detail).slice(0,160):''}`); }
async function browser() { return chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}); }
async function page(b) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const p = await ctx.newPage(); p.errs=[]; p.bad=[];
  p.on('pageerror', e=>p.errs.push(e.message));
  p.on('response', r=>{ if(r.url().includes('/api/') && r.status()>=500) p.bad.push(r.status()+' '+r.url()); });
  p.on('dialog', d=>d.accept().catch(()=>{}));
  return p;
}
const vis = p => p.evaluate(()=>['ob0','ob1','obLogin','obSeek','ob2','obMe'].filter(x=>!document.getElementById(x).classList.contains('hidden') && !document.getElementById('onboard').classList.contains('hidden')).join(',') || (document.getElementById('app').classList.contains('hidden')?'없음':'app'));
const toast = p => p.$eval('#toast', e=>e.textContent);
const api = (p, m, u, b) => p.evaluate(async([m,u,b])=>{ const r=await fetch(u,{method:m,headers:{'Content-Type':'application/json'},body:b===undefined?undefined:JSON.stringify(b)}); let j=null; try{j=await r.json()}catch(e){} return {status:r.status, body:j}; },[m,u,b]);
const overflow = p => p.evaluate(()=>[...document.querySelectorAll('input,select,textarea,button,.panel,.card')].filter(e=>e.offsetParent).filter(e=>e.getBoundingClientRect().right>document.documentElement.clientWidth+1).map(e=>e.id||e.className).slice(0,5));
function summary(){ const f=results.filter(r=>!r.ok); console.log(`\n== ${results.length}개 중 통과 ${results.length-f.length}, 실패 ${f.length}`); f.forEach(r=>console.log('  실패:', r.name, r.detail)); }
module.exports = { B, ck, browser, page, vis, toast, api, overflow, summary, results };
