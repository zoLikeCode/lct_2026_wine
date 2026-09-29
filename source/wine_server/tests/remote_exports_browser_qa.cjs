const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../..'),out=path.resolve(__dirname,'../reports/remote_exports_browser');fs.mkdirSync(out,{recursive:true});
const credentials=JSON.parse(fs.readFileSync(path.join(root,'work/wine_server_private/credentials.json')));
(async()=>{
 const browser=await chromium.launch({headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:960},reducedMotion:'reduce'}),page=await ctx.newPage();ctx.setDefaultTimeout(20000);
 const errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('https://wine.77-105-169-21.sslip.io/admin/');await page.locator('#password').fill(credentials.admin_password);await page.locator('#login-form button').click();await page.locator('#application').waitFor({state:'visible'});await page.waitForFunction(()=>!document.body.classList.contains('busy'));
 await page.locator('[data-tab=export]').click();await page.waitForFunction(()=>!document.body.classList.contains('busy'));assert.equal(await page.locator('#export-count-folders').textContent(),'2 103');
 async function shots(name){for(const width of [1440,1024,390,360]){await page.setViewportSize({width,height:960});await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:true,animations:'disabled'});const d=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert(d.scroll<=d.width,name+' overflow '+width);checks.push({name,...d})}}
 await shots('all-images');await page.locator('[name=export-scope][value=selected]').check();await page.locator('#export-search').fill('100 оттенков');await page.locator('#export-folders input').first().check();await page.locator('#export-folders input').nth(1).check();await page.locator('#export-mode').selectOption('images_json');await page.waitForFunction(()=>document.querySelector('#export-count-folders').textContent==='2');await shots('selected-folders');
 await page.locator('#export-mode').selectOption('json');assert(await page.locator('#export-filter').isDisabled());await page.waitForFunction(()=>document.querySelector('#export-count-images').textContent==='0');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,admin_data_writes:false,errors,checks},null,2));await browser.close();console.log('PASS: real-server export UI, '+checks.length+' viewport checks, no data mutations');
})().catch(e=>{console.error(e);process.exit(1)});
