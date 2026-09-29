const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const remote=process.env.WINE_QA_REMOTE==='1',root=path.resolve(__dirname,'../../..');
const out=path.resolve(__dirname,'../reports/formats_browser'+(remote?'_remote':''));fs.mkdirSync(out,{recursive:true});
const base=remote?'https://wine.77-105-169-21.sslip.io':'http://127.0.0.1:8202';
(async()=>{
 const browser=await chromium.launch({headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true,reducedMotion:'reduce'}),page=await ctx.newPage();
 const errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));ctx.setDefaultTimeout(30000);
 const password=remote?JSON.parse(fs.readFileSync(path.join(root,'work/wine_server_private/credentials.json'))).admin_password:'test-password';
 await page.goto(base+'/admin/');await page.locator('#password').fill(password);await page.locator('#login-form button').click();await page.locator('#application').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.body.classList.contains('busy'));await page.locator('[data-tab=export]').click();
 await page.waitForFunction(()=>!document.body.classList.contains('busy')&&!document.querySelector('#export-preview-status').textContent.includes('Считаем'));
 assert.equal(await page.locator('#export-format').inputValue(),'coco');assert.equal(await page.locator('#export-filter').inputValue(),'labeled');assert(await page.locator('#export-filter').isDisabled());
 if(remote){
  const catalog=await(await ctx.request.get(base+'/admin/api/exports/catalog')).json();const wine=catalog.items.filter(x=>x.labeled).sort((a,b)=>a.labeled-b.labeled)[0];assert(wine,'Need an existing annotation');
  await page.locator('[name=export-scope][value=selected]').check();await page.locator('#export-search').fill(wine.slug);await page.locator('#export-folders input').first().check();
 }
 async function shots(state){for(const width of [1440,1024,390,360]){
  await page.setViewportSize({width,height:960});await page.screenshot({path:path.join(out,`${state}-${width}.png`),fullPage:true,animations:'disabled'});
  const metrics=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,selects:[...document.querySelectorAll('#export-view select')].map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))}));
  assert(metrics.scroll<=metrics.width,`${state} overflow ${width}`);assert(metrics.selects.every(s=>s.width>0&&s.height>=40));checks.push({state,...metrics});
 }}
 await shots('coco');await page.setViewportSize({width:1440,height:960});
 for(const fmt of ['coco','yolo_detect','yolo_segment']){
  await page.locator('#export-format').selectOption(fmt);await page.waitForFunction(()=>!document.querySelector('#export-build').disabled);
  const created=page.waitForResponse(r=>r.url().endsWith('/admin/api/exports')&&r.request().method()==='POST');await page.locator('#export-build').click();const job=await(await created).json();assert(job.id);
  await page.waitForFunction(id=>['ready','error'].includes(exportJobs.find(j=>j.id===id)?.status),job.id,{timeout:60000});
  const finished=await page.evaluate(id=>exportJobs.find(j=>j.id===id),job.id);assert.equal(finished.status,'ready',finished.error);
  const link=page.locator(`a[href="/admin/api/exports/${job.id}/download"]`);await link.waitFor({timeout:60000});
  const pending=page.waitForEvent('download');await link.click();const dl=await pending;await dl.saveAs(path.join(out,fmt+'.zip'));
  if(fmt==='yolo_segment')await shots('ready');
  await ctx.request.post(base+'/admin/api/exports/'+job.id+'/delete',{data:{}});await page.locator('#export-refresh').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 }
 await page.locator('#export-mode').selectOption('json');assert(await page.locator('#export-filter').isDisabled());await page.waitForFunction(()=>document.querySelector('#export-count-images').textContent==='0');
 await page.locator('#export-split').selectOption('grouped');await page.waitForFunction(()=>document.querySelector('#export-preview-status').textContent.includes('три независимые'));assert(await page.locator('#export-build').isDisabled());
 await shots('split-error');await page.locator('#export-split').selectOption('none');
 await page.locator('#export-format').selectOption('native');await page.locator('#export-mode').selectOption('images_json');assert(!(await page.locator('#export-filter').isDisabled()));
 await page.locator('#export-filter').selectOption('all');await page.locator('#export-mode').selectOption('images');assert(await page.locator('#export-format').isDisabled());
 await page.locator('#export-filter').selectOption('unlabeled');await page.waitForFunction(()=>!document.querySelector('#export-preview-status').textContent.includes('Считаем'));
 await page.locator('#export-mode').selectOption('images_json');await page.locator('#export-format').selectOption('coco');assert.equal(await page.locator('#export-filter').inputValue(),'labeled');
 await page.locator('#export-format').focus();await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'export-split');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,remote,errors,checks,source_data_mutations:false},null,2));await browser.close();console.log('PASS: standard export formats, downloads, negative/error states, four viewport sizes');
})().catch(e=>{console.error(e);process.exit(1)});
