const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const out=path.resolve(__dirname,'../reports/real_bounds_browser');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});
 const ctx=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
 const page=await ctx.newPage(),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
 const idle=()=>page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.goto('http://127.0.0.1:8206/admin/');await page.locator('#password').fill('test-password');
 await page.locator('#login-form button').click();await page.locator('#application').waitFor({state:'visible'});await idle();
 await page.locator('[data-tab=bounds]').click();await idle();
 assert.equal(await page.locator('#bounds-photo-set').inputValue(),'real');
 assert.equal(await page.locator('#bounds-filter').inputValue(),'unlabeled');
 assert.equal(await page.locator('#bounds-total').textContent(),'3');
 assert.equal(await page.locator('#bounds-labeled').textContent(),'1');
 assert.equal(await page.locator('#bounds-remaining').textContent(),'2');
 const first=await page.evaluate(()=>boundaryItems[boundaryIndex].id);
 for(const width of [1440,1024,390,360]){
  await page.setViewportSize({width,height:1050});await page.screenshot({path:path.join(out,`bounds-${width}.png`),fullPage:true});
  const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
    canvas:document.querySelector('#bounds-editor canvas').getBoundingClientRect().toJSON(),
    controls:[...document.querySelectorAll('#bounds-view select,#bounds-view button')].map(e=>({id:e.id,...e.getBoundingClientRect().toJSON()}))}));
  assert(layout.scroll<=width,`Overflow at ${width}`);assert(layout.canvas.width>100&&layout.canvas.height>100);
  assert(layout.controls.every(r=>r.width>0&&r.height>=44&&r.x>=0&&r.right<=width),JSON.stringify(layout));checks.push(layout);
 }
 await page.setViewportSize({width:1440,height:1050});
 // Draw a rectangle through the real canvas interaction.
 const canvas=await page.locator('#bounds-editor canvas').boundingBox();
 await page.mouse.move(canvas.x+canvas.width*.2,canvas.y+canvas.height*.2);await page.mouse.down();
 await page.mouse.move(canvas.x+canvas.width*.8,canvas.y+canvas.height*.8);await page.mouse.up();
 assert(await page.evaluate(()=>boundsEditor.dirty));
 page.once('dialog',d=>d.dismiss());await page.locator('#bounds-photo-set').selectOption('all');await idle();
 assert.equal(await page.locator('#bounds-photo-set').inputValue(),'real');assert(await page.evaluate(()=>boundsEditor.dirty));
 await page.locator('#save-bounds').click();await idle();
 assert.equal(await page.locator('#bounds-remaining').textContent(),'1');
 await page.locator('#bounds-prev').click();await idle();assert.equal(await page.evaluate(()=>boundaryItems[boundaryIndex].id),first);
 assert.equal(await page.evaluate(()=>boundsEditor.points.length),4);assert.equal(await page.evaluate(()=>boundaryRevision),1);
 await page.locator('#save-bounds').click();await idle();assert.equal(await page.locator('#bounds-remaining').textContent(),'1');
 await page.locator('#bounds-no-label').check();await page.locator('#save-bounds').click();await idle();
 assert.equal(await page.locator('#bounds-remaining').textContent(),'0');assert(!(await page.locator('#bounds-prev').isDisabled()));
 assert(await page.locator('#save-bounds').isDisabled());
 await page.locator('#bounds-prev').click();await idle();assert(await page.locator('#bounds-no-label').isChecked());
 await page.locator('#load-bounds').click();await idle();assert(await page.locator('#bounds-editor').isHidden());
 await page.screenshot({path:path.join(out,'empty.png'),fullPage:true});
 await page.locator('#bounds-filter').selectOption('labeled');await idle();assert.equal(await page.evaluate(()=>boundaryItems.length),3);
 await page.locator('#bounds-slug').selectOption('cabernet');await idle();assert.equal(await page.locator('#bounds-total').textContent(),'1');
 await page.locator('#bounds-slug').selectOption('');await idle();await page.locator('#bounds-filter').selectOption('all');await idle();
 await page.locator('#bounds-photo-set').selectOption('all');await idle();assert.equal(await page.locator('#bounds-total').textContent(),'4');
 assert.equal(await page.evaluate(()=>boundaryItems.length),4);
 await page.locator('#bounds-photo-set').focus();await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'bounds-filter');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,checks,errors,source_data_mutations:false},null,2));
 await browser.close();console.log('PASS: real-photo default, saved/unlabeled/all/slug filters, counts, draw/save/back/correction, empty state, dirty protection, four widths.');
})().catch(e=>{console.error(e);process.exit(1)});
