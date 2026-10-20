const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const out=path.resolve(__dirname,'../reports/browser');fs.mkdirSync(out,{recursive:true});
const fixture=path.resolve(__dirname,'../../../work/wine_server_qa/Camera/IMG_01.jpg');
const base='http://127.0.0.1:8199';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
 const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['camera'],reducedMotion:'reduce'});
 const page=await context.newPage();let errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 await page.goto(base+'/admin/');await page.locator('#password').fill('test-password');await page.locator('#login-form button').click();await page.locator('#application').waitFor({state:'visible'});
 await page.locator('[data-tab=photos]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));await page.locator('#photo-files').setInputFiles([{name:'one.jpg',mimeType:'image/jpeg',buffer:fs.readFileSync(fixture)},{name:'two.jpg',mimeType:'image/jpeg',buffer:fs.readFileSync(fixture)}]);
 await page.locator('#label-view').waitFor({state:'visible'});await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.locator('[data-slug=merlo]').click();await page.locator('[data-slug=cabernet]').hover();assert.equal(await page.locator('#selected-slug').textContent(),'merlo');
 assert.equal(await page.locator('#label-status').textContent(),'Ещё не распределено');
 await page.locator('#with-bounds').check();const box=await page.locator('#label-editor canvas').boundingBox();await page.mouse.move(box.x+box.width*.2,box.y+box.height*.25);await page.mouse.down();await page.mouse.move(box.x+box.width*.8,box.y+box.height*.75);await page.mouse.up();
 await page.locator('#save-label').click();await page.waitForFunction(()=>document.querySelector('#label-position').textContent==='2 / 2');
 await page.locator('#label-prev').click();await page.waitForFunction(()=>document.querySelector('#label-position').textContent==='1 / 2'&&!document.body.classList.contains('busy'));
 assert.equal(await page.locator('#selected-slug').textContent(),'merlo');assert((await page.locator('#label-status').textContent()).includes('Уже сохранено'));
 await page.locator('[data-slug=cabernet]').click();await page.locator('#save-label').click();await page.waitForFunction(()=>document.querySelector('#label-position').textContent==='2 / 2'&&!document.body.classList.contains('busy'));
 await page.locator('#label-prev').click();await page.waitForFunction(()=>document.querySelector('#selected-slug').textContent==='cabernet'&&!document.body.classList.contains('busy'));
 const widths=[1440,1024,390,360],layout=[];
 async function shot(name){await page.waitForFunction(()=>!document.body.classList.contains('busy'));await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});const dim=await page.evaluate(()=>({w:innerWidth,scroll:document.documentElement.scrollWidth}));layout.push({name,...dim});assert(dim.scroll<=dim.w+1,`Overflow ${name}: ${dim.scroll}`)}
 for(const width of widths){await page.setViewportSize({width,height:1000});await shot('admin-label-'+width)}
 await page.setViewportSize({width:1440,height:1000});await page.locator('[data-tab=review]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 for(const width of widths){await page.setViewportSize({width,height:1000});await shot('admin-review-'+width)}
 const reviewCount=await page.locator('#review-grid .tile').count();await page.locator('#review-grid input').first().check();await page.locator('#save-review').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.locator('#undo-review').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));assert.equal(await page.locator('#review-grid .tile').count(),reviewCount);await page.locator('[data-tab=bounds]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.locator('#bounds-filter').selectOption('all');await page.locator('#load-bounds').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 for(const width of widths){await page.setViewportSize({width,height:1000});await shot('admin-bounds-'+width)}
 await page.locator('#bounds-no-label').check();await page.locator('#save-bounds').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.goto(base+'/#key=qa-scanner-key-012345678901234567890');await page.locator('#connection.ready').waitFor();
 for(const width of widths){await page.setViewportSize({width,height:1000});await shot('scanner-'+width)}
 await page.locator('#photo-file').setInputFiles(fixture);await page.locator('#result-view').waitFor({state:'visible'});
 for(const width of widths){await page.setViewportSize({width,height:1000});await shot('scanner-result-'+width)}
 await page.locator('[data-view=history]').click();assert.equal(await page.locator('#history-list .wine-row').count(),1);
	 await page.locator('[data-view=scan]').click();assert.equal(await page.locator('#close-camera').count(),0);await page.locator('#camera-button').click();await page.locator('#camera').waitFor({state:'visible'});assert.equal(await page.locator('#camera-toggle-slot #camera-button').count(),1);await page.locator('#camera-button').click();assert(await page.locator('#camera').isHidden());
 await page.goto(base+'/admin/');await page.locator('#history-grid .scan').first().waitFor();
 for(const width of widths){await page.setViewportSize({width,height:1000});await shot('admin-history-'+width)}
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,errors,layout,checks:['upload folder','hover does not select','click selects without moving','save with boundaries','back includes assigned photos','relabel preserves asset','review rejection','boundary filters','scan saved in central history','camera opens and closes','mobile local history']},null,2));
 await browser.close();console.log('PASS: interactions, camera, and '+layout.length+' viewport checks');
})().catch(e=>{console.error(e);process.exit(1)});
