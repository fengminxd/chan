// Execute the actual chart script against a recording Canvas adapter.
// This checks draw/filter/navigation paths and exports matching standalone SVGs.
const fs=require('fs'),vm=require('vm'),path=require('path');
const scope=process.argv[2]||'4h';
const dir=__dirname,html=fs.readFileSync(path.join(dir,'btc_4h.html'),'utf8');
const data=html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const code=html.slice(html.lastIndexOf('<script>')+8,html.lastIndexOf('</script>'));
const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
let parts=[],defs=[],state={},stack=[],current='';
function coord(n){if(!Number.isFinite(n))throw Error('Nonfinite coordinate '+n);return +n.toFixed(3)}
function style(fill=false){return `fill="${fill?esc(state.fillStyle):'none'}" stroke="${fill?'none':esc(state.strokeStyle)}" stroke-width="${state.lineWidth||1}" opacity="${state.globalAlpha??1}" ${state.dash?.length?`stroke-dasharray="${state.dash.join(',')}"`:''} ${state.clip?`clip-path="url(#${state.clip})"`:''}`}
const api={setTransform(){parts=[];defs=[];state={fillStyle:'#000',strokeStyle:'#000',lineWidth:1,globalAlpha:1};stack=[]},save(){stack.push({...state})},restore(){state=stack.pop()},beginPath(){current=''},moveTo(x,y){current+=`M${coord(x)},${coord(y)} `},lineTo(x,y){current+=`L${coord(x)},${coord(y)} `},rect(x,y,w,h){current+=`M${coord(x)},${coord(y)} h${coord(w)} v${coord(h)} h${coord(-w)} Z `},closePath(){current+='Z '},clip(){state.clip='c'+defs.length;defs.push(`<clipPath id="${state.clip}"><path d="${current}"/></clipPath>`)},stroke(){parts.push(`<path d="${current}" ${style()}/>`);},fill(){parts.push(`<path d="${current}" ${style(true)}/>`);},setLineDash(d){state.dash=d},fillRect(x,y,w,h){parts.push(`<rect x="${coord(x)}" y="${coord(y)}" width="${coord(w)}" height="${coord(h)}" ${style(true)}/>`);},strokeRect(x,y,w,h){parts.push(`<rect x="${coord(x)}" y="${coord(y)}" width="${coord(w)}" height="${coord(h)}" ${style()}/>`);},fillText(t,x,y){const font=state.font||'11px system-ui',size=font.match(/(\d+)px/)[1];parts.push(`<text x="${coord(x)}" y="${coord(y)}" fill="${esc(state.fillStyle)}" font-size="${size}" font-weight="${font.includes('bold')?'bold':'normal'}" font-family="DejaVu Sans, WenQuanYi Zen Hei, sans-serif" text-anchor="${state.textAlign==='right'?'end':state.textAlign==='center'?'middle':'start'}" ${state.clip?`clip-path="url(#${state.clip})"`:''}>${esc(t)}</text>`);}};
const ctx=new Proxy(api,{get(o,k){return k in o?o[k]:state[k]},set(o,k,v){state[k]=v;return true}});
class Element{constructor(id=''){this.id=id;this.value=['level','signalFilter'].includes(id)?'all':'';this.checked=true;this.children=[];this.style={};this.dataset={};this.clientWidth=1600;this.clientHeight=690;this.handlers={};this.classList={add(){},remove(){}}}append(c){this.children.push(c)}replaceChildren(){this.children=[]}getContext(){return ctx}addEventListener(k,f){this.handlers[k]=f}setPointerCapture(){}scrollIntoView(){}click(){this.onclick?.()}}
const map=new Map(),buttons=['all','90','30','7'].map(v=>{const e=new Element();e.dataset.days=v;return e});
global.document={getElementById(id){if(!map.has(id))map.set(id,new Element(id));return map.get(id)},createElement(){return new Element()},querySelectorAll(){return buttons}};
document.getElementById('data').textContent=data;global.window={devicePixelRatio:1};global.location={search:'?scope='+scope};
vm.runInThisContext(code,{filename:'btc_4h_chart.js'});
function exportSVG(name){fs.writeFileSync(path.join(dir,scope+'_'+name),`<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="690" viewBox="0 0 1600 690"><defs>${defs.join('')}</defs>${parts.join('')}</svg>`)}
if(!window.chartReady)throw Error('Chart failed initialization');
const expected=JSON.parse(data)[scope].signals.length;if(map.get('rows').children.length!==Math.max(1,expected))throw Error('Missing signals');
exportSVG('btc_4h_full.svg');buttons[1].onclick();buttons[2].onclick();exportSVG('btc_4h_recent30d.svg');buttons[3].onclick();exportSVG('btc_4h_recent7d.svg');
for(const label of ['B1','S1','B2','S2','B3','S3','all']){map.get('signalFilter').value=label;map.get('signalFilter').onchange()}
if(expected)map.get('rows').children[0].onclick();
for(const id of ['strokes','segments','centers','signals']){map.get(id).checked=false;map.get(id).onchange();map.get(id).checked=true;map.get(id).onchange()}
map.get('level').value='1';map.get('level').onchange();map.get('level').value='all';map.get('level').onchange();
const canvas=map.get('chart');canvas.handlers.wheel({preventDefault(){},offsetX:800,deltaY:-100});canvas.handlers.pointerdown({clientX:800,pointerId:1});canvas.handlers.pointermove({clientX:850,offsetX:850,offsetY:300});canvas.handlers.pointerup();canvas.handlers.pointermove({offsetX:800,offsetY:300});map.get('reset').onclick();
fs.writeFileSync(path.join(dir,scope+'_chart_validation.json'),JSON.stringify({script_execution:'passed',signals_rendered:expected,finite_coordinates:true,checks:['full/90/30/7 day ranges','signal type filters','level filters','layer switches','signal location','wheel zoom','drag pan','hover','reset'],scope:'Node DOM/Canvas adapter; not a browser screenshot'},null,2));
console.log('PASS: chart script, '+expected+' signal rows, controls, and SVG exports');
