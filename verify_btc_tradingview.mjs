import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
const dir=path.resolve(process.argv[2]||'reports/BTCUSDT_20260606_current_tradingview');
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'btc-tv-browser-'));
const chrome=spawn('/tmp/chan-browsers/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--allow-file-access-from-files','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank']);
let logs='',ws,session,id=0;const pending=new Map();const delay=ms=>new Promise(r=>setTimeout(r,ms));
chrome.stderr.on('data',c=>logs+=c.toString());
function send(method,params={},sid=session){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params,...(sid?{sessionId:sid}:{})}));setTimeout(()=>{if(pending.has(n)){pending.delete(n);reject(Error('CDP timeout '+method))}},20000).unref()})}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
const assert=(ok,message)=>{if(!ok)throw Error(message)},checks=[],errors=[];
async function settle(){await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>setTimeout(r,60))))')}
try{
 for(let i=0;i<100&&!logs.includes('DevTools listening on ');i++)await delay(100);
 const url=logs.match(/DevTools listening on (ws:\/\/\S+)/)?.[1];if(!url)throw Error(logs);
 ws=new WebSocket(url);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
 const {targetId}=await send('Target.createTarget',{url:'about:blank'},null);
 session=(await send('Target.attachToTarget',{targetId,flatten:true},null)).sessionId;
 await send('Page.enable');await send('Runtime.enable');
 await send('Emulation.setDeviceMetricsOverride',{width:1680,height:1100,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:pathToFileURL(path.join(dir,'btc_tradingview.html')).href});
 let ready=false;for(let i=0;i<100;i++){if(await evaluate('!!window.chartReady')){ready=true;break}await delay(100)}assert(ready,'chart ready');
 const payload=JSON.parse(fs.readFileSync(path.join(dir,'chart_data.json'),'utf8'));
 assert(payload.start===Date.parse('2026-06-06T00:00:00+08:00')/1000,'display start');
 for(const period of ['8h','4h','2h','1h'])for(const basis of ['strokes','types']){
  await evaluate(`chartUI.setPeriod(${JSON.stringify(period)});chartUI.setBasis(${JSON.stringify(basis)});chartUI.setFilter('all');chartUI.chart.timeScale().fitContent()`);await settle();
  const stats=await evaluate('chartStats'),frame=payload.frames[period],view=frame.views[basis];
  assert(stats.bars===frame.bars.length,'bar count');assert(stats.signals===view.signals.length,'signal count');
  assert(stats.visibleMarkers===view.signals.length,period+' '+basis+' all markers visible');
  assert(stats.markers.every(m=>Number.isFinite(m.x)&&Number.isFinite(m.y)),'marker finite');
  for(const s of view.signals.filter(s=>s.certainty==='pending_stroke_candidate')){
   const marker=stats.markers.find(m=>m.id===s.id);
   assert(marker&&!marker.confirmed,'pending tail uses hollow candidate marker');
   assert(s.actionable===false&&s.higher_point_confirmed===false,'pending tail cannot authorize execution');
  }
  if(period==='1h'&&basis==='strokes'){
   const target=view.signals.filter(s=>s.time===Date.parse('2026-10-12T14:00:00+08:00')/1000&&s.price===84340.2);
   assert(target.some(s=>s.label==='S2'&&s.certainty==='pending_stroke_candidate'),'Oct 12 S2 candidate present');
   assert(target.some(s=>s.label==='S3'&&s.certainty==='pending_stroke_candidate'),'Oct 12 S3 candidate present');
  }
  const sourceLines=view.nodes.filter(n=>['stroke','segment'].includes(n.kind)&&Math.min(n.end,frame.bars.at(-1).time)>Math.max(n.start,frame.bars[0].time));
  if(stats.lines.length!==sourceLines.length)throw Error(period+' '+basis+' lines '+JSON.stringify({actual:stats.lines.length,expected:sourceLines.length,missing:sourceLines.filter(n=>!stats.lines.some(l=>l.id===n.id)),range:await evaluate('chartUI.chart.timeScale().getVisibleRange()')}));
  for(const l of stats.lines){const n=sourceLines.find(n=>n.id===l.id);assert(l.width===(n.kind==='stroke'?(n.direct_break?3.2:1.15):2.2),'line width');assert(l.color===(n.kind==='stroke'?'#4c9aff':'#ffad45'),'unchanged line color');assert(JSON.stringify(l.dash)===JSON.stringify(n.state==='completed'?[]:[6,4]),'unchanged dash')}
  for(const filter of ['B1','B2','B3','S1','S2','S3','all']){
   await evaluate(`chartUI.setFilter(${JSON.stringify(filter)})`);await settle();
   const count=await evaluate('chartStats.signals'),expected=view.signals.filter(s=>filter==='all'||s.label===filter).length;
   assert(count===expected,'filter count');assert(await evaluate('document.querySelectorAll("#rows tr").length')===Math.max(1,expected),'table count');
  }
  if(view.signals.length){await evaluate('document.querySelector("#rows tr").click()');await settle();assert(await evaluate('!!chartStats.selected'),'table selection');await evaluate('chartUI.chart.timeScale().fitContent()');await settle()}
  for(const id of ['strokes','segments','signals','centers','prices']){
   await evaluate(`document.getElementById('${id}').click()`);await settle();
   if(id==='strokes')assert((await evaluate('chartStats.lines.filter(n=>n.kind==="stroke").length'))===0,'hide strokes');
   if(id==='segments')assert((await evaluate('chartStats.lines.filter(n=>n.kind==="segment").length'))===0,'hide segments');
   if(id==='signals')assert((await evaluate('chartStats.visibleMarkers'))===0,'hide signals');
   await evaluate(`document.getElementById('${id}').click()`);await settle();
  }
  if(basis==='strokes'){
   const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(dir,period+'_full.png'),Buffer.from(shot.data,'base64'));
   await evaluate(`document.querySelector('[data-days="30"]').click()`);await settle();
   const recent=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(dir,period+'_recent30d.png'),Buffer.from(recent.data,'base64'));
  }
  checks.push({period,basis,bars:stats.bars,signals:stats.signals,lines:stats.lines.length,breaks:stats.lines.filter(n=>n.direct_break).length});
 }
 await send('Emulation.setDeviceMetricsOverride',{width:430,height:1000,deviceScaleFactor:1,mobile:true});
 await evaluate("chartUI.setPeriod('1h');chartUI.setBasis('strokes');document.querySelector('[data-days=\"7\"]').click()");await settle();
 assert(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'mobile no overflow');
 const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(dir,'mobile.png'),Buffer.from(shot.data,'base64'));
 assert(await evaluate('chartUI.chart.takeScreenshot().width>0'),'PNG export');assert(errors.length===0,'no browser errors');
 const result={passed:true,checks,filters:56,errors,mobile:true};fs.writeFileSync(path.join(dir,'browser_validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}catch(e){console.error(e);process.exitCode=1;fs.writeFileSync(path.join(dir,'browser_error.json'),JSON.stringify({error:String(e),errors,checks},null,2))}
finally{fs.writeFileSync(path.join(dir,'browser.log'),logs);ws?.close();chrome.kill();await delay(100);fs.rmSync(profile,{recursive:true,force:true})}
