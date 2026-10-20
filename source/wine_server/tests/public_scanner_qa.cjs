const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../..');
const remote=process.argv.includes('--remote');
const base=remote?'https://wine.77-105-169-21.sslip.io':'http://127.0.0.1:8199';
const input=remote?JSON.parse(fs.readFileSync(path.join(root,'work/wine_server_private/benchmark_input.json'))).path:path.join(root,'work/wine_server_qa/Camera/IMG_01.jpg');
const out=path.resolve(__dirname,'../reports/public_scanner_20260928',remote?'remote':'local');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,proxy:process.env.WINE_QA_PROXY?{server:process.env.WINE_QA_PROXY}:undefined,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
 try {
  const context=await browser.newContext({viewport:{width:390,height:844},permissions:['camera'],reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[],checks=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push({path:new URL(r.url()).pathname,authorization:!!r.headers().authorization});});
  const shots=async name=>{
   for(const width of [1440,1024,390,360]){
    await page.setViewportSize({width,height:width<500?844:960});
    await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:true,animations:'disabled'});
    const d=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert(d.scroll<=d.width+1,`${name} overflow ${width}`);checks.push({name,...d});
   }
  };
  await page.goto(base+'/');await page.locator('#connection.ready').waitFor({timeout:60000});
  assert.equal(await page.locator('#access-dialog').count(),0);
  assert.equal(await page.evaluate(()=>localStorage.getItem('wine-access')),null);
  assert.equal(await page.evaluate(async()=>(await fetch('/admin/api/history')).status),401);
  assert.equal(await page.evaluate(()=>isSecureContext),true);
  await page.evaluate(()=>Promise.race([navigator.serviceWorker.ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('Service worker not ready after 45 seconds')),45000))]));
  const manifest=await page.evaluate(async()=>await(await fetch('/manifest.webmanifest')).json());
  assert.equal(manifest.start_url,'/');assert.equal(manifest.display,'standalone');assert(manifest.icons.some(i=>i.sizes==='512x512'));
  await shots('scanner');console.log('Scanner ready without key; PWA resources loaded');

  // The OS install sheet is unavailable headlessly. Check both UI paths explicitly.
  await page.evaluate(()=>{installPrompt=null;});
  await page.locator('#install-button').click();await page.locator('#install-dialog[open]').waitFor();
  assert(!/личную ссылку|код подключения|Подключите/.test(await page.locator('#install-dialog').textContent()));
  await shots('install');await page.keyboard.press('Escape');assert.equal(await page.locator('#install-dialog').isVisible(),false);
  await page.evaluate(()=>{
   window.installCalls=0;
   const event=new Event('beforeinstallprompt',{cancelable:true});
   event.prompt=async()=>{window.installCalls++;};event.userChoice=Promise.resolve({outcome:'accepted'});
   window.dispatchEvent(event);
  });
  await page.locator('#install-button').click();assert.equal(await page.evaluate(()=>window.installCalls),1);
  assert.equal(await page.locator('#install-dialog').isVisible(),false);

	  assert.equal(await page.locator('#close-camera').count(),0);
	  await page.locator('#camera-button').click();await page.locator('#camera').waitFor({state:'visible'});
	  assert.equal(await page.locator('#camera-toggle-slot #camera-button').count(),1);
	  await page.locator('#camera-button').click();assert(await page.locator('#camera').isHidden());
  const chooser=page.waitForEvent('filechooser');await page.locator('#gallery-button').click();
  await (await chooser).setFiles(input);
  await page.locator('#result-view').waitFor({state:'visible',timeout:120000});
  const result={name:await page.locator('#result-title').textContent(),portal:await page.locator('#wine-card a.button').getAttribute('href')};console.log('Anonymous photo returned a wine card');
  assert(result.portal.startsWith('https://vino-svoe.ru/wines/'));await shots('result');
  await page.locator('[data-view=history]').click();assert.equal(await page.locator('#history-list .wine-row').count(),1);
  await shots('history');await page.goBack();await page.locator('#result-view').waitFor({state:'visible'});

  await page.evaluate(()=>localStorage.setItem('wine-access',JSON.stringify('expired-legacy-key')));
  await page.goto(base+'/#key=expired-legacy-key');await page.locator('#connection.ready').waitFor();
  assert.equal(await page.evaluate(()=>location.hash),'');assert.equal(await page.evaluate(()=>localStorage.getItem('wine-access')),null);
  await page.locator('[data-view=history]').click();assert.equal(await page.locator('#history-list .wine-row').count(),1);
  await page.reload();await page.locator('#connection.ready').waitFor();
  await page.waitForFunction(()=>navigator.serviceWorker.controller!==null);
  await context.setOffline(true);await page.reload();
  await page.locator('[data-view=history]').click();await page.locator('#history-list .wine-row').waitFor();
  await page.locator('#history-list .wine-row').click();await page.locator('#result-view').waitFor({state:'visible'});
  await context.setOffline(false);
  assert(requests.length>0);assert(requests.every(r=>!r.authorization));assert.deepEqual(errors,[]);
  const report={ok:true,base,checks,errors,anonymous_scan:true,admin_history_protected:true,legacy_links_work:true,camera_open_close:true,gallery_filechooser:true,history_back:true,offline_history:true,pwa_manifest:true,install_fallback:true,simulated_install_prompt:true,physical_android_install_tested:false,api_requests:requests.length,authorization_headers_sent:0,result};
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({ok:true,remote,viewport_checks:checks.length,anonymous_scan:true,admin_history_protected:true,result}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
