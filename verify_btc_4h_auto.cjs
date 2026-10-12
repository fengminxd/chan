// Real Chromium verification. Arguments: output directory, Playwright module, browser executable.
const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const dir=path.resolve(process.argv[2]||'reports/BTCUSDT_4h_20251006_current');
const {chromium}=require(process.argv[3]||'playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.argv[4]?{executablePath:process.argv[4]}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(pathToFileURL(path.join(dir,'btc_4h.html')).href);
    await page.waitForFunction(()=>window.chartReady===true);
    const payload=JSON.parse(fs.readFileSync(path.join(dir,'chart_data.json'),'utf8'));
    assert.deepStrictEqual(payload.manual_annotations,[]);
    const checks=[];
    for(const basis of ['strokes','types']){
      await page.selectOption('#basis',basis);
      await page.click('#reset');
      const v=payload.views[basis];
      const stats=await page.evaluate(()=>window.chartStats);
      assert.equal(stats.bars,payload.bars.length);
      assert.equal(stats.signals,v.signals.length);
      assert.equal(stats.divergences,v.divergences.length);
      assert.equal(stats.markers.length,v.signals.length+v.divergences.length);
      assert(stats.markers.every(m=>Number.isFinite(m.x)&&Number.isFinite(m.y)));
      assert.equal(await page.locator('#signalRows tr').count(),Math.max(1,v.signals.length));
      assert.equal(await page.locator('#divRows tr').count(),Math.max(1,v.divergences.length));
      const colored=await page.locator('#chart').evaluate(c=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let count=0;for(let i=0;i<d.length;i+=4)if(Math.max(d[i],d[i+1],d[i+2])-Math.min(d[i],d[i+1],d[i+2])>45)count++;return count});
      assert(colored>5000,'Canvas must contain candles and colored annotations');
      await page.locator('.panel').first().screenshot({path:path.join(dir,basis+'_full.png')});
      for(const label of ['B1','B2','B3','S1','S2','S3','all']){
        await page.selectOption('#signalFilter',label);
        const expected=v.signals.filter(s=>label==='all'||s.label===label).length;
        assert.equal(await page.locator('#signalRows tr').count(),Math.max(1,expected));
      }
      if(v.divergences.length){await page.locator('#divRows tr').first().click();assert(await page.locator('#detailPanel').isVisible());assert((await page.locator('#detail').textContent()).includes('referenced_center'));}
      for(const days of ['180','90','30','7','all'])await page.click(`[data-days="${days}"]`);
      for(const layer of ['strokes','segments','centers','signals','divergences']){await page.uncheck('#'+layer);await page.check('#'+layer)}
      await page.click('[data-days="30"]');
      await page.locator('.panel').first().screenshot({path:path.join(dir,basis+'_recent30d.png')});
      checks.push({basis,...stats,colored_pixels:colored});
    }
    await page.setViewportSize({width:390,height:844});
    await page.click('#reset');
    assert.equal((await page.evaluate(()=>window.chartStats)).bars,payload.bars.length);
    assert.deepStrictEqual(errors,[]);
    const result={status:'passed',browser:browser.version(),checks,errors,
      coverage:['both structural bases','actual colored canvas pixels','signal and divergence counts','all signal filters','divergence details','date windows','layer visibility','mobile redraw'],manual_annotations:0};
    fs.writeFileSync(path.join(dir,'browser_validation.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify({status:result.status,browser:result.browser,views:checks.map(x=>({basis:x.basis,bars:x.bars,signals:x.signals,divergences:x.divergences})),errors}));
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
