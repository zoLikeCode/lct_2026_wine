// Browser contract test for the RunPod-shaped public response and light design.
// Serves the actual static files with fixture APIs; no GPU, live server, or physical camera.
const {chromium}=require('/Users/forthang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');

const root=path.resolve(__dirname,'../../..');
const mobile=path.join(root,'outputs/wine_server/static/mobile');
const admin=path.join(root,'outputs/wine_server/static/admin');
const out=path.join(root,'work/runpod_ui_qa');
fs.mkdirSync(out,{recursive:true});
const photo=fs.readFileSync(path.join(mobile,'icon-192.png'));
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2','.webmanifest':'application/manifest+json','.webp':'image/webp'};

function card(slug,name,more={}){
 return {slug,name,winery:'Тестовая винодельня',category:'Красное сухое',region:'Крым',grapes:'Каберне',description:'Описание из каталога.',pairings:['Сыр'],portal_url:'https://vino-svoe.ru/wines/'+slug,image:'/assets/no-bottle.svg',rating:0,favorite:false,average_rating:null,rating_count:0,...more};
}
const candidates=[card('known-1','Вино 1',{pairings:['Мясные блюда','Сыры']}),card('known-2','Вино 2'),card('known-3','Вино 3'),card('known-4','Вино 4'),card('new-slug','Новое вино',{preferences_available:false,portal_url:'',description:'',pairings:[]})];
let predictMode='no_wine';
const uploads=[];
const assistantCalls=[];
const assignments=[];
const preferences=new Map();
const adminHistory=[
 {id:'scan-no-wine',created:'2026-09-29T13:00:00Z',device:'qa-browser',status:'done',result:{slug:null,no_wine:true,model_version:'qwen3-vl-8b-lora-v2-runpod-1'},error:'',asset_id:'',assigned_slug:''},
 {id:'scan-unknown',created:'2026-09-29T12:00:00Z',device:'qa-browser',status:'done',result:{slug:'new-slug',no_wine:false,ml_response:{card:{name:'Новое вино'}},model_version:'qwen3-vl-8b-lora-v2-runpod-1'},error:'',asset_id:'',assigned_slug:''}
];
function json(res,data,status=200){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));}
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://127.0.0.1');const p=url.pathname;
 if(p==='/api/status')return json(res,{ready:true,catalog_count:2103});
 if(p==='/api/product/bootstrap')return json(res,{preferences:Object.fromEntries(preferences),collection:[],recommendations:[],assistant_mode:'catalog',catalog_count:2103});
 if(p==='/api/product/recommendations')return json(res,{items:[]});
 if(p.startsWith('/api/product/preferences/')){
  const slug=decodeURIComponent(p.slice('/api/product/preferences/'.length));const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const values=JSON.parse(Buffer.concat(chunks).toString());const updated={rating:0,favorite:false,...preferences.get(slug),...values};preferences.set(slug,updated);
  return json(res,{...(candidates.find(x=>x.slug===slug)||card(slug,slug)),...updated});
 }
 if(p.startsWith('/api/product/wines/')){
  const slug=decodeURIComponent(p.slice('/api/product/wines/'.length));return json(res,candidates.find(x=>x.slug===slug)||card(slug,slug));
 }
 if(p.startsWith('/api/product/scans/')&&p.endsWith('/image')){res.writeHead(200,{'Content-Type':'image/png'});return res.end(photo);}
	  if(p==='/api/product/assistant'){
	   const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());assistantCalls.push(body);
	   if(body.intent==='dish_pairing')return json(res,{text:'К этому вину подойдёт запечённая утка: насыщенный вкус блюда поддержит характер вина.',items:[],mode:'ai',warning:null,recommendation_type:'dish',wine:candidates[0],dishes:[{name:'Запечённая утка',source_pairing:'Мясные блюда'}]});
	   if(body.intent==='dish_recipe')return json(res,{text:'Ингредиенты: утка, яблоки, соль. Запекайте 90 минут при 180 °C.',items:[],mode:'ai',warning:null,recommendation_type:'dish_recipe',wine:null,dishes:[]});
	   return json(res,{text:'Похоже на вино из каталога. Сверьте этикетку.',items:[candidates[0]],mode:'ai',warning:null});
 }
 if(p==='/api/predict'){
  const chunks=[];for await(const chunk of req)chunks.push(chunk);uploads.push(Buffer.concat(chunks));
  if(predictMode==='busy')return json(res,{detail:'Сканер занят. Повторите через несколько секунд.'},429);
  if(predictMode==='unavailable')return json(res,{detail:'Распознавание временно недоступно.'},503);
  if(predictMode==='normal')return json(res,{id:'public-normal',no_wine:false,wine:candidates[0],alternatives:candidates.slice(1),confidence:{top1_score:.93,gap_top1_top2:.08},model_version:'qwen3-vl-8b-lora-v2-runpod-1',latency_ms:1200});
  return json(res,{id:'public-no-wine',no_wine:true,wine:null,alternatives:candidates,confidence:{top1_score:.49,gap_top1_top2:.02},model_version:'qwen3-vl-8b-lora-v2-runpod-1',latency_ms:1200});
 }
 if(p==='/admin/api/config')return json(res,{stats:{images:0},scanner_url:'/',batches:[],ready:true,model_version:'qwen3-vl-8b-lora-v2-runpod-1'});
 if(p==='/admin/api/wines')return json(res,{items:candidates.slice(0,4).map(x=>({...x,count:1,unreviewed:0,reference_label:'',url:x.portal_url}))});
 if(p==='/admin/api/history')return json(res,{items:adminHistory,total:adminHistory.length,offset:0});
 if(p.startsWith('/admin/api/image/')){res.writeHead(200,{'Content-Type':'image/png'});return res.end(photo);}
 if(p.startsWith('/admin/api/boundaries/'))return json(res,{});
 if(p==='/admin/api/assign'){
  const chunks=[];for await(const chunk of req)chunks.push(chunk);const data=JSON.parse(Buffer.concat(chunks).toString());assignments.push(data);
  const row=adminHistory.find(x=>x.id===data.id);if(row){row.assigned_slug=data.slug;row.asset_id='asset-'+data.id;}
  return json(res,{asset_id:'asset-'+data.id,slug:data.slug});
 }
 let file;
 if(p==='/'||p==='/index.html')file=path.join(mobile,'index.html');
 else if(p==='/old-design'||p==='/old-design/'){res.writeHead(308,{Location:'/'});return res.end();}
 else if(p==='/admin'||p==='/admin/')file=path.join(admin,'index.html');
 else if(p.startsWith('/admin/static/'))file=path.join(admin,p.slice('/admin/static/'.length));
 else file=path.join(mobile,p);
 if(!file.startsWith(p.startsWith('/admin')?admin:mobile)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end();}
 res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(res);
});

async function layout(page,name,width){
 await page.setViewportSize({width,height:width<500?844:960});
 const dimensions=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));
 assert(dimensions.scroll<=dimensions.client+1,`${name} overflows at ${width}px: ${JSON.stringify(dimensions)}`);
 await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:!/assistant-attachment|dish-answer|wine-answer/.test(name),animations:'disabled'});
 return {name,width,...dimensions};
}

(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
 const checks=[];
 try{
  const removedPage=await browser.newPage();
  assert.equal((await removedPage.request.get(base+'/dark-design')).status(),404,'Dark design route must be gone');
  await removedPage.close();
  for(const route of ['/']){
   const design='light';
   preferences.clear();
   const context=await browser.newContext({viewport:{width:390,height:844},permissions:['camera'],reducedMotion:'reduce',serviceWorkers:'block'});
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(base+route);await page.waitForFunction(()=>typeof processFile==='function');
   const manifest=await page.evaluate(async()=>{const href=document.querySelector('link[rel=manifest]').getAttribute('href');return {href,data:await(await fetch(href)).json()};});
   assert.equal(manifest.href,'/manifest.webmanifest');
   assert.equal(manifest.data.start_url,route);assert.equal(manifest.data.id,route);
   assert(await page.locator('.app-header .brand').isHidden(),'Scanner must not display its brand');
   assert(await page.locator('#install-button').isHidden(),'Scanner must not display the install button');
	   assert(await page.locator('#help-button').isHidden(),'Scanner must not display help');
	   assert.equal(await page.locator('#close-camera').count(),0,'Scanner must not display a separate close X');
	   const idleShade=await page.locator('.scan-frame').evaluate(el=>getComputedStyle(el).boxShadow);
	   assert(idleShade.includes('rgba(33, 23, 25,'),'The area outside the scanner frame must have a subtle shade');
   for(const width of [1440,1024,390,360])checks.push(await layout(page,design+'-scanner',width));
   assert(await page.locator('#camera').isHidden(),'Camera must start off');
   assert.equal(await page.locator('#camera-button').getAttribute('aria-pressed'),'false');
	   await page.locator('#camera-button').click();await page.locator('#camera').waitFor({state:'visible'});
	   assert.equal(await page.locator('#camera-button').getAttribute('aria-pressed'),'true');
	   assert.equal(await page.locator('#camera-toggle-slot #camera-button').count(),1,'Camera toggle must remain available while live');
	   const liveShade=await page.locator('.scan-frame').evaluate(el=>getComputedStyle(el).boxShadow);
	   assert(liveShade.includes('rgba(33, 23, 25,'),'The live camera area outside the frame must stay shaded');
   for(const width of [1440,1024,390,360])checks.push(await layout(page,design+'-live-scanner',width));
	   await page.locator('#camera-button').click();assert(await page.locator('#camera').isHidden(),'Second press on camera button must turn it off');
	   assert.equal(await page.locator('#camera-button').getAttribute('aria-pressed'),'false');
	   await page.locator('#camera-button').click();await page.locator('#camera').waitFor({state:'visible'});
	   await page.locator('#camera-button').click();assert(await page.locator('#camera').isHidden(),'Camera button must turn off a reopened camera');

   predictMode='no_wine';await page.locator('#photo-file').setInputFiles({name:'original.png',mimeType:'image/png',buffer:photo});
   await page.locator('#result-title').getByText('Не удалось распознать вино').waitFor();
   assert.equal(await page.locator('.no-wine-result p').count(),0,'No-wine result shows only the requested status text');
   assert.equal(await page.locator('#alternatives-count').textContent(),'5');
   assert(uploads.at(-1).includes(photo),'The uploaded original PNG bytes were changed');
   for(const width of [1440,1024,390,360])checks.push(await layout(page,design+'-no-wine',width));
   await page.locator('#ask-about-scan').click();await page.locator('#assistant-attachment').waitFor({state:'visible'});
   assert.equal(await page.locator('#attachment-label').textContent(),'Снимок скана');
   await page.locator('#remove-attachment').click();assert(await page.locator('#assistant-attachment').isHidden());
   await page.goBack();await page.locator('#result-title').getByText('Не удалось распознать вино').waitFor();
   await page.locator('#alternatives summary').click();
   assert.equal(await page.locator('#alternative-list .wine-row').count(),5);
   await page.locator('#alternative-list .wine-row').last().click();
	   assert.equal(await page.locator('#result-title').textContent(),'Новое вино');
	   assert(await page.locator('#wine-personal').isHidden(),'Unknown catalog slug must not offer ratings or favorites');
	   assert.equal(await page.locator('#wine-card .source-link').count(),0);
	   assert.equal(await page.locator('#wine-card .wine-food, #wine-card .food-list, #wine-card #ask-pairing').count(),0,'Alternative card must not repeat the food block');
	   assert.equal(await page.locator('#wine-card .match-note, #wine-card .match-score').count(),0,'Alternative card must not show the matching notice');
   await page.locator('#rescan-top').click();await page.locator('#scan-view [data-view=history]').click();
   assert.equal(await page.locator('#history-list .wine-row').count(),1);
   assert.match(await page.locator('#history-list').textContent(),/Не удалось распознать вино/);
   for(const width of [1440,1024,390,360])checks.push(await layout(page,design+'-history',width));
   await page.locator('#history-list .wine-row').click();assert.equal(await page.locator('#result-title').textContent(),'Не удалось распознать вино');
   await page.goBack();await page.locator('#history-view').waitFor({state:'visible'});
   await page.goForward();await page.locator('#result-title').getByText('Не удалось распознать вино').waitFor();

   await page.locator('[data-view=scan]').last().click();
   const beforeInvalid=uploads.length;
   await page.locator('#photo-file').setInputFiles({name:'damaged.png',mimeType:'image/png',buffer:Buffer.from('invalid image')});
   await page.locator('#notice-text').getByText(/Фото не открывается/).waitFor();
   assert(await page.locator('#retry-scan').isHidden(),'A damaged image cannot be retried');
   await page.locator('#photo-file').setInputFiles({name:'oversize.png',mimeType:'image/png',buffer:Buffer.alloc(24*1024*1024+1)});
   await page.locator('#notice-text').getByText(/до 24 МБ/).waitFor();
   assert(await page.locator('#retry-scan').isHidden(),'An oversize file cannot be retried');
   assert.equal(uploads.length,beforeInvalid,'Locally invalid files must not reach predict');
   predictMode='busy';
   await page.locator('#photo-file').setInputFiles({name:'original.png',mimeType:'image/png',buffer:photo});
   await page.locator('#retry-scan').waitFor({state:'visible'});
   assert.match(await page.locator('#notice-text').textContent(),/занят/);
   predictMode='unavailable';await page.locator('#retry-scan').click();
   await page.locator('#retry-scan').waitFor({state:'visible'});
   assert.match(await page.locator('#notice-text').textContent(),/недоступно/);
   predictMode='normal';await page.locator('#retry-scan').click();
   await page.locator('#result-title').getByText('Вино 1').waitFor();
	   assert.equal(await page.locator('.catalog-tag').count(),0,'The catalog overlay label must be removed from recognized wine');
	   assert.equal(await page.locator('#alternatives-count').textContent(),'4');
	   assert.equal(await page.locator('#wine-card .match-note, #wine-card .match-score').count(),0,'Recognized card must not show the matching notice or score');
	   assert.equal(await page.locator('.result-pairing h2').textContent(),'С этим вином сочетаются');
	   assert.equal(await page.locator('.pairing-preview li').first().textContent(),'Мясные блюда');
	   assert.equal(await page.locator('#wine-card .wine-food, #wine-card .food-list, #wine-card #ask-pairing').count(),0,'Pairings must appear only in the upper result panel');
	   assert.equal(await page.locator('#wine-card .wine-extra h2').count(),1,'Wine description must have one character heading');
	   assert.equal((await page.locator('#wine-card .wine-description h2').textContent()).replace(/\s/g,''),'Характервбокале.');
	   assert.equal(await page.locator('#ask-about-scan').textContent(),'Подобрать блюдо с помощником');
   for(const width of [1440,1024,390,360]){
    await page.setViewportSize({width,height:width<500?844:960});
    const order=await page.evaluate(()=>{
     const image=document.querySelector('.scan-result-card .wine-visual').getBoundingClientRect();
     const heading=document.querySelector('.scan-result-card #result-title').getBoundingClientRect();
     const advice=document.querySelector('.scan-result-card .result-pairing').getBoundingClientRect();
     return {image:image.top,heading:heading.top,advice:advice.top};
    });
    if(width<500)assert(order.image<order.heading&&order.heading<order.advice,`Recognized wine must precede advice at ${width}px: ${JSON.stringify(order)}`);
    else assert(order.heading<order.advice,`Wine heading must precede advice at ${width}px: ${JSON.stringify(order)}`);
   }
   assert(uploads.at(-1).includes(photo),'Retry changed the original PNG bytes');
   for(const width of [1440,1024,390,360])checks.push(await layout(page,design+'-normal-result',width));
   await page.locator('#ask-about-scan').click();await page.locator('#assistant-attachment').waitFor({state:'visible'});
   for(const width of [1440,1024,390,360])checks.push(await layout(page,design+'-assistant-attachment',width));
   assert(await page.locator('.prompt-list').isHidden(),'Wine suggestion prompts must not appear with a dish request');
   assert.equal(await page.locator('#assistant-input').inputValue(),'Что приготовить к «Вино 1»?');
   await page.locator('#send-button').click();
   await page.locator('.chat-dish h3').getByText('Запечённая утка').waitFor();
   assert.match(await page.locator('.dish-turn .chat-answer').textContent(),/запечённая утка/);
   assert.equal(await page.locator('.dish-turn .chat-result').count(),0);
   assert.equal(assistantCalls.at(-1).scan_id,'public-normal');
   assert.equal(assistantCalls.at(-1).intent,'dish_pairing');
   const answerName='dish-answer';
   for(const width of [1440,1024,390,360]){
    checks.push(await layout(page,design+'-'+answerName,width));
    if(width<500){
     const nav=await page.evaluate(()=>{const element=document.querySelector('.bottom-nav');const rect=element.getBoundingClientRect();return {bottom:rect.bottom,top:rect.top,height:rect.height,viewport:innerHeight,hit:document.elementFromPoint(innerWidth/2,rect.top+rect.height/2)?.closest('.bottom-nav')===element};});
     assert(nav.height>=60&&nav.top>=0&&nav.bottom<=nav.viewport&&nav.hit,'Bottom navigation must remain visible and clickable after assistant answer: '+JSON.stringify(nav));
    }
   }
	   const answerFont=await page.locator('.dish-turn .chat-answer').evaluate(el=>Number.parseFloat(getComputedStyle(el).fontSize));
	   assert(answerFont>=15,'Dish explanation must be at least 15px');
	   assert(!JSON.stringify(assistantCalls.at(-1)).includes('data:image/'),'Photo bytes must not be sent in assistant JSON');
	   assert(await page.locator('#assistant-attachment').isHidden(),'Scan attachment must clear after the dish answer');
	   assert.equal(await page.locator('#assistant-input').getAttribute('placeholder'),'Например, как приготовить это блюдо?');
	   await page.locator('#assistant-input').fill('Как приготовить?');await page.locator('#send-button').click();
	   await page.locator('.recipe-turn .chat-answer').getByText(/Ингредиенты: утка/).waitFor();
	   const recipeFont=await page.locator('.recipe-turn .chat-answer').evaluate(el=>Number.parseFloat(getComputedStyle(el).fontSize));
	   assert(recipeFont>=15,'Recipe explanation must be at least 15px');
	   assert.equal(assistantCalls.at(-1).intent,'dish_recipe');
	   assert.equal(assistantCalls.at(-1).scan_id,'public-normal');
	   assert.equal(assistantCalls.at(-1).dish_name,'Запечённая утка');
	   assert.equal(assistantCalls.at(-1).message,'Как приготовить?');
	   assert(assistantCalls.at(-1).context.includes('Что приготовить к «Вино 1»?'));
	   assert.equal(await page.locator('.recipe-turn .chat-result, .recipe-turn .chat-dish, .recipe-turn .chat-attached-photo').count(),0,'Recipe answer must have no wine cards, dish selector, or reattached photo');
	   await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
	   const initialRecipeVisible=await page.evaluate(()=>{const answer=document.querySelector('.recipe-turn .chat-answer').getBoundingClientRect(),pane=document.querySelector('#chat-messages').getBoundingClientRect();return answer.top>=pane.top-1&&answer.top<pane.bottom;});
	   assert(initialRecipeVisible,'Recipe answer must be visible immediately after sending');
	   for(const width of [1440,1024,390,360]){
	    await page.setViewportSize({width,height:width<500?844:960});
	    await page.locator('.recipe-turn').scrollIntoViewIfNeeded();
	    const recipePosition=await page.evaluate(()=>{const answer=document.querySelector('.recipe-turn .chat-answer').getBoundingClientRect(),pane=document.querySelector('#chat-messages'),area=pane.getBoundingClientRect(),turn=document.querySelector('.recipe-turn').getBoundingClientRect();return {answer:{top:answer.top,bottom:answer.bottom},pane:{top:area.top,bottom:area.bottom,scrollTop:pane.scrollTop,scrollHeight:pane.scrollHeight,clientHeight:pane.clientHeight},turn:{top:turn.top,bottom:turn.bottom}};});
	    assert(recipePosition.answer.top>=recipePosition.pane.top-1&&recipePosition.answer.top<recipePosition.pane.bottom,`Recipe answer must start in the chat viewport at ${width}px: ${JSON.stringify(recipePosition)}`);
	    checks.push(await layout(page,design+'-recipe-answer',width));
	   }
	   await page.locator('#assistant-input').fill('Какие ингредиенты нужны?');await page.locator('#send-button').click();
	   await page.locator('.recipe-turn').nth(1).locator('.chat-answer').getByText(/Ингредиенты: утка/).waitFor();
	   assert.equal(assistantCalls.at(-1).intent,'dish_recipe');
	   assert.equal(assistantCalls.at(-1).dish_name,'Запечённая утка');
	   await page.locator('#assistant-input').fill('Подбери другое вино');await page.locator('#send-button').click();
	   await page.locator('.chat-turn').last().locator('.chat-result').waitFor();
	   assert.equal(assistantCalls.at(-1).intent,undefined,'Unrelated request must keep normal assistant behavior');
	   assert.equal(assistantCalls.at(-1).scan_id,undefined,'Unrelated request must not reuse the prior scan');
	   assert.equal(await page.locator('#assistant-input').getAttribute('placeholder'),'Например, красное к стейку…');
	   await page.goBack();await page.locator('#result-title').getByText('Вино 1').waitFor();
   await page.locator('#wine-personal [data-rating="5"]').click();
   await page.waitForFunction(()=>document.querySelector('#wine-personal [data-rating="5"]')?.getAttribute('aria-pressed')==='true');
   await page.locator('#save-wine').click();
   await page.waitForFunction(()=>document.querySelector('#save-wine')?.getAttribute('aria-pressed')==='true');
   await page.locator('#rescan-top').click();await page.locator('#scan-view [data-view=history]').click();
   assert.equal(await page.locator('#history-list .wine-row').count(),2);
   await page.evaluate(()=>{const saved=JSON.parse(localStorage.getItem('wine-history'));delete saved[0].thumb;localStorage.setItem('wine-history',JSON.stringify(saved));});
   await page.reload();await page.locator('#scan-view [data-view=history]').click();
   await page.locator('#history-list .wine-row').first().click();await page.locator('#ask-about-scan').click();
   assert.match(await page.locator('#attachment-image').getAttribute('src'),/\/api\/product\/scans\/public-normal\/image$/);
   await page.waitForFunction(()=>document.getElementById('attachment-image').naturalWidth>0);
   assert.deepEqual(errors,[],`${design} browser errors`);
   await context.close();
  }

  const adminContext=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce',serviceWorkers:'block'});
  const adminPage=await adminContext.newPage();const adminErrors=[];adminPage.on('pageerror',e=>adminErrors.push(e.message));
  await adminPage.goto(base+'/admin/');await adminPage.locator('#application').waitFor({state:'visible'});
  assert.match(await adminPage.locator('#history-grid .scan').first().textContent(),/Вино не найдено/);
  assert.match(await adminPage.locator('#history-grid .scan').last().textContent(),/Новое вино/);
  for(const width of [1440,1024,390,360])checks.push(await layout(adminPage,'admin-history',width));
  await adminPage.locator('#history-grid .scan button').first().click();
  await adminPage.locator('#label-view').waitFor({state:'visible'});
  await adminPage.locator('[data-slug=known-1]').click();await adminPage.locator('#save-label').click();
  await adminPage.waitForFunction(()=>!document.body.classList.contains('busy'));
  assert.equal(assignments.at(-1).id,'scan-no-wine');assert.equal(assignments.at(-1).slug,'known-1');
  assert.equal(adminHistory[0].result.slug,null,'Assignment must leave the original prediction intact');
  assert.deepEqual(adminErrors,[],'Admin browser errors');
  await adminContext.close();

  const pwaContext=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
  const pwaPage=await pwaContext.newPage();const pwaErrors=[];pwaPage.on('pageerror',e=>pwaErrors.push(e.message));
  await pwaPage.goto(base+'/__seed');
  await pwaPage.evaluate(async()=>{const cache=await caches.open('wine-server-v11-light-default');await cache.put('/app.js',new Response('stale app'))});
  await pwaPage.goto(base+'/');
  await pwaPage.waitForFunction(()=>navigator.serviceWorker?.controller!==null);
  const cacheNames=await pwaPage.evaluate(()=>caches.keys());
  assert(cacheNames.includes('wine-server-v19-recipe-followup'));
  assert(!cacheNames.includes('wine-server-v11-light-default'),'Old installed PWA cache should be removed');
  predictMode='no_wine';await pwaPage.locator('#photo-file').setInputFiles({name:'original.png',mimeType:'image/png',buffer:photo});
  await pwaPage.locator('#result-title').getByText('Не удалось распознать вино').waitFor();
  await pwaContext.setOffline(true);await pwaPage.reload();
  await pwaPage.locator('#scan-view [data-view=history]').click();
  await pwaPage.locator('#history-list .wine-row').waitFor();
  assert.match(await pwaPage.locator('#history-list').textContent(),/Не удалось распознать вино/);
  assert.deepEqual(pwaErrors,[],'PWA browser errors');
  await pwaContext.close();

	  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,checks:checks.length,assignments:assignments.length,uploads:uploads.length,design:'light',viewports:[1440,1024,390,360],camera_second_press_off:true,camera_close_x_absent:true,scanner_outside_frame_shaded:true,recognized_wine_precedes_text:true,catalog_overlay_removed:true,no_wine_plain_status:true,dark_route_removed:true,dish_pairing:true,dish_recipe_followup:true,scan_attachment_sent_by_id:true,attachment_after_reload:true,photo_bytes_absent_from_assistant_json:true,pwa_cache:'wine-server-v19-recipe-followup',pwa_old_cache_removed:true,pwa_offline_no_wine_history:true},null,2));
	  console.log(`PASS: ${checks.length} browser layout checks; light-only result order, dish and recipe follow-up, camera toggle without X, photo attachment, no_wine, five/four candidates, history, 429/503 retry, admin assignment, PWA update/offline history`);
 }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1});
