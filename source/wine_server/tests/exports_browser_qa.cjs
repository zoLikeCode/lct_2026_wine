const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const out=path.resolve(__dirname,'../reports/exports_browser');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true,reducedMotion:'reduce'}),page=await ctx.newPage();
 ctx.setDefaultTimeout(20000);const errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
 const base='http://127.0.0.1:8199';await page.goto(base+'/admin/');await page.locator('#password').fill('test-password');await page.locator('#login-form button').click();await page.locator('#application').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.body.classList.contains('busy'));await page.locator('[data-tab=export]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 // Annotate only the isolated synthetic fixture; never run this mutation against production.
 await ctx.request.post(base+'/admin/api/boundaries/0',{data:{points:[[.15,.2],[.85,.8]]}});
 await page.locator('[data-tab=export]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 const existing=await(await ctx.request.get(base+'/admin/api/exports')).json();for(const j of existing.items)if(['ready','error'].includes(j.status))await ctx.request.post(base+'/admin/api/exports/'+j.id+'/delete',{data:{}});
 await page.locator('#export-refresh').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.locator('[name=export-scope][value=selected]').check();assert(await page.locator('#export-folder-picker').isVisible());
 await page.locator('#export-search').fill('Мерло');await page.locator('#export-folders input[value=merlo]').check();
 await page.locator('#export-search').fill('Каберне');await page.locator('#export-folders input[value=cabernet]').check();
 assert.equal(await page.locator('#export-selected-count').textContent(),'Выбрано: 2');
 await page.locator('#export-search').fill('несуществующее вино');assert(await page.locator('#export-folders').textContent().then(t=>t.includes('Папки не найдены')));
 await page.locator('#export-search').fill('');assert(await page.locator('#export-folders input[value=merlo]').isChecked());
 await page.locator('#export-mode').selectOption('images_json');await page.waitForFunction(()=>!document.querySelector('#export-build').disabled);
 async function shot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});const d=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert(d.scroll<=d.width,name+' overflow');checks.push({name,...d})}
 for(const width of [1440,1024,390,360]){await page.setViewportSize({width,height:960});await shot('selected-'+width)}
 await page.setViewportSize({width:1440,height:960});await page.locator('#export-build').click();await page.locator('#export-jobs a').first().waitFor({timeout:15000});
 let downloadPromise=page.waitForEvent('download');await page.locator('#export-jobs a').first().click();let downloaded=await downloadPromise;await downloaded.saveAs(path.join(out,'photos-json.zip'));
 for(const width of [1440,1024,390,360]){await page.setViewportSize({width,height:960});await shot('ready-'+width)}
 await page.locator('#export-mode').selectOption('json');assert(await page.locator('#export-filter').isDisabled());assert.equal(await page.locator('#export-filter').inputValue(),'labeled');
 await page.waitForFunction(()=>!document.querySelector('#export-build').disabled&&document.querySelector('#export-count-images').textContent==='0');
 await page.locator('#export-build').click();await page.waitForFunction(()=>document.querySelectorAll('#export-jobs a').length===2);
 downloadPromise=page.waitForEvent('download');await page.locator('#export-jobs a').first().click();downloaded=await downloadPromise;await downloaded.saveAs(path.join(out,'annotations-only.zip'));
 await page.locator('#export-jobs .export-delete').first().click();await page.waitForFunction(()=>document.querySelectorAll('#export-jobs a').length===1);
 await page.locator('#export-mode').selectOption('images');await page.locator('#export-filter').selectOption('unlabeled');await page.waitForFunction(()=>!document.querySelector('#export-preview-status').textContent.includes('Считаем'));
 await page.locator('#export-clear').click();await page.waitForFunction(()=>document.querySelector('#export-build').disabled);assert.equal(await page.locator('#export-selected-count').textContent(),'Выбрано: 0');
 await page.locator('[name=export-scope][value=all]').check();await page.locator('#export-filter').selectOption('all');await page.waitForFunction(()=>!document.querySelector('#export-build').disabled);
 // A recoverable server failure must leave the action usable.
 await page.route('**/admin/api/exports',async route=>route.request().method()==='POST'?route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'Тест: временно недостаточно места'})}):route.continue());
 await page.locator('#export-build').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));assert(!(await page.locator('#export-build').isDisabled()));await page.unroute('**/admin/api/exports');
 await page.keyboard.press('Tab');await page.locator('#export-build').focus();assert.equal(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle),'solid');
 await page.locator('[data-tab=bounds]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.locator('#bounds-slug').selectOption('merlo');await page.locator('#bounds-filter').selectOption('labeled');await page.locator('#open-exports').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 assert(await page.locator('#export-view').isVisible());assert.equal(await page.locator('#export-selected-count').textContent(),'Выбрано: 1');assert.equal(await page.locator('#export-filter').inputValue(),'labeled');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,errors,checks,interactions:['multi-folder search and persistent selection','empty search','download photos + JSON','download JSON only','delete archive','unlabeled filter','empty selection','all folders','retry after server error','keyboard focus','shortcut from label editor']},null,2));
 await browser.close();console.log('PASS: export UI, downloads and '+checks.length+' viewport checks');
})().catch(e=>{console.error(e);process.exit(1)});
