const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const dir=path.resolve(process.argv[2]||'reports/SUIUSDT_100d_current');
const {chromium}=require(process.argv[3]||'playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.argv[4]?{executablePath:process.argv[4]}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1150}}),errors=[],checks=[];
    page.on('pageerror',e=>errors.push(e.message));
    const payload=JSON.parse(fs.readFileSync(path.join(dir,'chart_data.json'),'utf8'));
    assert.equal(Date.parse(payload.asof)-Date.parse(payload.start),100*86400000);
    await page.goto(pathToFileURL(path.join(dir,payload.symbol.slice(0,-4).toLowerCase()+'.html')).href);
    await page.waitForFunction(()=>window.chartReady===true);
    assert((await page.title()).includes(payload.symbol));
    assert((await page.locator('#quoteInfo').textContent()).includes(payload.symbol));
    assert.equal(await page.inputValue('#period'),'4h');
    assert.equal(await page.inputValue('#basis'),'types');
    assert.equal(await page.inputValue('#history'),'recent');
    assert((await page.locator('#quoteInfo').textContent()).includes(Number(payload.source.ticker.lastPrice).toFixed(payload.price_decimals)));
    for(const history of ['recent','full'])for(const period of ['1h','4h'])for(const basis of ['types','strokes']){
      await page.selectOption('#history',history);await page.selectOption('#period',period);await page.selectOption('#basis',basis);await page.click('#reset');
      const pd=payload.histories[history].periods[period],v=pd.views[basis];
      let stats=await page.evaluate(()=>window.chartStats);
      assert.equal(stats.history,history);assert.equal(stats.period,period);assert.equal(stats.basis,basis);
      assert.equal(stats.bars,pd.bars.length);assert.equal(stats.signals,v.signals.length);assert.equal(stats.divergences,v.divergences.length);
      assert.equal(stats.markers.length,v.signals.length+v.divergences.length);
      assert(stats.markers.every(m=>Number.isFinite(m.x)&&Number.isFinite(m.y)));
      assert((await page.locator('#sourceInfo').textContent()).includes('完整'+period));
      assert.equal(await page.locator('#signalRows tr').count(),Math.max(1,v.signals.length));
      for(const signal of ['B1','B2','B3','S1','S2','S3','all']){
        await page.selectOption('#signalFilter',signal);
        assert.equal(await page.locator('#signalRows tr').count(),Math.max(1,v.signals.filter(s=>signal==='all'||s.label===signal).length));
      }
      if(v.signals.length){await page.locator('#signalRows tr').first().click();assert(await page.locator('#detailPanel').isVisible());}
      await page.click('#reset');
      const colored=await page.locator('#chart').evaluate(c=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(Math.max(d[i],d[i+1],d[i+2])-Math.min(d[i],d[i+1],d[i+2])>40)n++;return n});
      assert(colored>3000);
      if(history==='recent')await page.locator('.panel').nth(1).screenshot({path:path.join(dir,period+'_'+basis+'_100d.png')});
      for(const days of ['60','30','7','all'])await page.click(`[data-days="${days}"]`);
      for(const layer of ['strokes','segments','centers','signals','divergences']){await page.uncheck('#'+layer);await page.check('#'+layer);}
      await page.click('[data-days="7"]');
      if(history==='recent'&&basis==='strokes')await page.locator('.panel').nth(1).screenshot({path:path.join(dir,period+'_recent7d.png')});
      checks.push({history,period,basis,...stats,colored_pixels:colored});
    }
    await page.setViewportSize({width:390,height:844});
    await page.selectOption('#history','recent');await page.selectOption('#period','1h');await page.selectOption('#basis','types');await page.click('#reset');
    assert.equal((await page.evaluate(()=>window.chartStats)).bars,payload.histories.recent.periods['1h'].bars.length);
    await page.screenshot({path:path.join(dir,'mobile.png')});
    assert.deepStrictEqual(errors,[]);
    const result={status:'passed',browser:browser.version(),errors,checks};
    fs.writeFileSync(path.join(dir,'browser_validation.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify({status:result.status,browser:result.browser,combinations:checks.length,errors}));
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
