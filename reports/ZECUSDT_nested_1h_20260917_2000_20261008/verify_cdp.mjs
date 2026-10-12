import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
const dir=path.dirname(fileURLToPath(import.meta.url)),mode=process.argv[2]||'verify';
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'zec-chart-browser-'));
const chrome=spawn('/tmp/chan-browsers/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--allow-file-access-from-files','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank']);
let logs='',ws,session,id=0;const pending=new Map();const delay=ms=>new Promise(r=>setTimeout(r,ms));
chrome.stderr.on('data',c=>logs+=c.toString());
function send(method,params={},sid=session){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params,...(sid?{sessionId:sid}:{})}));setTimeout(()=>{if(pending.has(n)){pending.delete(n);reject(Error('CDP timeout '+method))}},20000).unref()})}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
try{
 for(let i=0;i<100&&!logs.includes('DevTools listening on ');i++)await delay(100);
 const url=logs.match(/DevTools listening on (ws:\/\/\S+)/)?.[1];if(!url)throw Error(logs);
 ws=new WebSocket(url);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
 const {targetId}=await send('Target.createTarget',{url:'about:blank'},null);
 session=(await send('Target.attachToTarget',{targetId,flatten:true},null)).sessionId;
 await send('Page.enable');await send('Runtime.enable');
 await send('Emulation.setDeviceMetricsOverride',{width:mode==='mobile'?430:1600,height:1100,deviceScaleFactor:1,mobile:mode==='mobile'});
 await send('Page.navigate',{url:pathToFileURL(path.join(dir,'zec_42_tradingview.html')).href+(mode==='verify'?'?verify=1':'')});
 let ready=false;
 for(let i=0;i<150;i++){
  const r=await evaluate('({ready:!!window.chartReady, verified:document.body?.dataset.verified, error:window.chartError})');
  if(r.error)throw Error(r.error);
  if(mode==='verify'?r.verified==='true':r.ready){ready=true;break}
  await delay(150);
 }
 if(!ready)throw Error('Browser test did not finish: '+await evaluate('document.getElementById("test-result")?.textContent'));
 await delay(400);
 const stats=await evaluate('window.chartStats');
 if(mode==='verify'){
  const result=JSON.parse(await evaluate('document.getElementById("test-result").textContent'));
  fs.writeFileSync(path.join(dir,'browser_validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }else{
  fs.writeFileSync(path.join(dir,'browser_'+mode+'_stats.json'),JSON.stringify(stats,null,2));
  console.log(JSON.stringify({mode,markers:stats.visibleMarkers,buys:stats.buyMarkers,sells:stats.sellMarkers,strokes:stats.strokes.length}));
 }
 const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
 fs.writeFileSync(path.join(dir,'chart_'+mode+'.png'),Buffer.from(shot.data,'base64'));
 fs.writeFileSync(path.join(dir,'browser_'+mode+'.log'),logs);
}catch(e){console.error(e);process.exitCode=1;fs.writeFileSync(path.join(dir,'browser_'+mode+'.log'),logs)}
finally{ws?.close();chrome.kill();await delay(100);fs.rmSync(profile,{recursive:true,force:true})}
