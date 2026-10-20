// Read-only production verification. Does not draw, save or change annotations.
const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../..'),out=path.resolve(__dirname,'../reports/real_bounds_remote');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,proxy:{server:'socks5://127.0.0.1:8207'}});
 const ctx=await browser.newContext({viewport:{width:1440,height:1050}}),page=await ctx.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const idle=()=>page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.goto('https://wine.77-105-169-21.sslip.io/admin/');
 const password=JSON.parse(fs.readFileSync(path.join(root,'work/wine_server_private/credentials.json'))).admin_password;
 await page.locator('#password').fill(password);await page.locator('#login-form button').click();
 await page.locator('#application').waitFor({state:'visible'});await idle();
 await page.locator('[data-tab=bounds]').click();await idle();
 const counters={total:await page.locator('#bounds-total').textContent(),labeled:await page.locator('#bounds-labeled').textContent(),remaining:await page.locator('#bounds-remaining').textContent()};
 assert.equal(counters.total,'626');assert(Number(counters.labeled)>=47);assert.equal(Number(counters.labeled)+Number(counters.remaining),626);
 assert.equal(await page.locator('#bounds-photo-set').inputValue(),'real');
 assert.equal(await page.locator('#bounds-filter').inputValue(),'unlabeled');
 const expected=new Set(JSON.parse(fs.readFileSync(path.join(root,'work/wine_real_bounds/real_photo_selection.json'))).items.map(r=>r.image_id));
 const ids=await page.evaluate(()=>boundaryItems.map(r=>r.id));assert.equal(ids.length,Number(counters.remaining));assert(ids.every(id=>expected.has(id)));
 const checks=[];
 for(const width of [1440,1024,390,360]){
  await page.setViewportSize({width,height:1050});await page.screenshot({path:path.join(out,`real-${width}.png`),fullPage:true});
  const measure=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,canvas:document.querySelector('#bounds-editor canvas').getBoundingClientRect().toJSON()}));
  assert(measure.scroll<=width&&measure.canvas.width>100);checks.push(measure);
 }
 await page.locator('#bounds-filter').selectOption('labeled');await idle();assert.equal(await page.evaluate(()=>boundaryItems.length),Number(await page.locator('#bounds-labeled').textContent()));
 assert.equal(await page.evaluate(()=>boundsEditor.points.length),4);
 await page.locator('#bounds-filter').selectOption('all');await idle();assert.equal(await page.evaluate(()=>boundaryItems.length),626);
 await page.locator('#bounds-filter').selectOption('unlabeled');await idle();
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,counters,checks,errors,source_data_mutations:false},null,2));
 await browser.close();console.log('PASS: live admin, exact 626-photo membership, live counts, real default, saved boundaries load, four viewport widths.');
})().catch(e=>{console.error(e);process.exit(1)});
