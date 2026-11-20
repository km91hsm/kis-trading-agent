import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { apiCycle, retryDeadline } from '../api-runner.js';

const TOKEN = 'a'.repeat(32), URL_NEWS = 'https://www.saveticker.com/api/news/list?page=1&page_size=20&sort=created_at_desc&label_group=1&label_name=1';
const descriptor = { method:'GET',url:URL_NEWS,kind:'news',max_bytes:2097152 };
async function content(response, override={}) {
 const listeners=[], state={enabled:true,backend:'api',ownerToken:TOKEN}; let calls=0;
 const context={URL,URLSearchParams,AbortController,TextDecoder,Uint8Array,Date,JSON,Set,Object,Number,Promise,RegExp,Error,setTimeout,clearTimeout,
 location:{origin:'https://www.saveticker.com',hash:'#trader-saveticker-api='+TOKEN},document:{title:'뉴스',querySelector:()=>null},
 chrome:{runtime:{id:'ext',onMessage:{addListener:f=>listeners.push(f)}},storage:{local:{get:async()=>state}}},
 fetch:async(...args)=>{calls++; assert.equal(args[1].credentials,'same-origin'); assert.equal(args[1].redirect,'manual'); return response;},...override};
 vm.runInNewContext(await readFile(new URL('../api-content.js',import.meta.url),'utf8'),context);
 return {state,send:(extra={},sender={id:'ext'})=>new Promise(resolve=>{const ret=listeners[0]({type:'api_fetch',token:TOKEN,job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:descriptor},...extra},sender,resolve);if(!ret)resolve(null);}),calls:()=>calls};
}
function jsonResponse(data, status=200, extra={}) {return new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json',...extra}});}
test('isolated API content uses browser auth and projects only collection data',async()=>{
 const r=jsonResponse({news_list:[{id:'1',title:'news',author_secret:'never',content:'body'}]});Object.defineProperty(r,'url',{value:URL_NEWS});
 const c=await content(r); const out=await c.send(); assert.equal(out.status,200);assert.equal(out.data.news_list[0].title,'news');assert.equal(out.data.news_list[0].author_secret,undefined);assert.equal(c.calls(),1);
});
test('news list forwards only the verified group headline beside the article title', async () => {
 const grouped = {id:'story', title:'개별 기사', news_group_id:7,
  group_summary:{headline:'묶음 대표 제목', is_ai_headline:true, member_count:5,
   representative_news_id:null, name:'비공개 이름', content:'비공개 내용'}};
 const r=jsonResponse({news_list:[grouped, {...grouped, news_group_id:'7'}]});
 Object.defineProperty(r, 'url', {value:URL_NEWS});
 const out=await (await content(r)).send();
 const rows=JSON.parse(JSON.stringify(out.data.news_list));
 assert.deepEqual(rows[0], {id:'story', title:'개별 기사', news_group_id:7,
  group_summary:{headline:'묶음 대표 제목'}});
 assert.equal(rows[1].group_summary, undefined);
});
test('foreign sender, unknown URL, stopped and wrong owner do not fetch',async()=>{
 const c=await content(jsonResponse({}));assert.equal(await c.send({}, {id:'other'}),null);
 assert.equal((await c.send({token:'wrong'})).error,'api_denied');
 assert.equal((await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{...descriptor,url:'https://evil.invalid/api/news/list'}}})).error,'api_contract_unverified');
 c.state.enabled=false; assert.equal((await c.send()).error,'api_stopped');assert.equal(c.calls(),0);
});
test('challenge, redirect mismatch, too large and rate limit are not normal data',async()=>{
 let c=await content(jsonResponse({}),{document:{title:'잠시만 기다려 주세요 · SAVE',querySelector:()=>null}});assert.equal((await c.send()).status,403);assert.equal(c.calls(),0);
 let r=jsonResponse({});Object.defineProperty(r,'url',{value:'https://www.saveticker.com/login'});c=await content(r);assert.equal((await c.send()).status,403);
 r=jsonResponse({padding:'a'.repeat(2097153)});Object.defineProperty(r,'url',{value:URL_NEWS});c=await content(r);assert.equal((await c.send()).error,'api_response_too_large');
 r=jsonResponse({},429,{'retry-after':'120'});Object.defineProperty(r,'url',{value:URL_NEWS});c=await content(r);assert.equal((await c.send()).status,429);assert.equal((await c.send()).retry_after,'120');
});
test('Retry-After respects server date/seconds and a conservative local floor',()=>{
 const now=Date.now();assert.equal(retryDeadline('120',now),now+120000);assert.equal(retryDeadline('invalid',now),now+60000);assert.ok(retryDeadline(new Date(now+300000).toUTCString(),now)>now+299000);
});
function runner(response={status:200,url:URL_NEWS,content_type:'application/json',data:{news_list:[]}}, opts={}) {
 const calls=[],state={},views=[{stream:'news_all',kind:'news',interval_s:30},{stream:'calendar_2026-10',kind:'calendar',interval_s:300}],done=[];let current=true,blocked=false;
 const deps={wait:async()=>{},token:TOKEN,active:()=>current,store:async v=>Object.assign(state,v),blockHost:async()=>{blocked=true;},scheduler:{next:(_now,lane)=>views.find(v=>v.kind===lane),done:(...x)=>done.push(x),snapshot:()=>({})},request:async(type,body)=>{calls.push({type,...body});if(type==='api_begin')return {job_id:'b'.repeat(32),request:descriptor,view:views[0],expires_at:new Date(Date.now()+60000).toISOString()};return {count:0,status:'partial'};},chrome:{tabs:{query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],sendMessage:async()=>{if(opts.stop)current=false;return response;}},windows:{update:()=>{throw Error('focus forbidden');}}},...opts.deps};
 return {run:()=>apiCycle(deps),calls,state,done,isBlocked:()=>blocked};
}
test('API failures retain a fixed cause and stage without saving arbitrary exception text', async () => {
 for (const [cause, expected] of [['api_shape_unverified', 'api_shape_unverified'], ['PRIVATE https://secret.test?token=secret', 'api_request_failed']]) {
  const r=runner(undefined,{deps:{request:async type=>{
   if(type==='api_begin')return {job_id:'b'.repeat(32),request:descriptor};
   if(type==='api_prepare')throw Error(cause);
  }}});
  await r.run();
  assert.equal(r.state.lane_error_news,expected);
  assert.equal(r.state.lane_error_stage_news,'prepare');
  assert.ok(!JSON.stringify(r.state).includes('PRIVATE'));
 }
 const r=runner({error:'api_response_unverified'});await r.run();
 assert.equal(r.state.lane_error_news,'api_response_unverified');
 assert.equal(r.state.lane_error_stage_news,'fetch');
});
test('API cycle never focuses/activates, saves only current job and preserves partial',async()=>{
 const r=runner();await r.run();assert.ok(r.calls.some(c=>c.type==='api_snapshot'));assert.equal(r.state.lane_news,'API 부분 수집 · 0개');
 const stale=runner(undefined,{stop:true});await stale.run();assert.ok(!stale.calls.some(c=>c.type==='api_snapshot'));
});
test('auth stops later lanes; rate limit persists whole host deadline',async()=>{
 const auth=runner({status:403});await auth.run();assert.ok(auth.isBlocked());assert.equal(auth.calls.filter(c=>c.type==='api_begin').length,1);
 const rate=runner({status:429,retry_after:'120'});await rate.run();assert.ok(rate.state.apiCooldownUntil>=Date.now()+119000);assert.equal(rate.calls.filter(c=>c.type==='api_begin').length,1);
});
test('production worker API mode routes to api_snapshot and persisted cooldown prevents tab access',async()=>{
 const ev=()=>({listeners:[],addListener(fn){this.listeners.push(fn);}}), state={enabled:false,backend:'api',ownerToken:TOKEN},calls=[];const messages=ev();
 globalThis.chrome={storage:{local:{get:async()=>({...state}),set:async x=>Object.assign(state,x)}},runtime:{id:'ext',onMessage:messages,onStartup:ev(),onInstalled:ev(),connectNative:()=>{const p={onMessage:ev(),onDisconnect:ev(),disconnect(){},postMessage(m){calls.push(m);queueMicrotask(()=>p.onMessage.listeners[0]({ok:true,id:m.id,views:[{stream:'news_all',kind:'news',url:'https://www.saveticker.com/news',interval_s:30}],job_id:'b'.repeat(32),request:descriptor,status:'partial',count:1}));}};return p;}},tabs:{query:async()=>{calls.push({type:'query'});return[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}];},sendMessage:async()=>({url:URL_NEWS,status:200,content_type:'application/json',data:{news_list:[]}})},alarms:{onAlarm:ev(),create:async()=>{},clear:async()=>{}},downloads:{onDeterminingFilename:ev(),onChanged:ev()}};
 const worker=await import('../worker.js?api-mode-integration');state.enabled=true;await worker.tick();assert.ok(calls.some(c=>c.type==='api_snapshot'));assert.ok(!calls.some(c=>c.type==='begin'||c.type==='snapshot'));
 state.apiCooldownUntil=Date.now()+120000;calls.length=0;await worker.tick();assert.ok(!calls.some(c=>c.type==='query'||c.type==='api_begin'));
 await new Promise(resolve=>messages.listeners[0]({action:'stop'},{id:'ext'},resolve));
});
test('challenge query on owned tab is reused rather than repeatedly opening windows',async()=>{
 const r=runner({status:403},{deps:{chrome:{tabs:{query:async()=>[{id:1,url:'https://www.saveticker.com/news?__cf_chl_rt_tk=synthetic#trader-saveticker-api='+TOKEN}],sendMessage:async()=>({status:403})},windows:{create:async()=>{throw Error('must not open a new challenge tab');}}}}});
 await r.run();assert.ok(r.isBlocked());
});

test('opaque authentication redirect stops before reading a body',async()=>{
 const c=await content({type:'opaqueredirect',status:0,url:'',headers:new Headers()});
 assert.equal((await c.send()).status,403);
});
test('persisted 429 cooldown survives worker reload and DOM mode switch',async()=>{
 const ev=()=>({listeners:[],addListener(fn){this.listeners.push(fn);}}), state={enabled:false,backend:'dom',apiCooldownUntil:Date.now()+180000},calls=[];
 const messages=ev();globalThis.chrome={storage:{local:{get:async()=>({...state}),set:async x=>Object.assign(state,x)}},runtime:{id:'ext',onMessage:messages,onStartup:ev(),onInstalled:ev(),connectNative:()=>{const p={onMessage:ev(),onDisconnect:ev(),disconnect(){},postMessage(m){calls.push(m);queueMicrotask(()=>p.onMessage.listeners[0]({ok:true,id:m.id,views:[{stream:'news_all',kind:'news',interval_s:30}]}));}};return p;}},tabs:{query:async()=>{throw Error('must not access tabs during cooldown');}},alarms:{onAlarm:ev(),create:async()=>{},clear:async()=>{}},downloads:{onDeterminingFilename:ev(),onChanged:ev()}};
 const worker=await import('../worker.js?api-cooldown-restart');state.enabled=true;await worker.tick();assert.ok(!calls.some(c=>c.type==='begin'||c.type==='api_begin'));assert.equal(state.connection,'요청 제한 · 서버 대기 시간 적용');await new Promise(resolve=>messages.listeners[0]({action:'stop'},{id:'ext'},resolve));
});
test('project default group context without account names and allow issued detail requests',async()=>{
 const groupUrl='https://www.saveticker.com/api/stocks/api/v1/watchlist/groups';
 let r=jsonResponse({groups:[{groupId:42,isDefault:true,name:'private group',itemCount:10}]});Object.defineProperty(r,'url',{value:groupUrl});
 let c=await content(r);let out=await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{...descriptor,kind:'groups',url:groupUrl}}});
 assert.equal(out.data.groups[0].groupId,42);assert.equal(out.data.groups[0].name,undefined);
 const detailUrl='https://www.saveticker.com/api/news/detail?id=news_one';r=jsonResponse({id:'news_one',title:'One',content:[{type:'text',content:'Body'}]});Object.defineProperty(r,'url',{value:detailUrl});
 c=await content(r);out=await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{...descriptor,kind:'news_detail',url:detailUrl}}});assert.equal(out.data.id,'news_one');
});
test('PDF handoff reads only bounded PDF and releases only its own blob',async()=>{
 const pdfUrl='https://www.saveticker.com/api/report/download?url=%2Fapi%2Fuploads%2Fdocuments%2F77';
 let created=0,revoked=[];class TestURL extends URL {static createObjectURL(){created++;return 'blob:https://www.saveticker.com/own-pdf';}static revokeObjectURL(url){revoked.push(url);}}
 let r=new Response('%PDF-1.4\nSynthetic PDF',{headers:{'content-type':'application/pdf'}});Object.defineProperty(r,'url',{value:pdfUrl});
 let c=await content(r,{URL:TestURL,Blob,Uint8Array});let out=await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{method:'GET',url:pdfUrl,kind:'pdf',max_bytes:25*1024*1024}}});assert.equal(out.blob_url,'blob:https://www.saveticker.com/own-pdf');assert.equal(created,1);
 await c.send({type:'api_pdf_release',nonce:'c'.repeat(32)});assert.deepEqual(revoked,[]);
 await c.send({type:'api_pdf_release',nonce:'b'.repeat(32)});assert.deepEqual(revoked,['blob:https://www.saveticker.com/own-pdf']);
 r=new Response('not PDF',{headers:{'content-type':'application/pdf'}});Object.defineProperty(r,'url',{value:pdfUrl});c=await content(r,{URL:TestURL,Blob,Uint8Array});out=await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{method:'GET',url:pdfUrl,kind:'pdf',max_bytes:25*1024*1024}}});assert.equal(out.error,'api_pdf_unverified');assert.equal(created,1);
});
test('runner fetches only Native-issued children and sends correlated detail envelopes',async()=>{
 let step=0;const detailUrl='https://www.saveticker.com/api/news/detail?id=news_one';
 const r=runner(undefined,{deps:{request:async(type,body)=>{if(type==='api_begin')return {job_id:'b'.repeat(32),request:descriptor};if(type==='api_prepare')return {requests:[{job_id:'b'.repeat(32),request:{...descriptor,url:detailUrl,kind:'news_detail'}}]};if(type==='api_snapshot'&&body.stream==='news_all'){assert.equal(body.details[0].url,detailUrl);step++;}return {status:'partial',count:1};},chrome:{tabs:{query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],sendMessage:async(_id,m)=>({status:200,url:m.job.request.url,content_type:'application/json',data:m.job.request.kind==='news_detail'?{id:'news_one',title:'One',content:'Body'}:{news_list:[{id:'news_one',title:'One'}]}})}}}});
 await r.run();assert.equal(step,1);
});
test('failed default-group setup backs off rather than re-requesting every tick',async()=>{
 const r=runner(undefined,{deps:{setupRequired:true,request:async()=>({job_id:'b'.repeat(32),request:descriptor}),chrome:{tabs:{query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],sendMessage:async()=>({error:'api_context_unverified'})}}}});
 await r.run();assert.ok(r.state.apiSetupRetryAt>=Date.now()+59000);
});
test('explicit regular popular query is allowed with exact observed order only',async()=>{
 for(const tab of ['gainers','losers','turnover']){
  const url='https://www.saveticker.com/api/stocks/api/v1/tickers/popular?tab='+tab+'&groupId=42&basis=regular'+(tab==='turnover'?'':'&excludeRisky=true');
  const r=jsonResponse({tab,basis:'regular',excludeRisky:tab!=='turnover',session:'open',items:[]});Object.defineProperty(r,'url',{value:url});
  const c=await content(r);const out=await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{...descriptor,kind:'popular',url}}});
  assert.equal(out.status,200);assert.equal(out.data.basis,'regular');
 }
});
test('successful API capture clears stale tab preparation connection text',async()=>{
 const r=runner({status:200,url:descriptor.url,content_type:'application/json',data:{news_list:[]}});await r.run();assert.equal(r.state.connection,'연결됨 · API 수집 중');
});
test('read-only probe recognizes late security dialog and never fetches',async()=>{
 const headings=Array.from({length:40},()=>({textContent:'일반 제목'}));headings.push({textContent:'재확인이 필요합니다'});
 const c=await content(null,{document:{title:'뉴스',readyState:'complete',querySelector:()=>null,querySelectorAll:()=>headings}});
 assert.equal((await c.send({type:'api_probe'})).status,403);assert.equal(c.calls(),0);
 const good=await content(null,{document:{title:'뉴스',readyState:'complete',querySelector:()=>null,querySelectorAll:()=>[]}});assert.deepEqual(JSON.parse(JSON.stringify(await good.send({type:'api_probe'}))),{status:200,ready:true,authenticated:false});assert.equal(good.calls(),0);good.state.enabled=false;assert.equal((await good.send({type:'api_probe'})).error,'api_stopped');
});

test('recovery probe distinguishes exact recheck modal, challenge and authenticated page without fetch',async()=>{
 const modal=await content(null,{document:{title:'뉴스',readyState:'complete',querySelector:()=>null,querySelectorAll:()=>[{textContent:'재확인이 필요합니다'}]}});assert.equal((await modal.send({type:'api_probe'})).page_state,'app_recheck');assert.equal(modal.calls(),0);
 const cf=await content(null,{document:{title:'Just a moment',querySelector:()=>null,querySelectorAll:()=>[{textContent:'재확인이 필요합니다'}]}});assert.equal((await cf.send({type:'api_probe'})).page_state,'challenge');
 const good=await content(null,{document:{title:'뉴스',readyState:'complete',querySelector:s=>s==='[aria-label="프로필 메뉴"]'?{}:null,querySelectorAll:()=>[]}});assert.equal((await good.send({type:'api_probe'})).authenticated,true);assert.equal(good.calls(),0);
});
test('only the documented challenged-response header becomes a fixed non-secret marker',async()=>{
 for(const header of ['challenge','other']){const r=new Response('<html>synthetic</html>',{status:403,headers:{'content-type':'text/html','cf-mitigated':header,'set-cookie':'must-not-project'}});Object.defineProperty(r,'url',{value:URL_NEWS});const c=await content(r);const out=await c.send();assert.equal(out.security,header==='challenge'?'cloudflare_challenge':undefined);assert.ok(!JSON.stringify(out).includes('must-not-project'));}
});
test('API cycle forwards only response classification to the whole-host guard',async()=>{let cause;const response={status:403,security:'cloudflare_challenge'};const r=runner(response,{deps:{blockHost:async x=>{cause=x?.security}}});await r.run();assert.equal(cause,'cloudflare_challenge');assert.ok(!r.calls.some(x=>x.type==='api_snapshot'));});

test('news list survives a verification barrier or rate limit on an optional detail', async () => {
 for (const status of [403, 429]) {
  const list = {status:200,url:URL_NEWS,content_type:'application/json',data:{news_list:[{id:'one',title:'One'}]}};
  const child = {request:{...descriptor,kind:'news_detail'}};
  const calls = [];
  const r = runner(list, {deps:{
   request: async (type, body) => {
    calls.push(type);
    if (type === 'api_begin') return {request:descriptor};
    if (type === 'api_prepare') return {requests:[child,child]};
    if (type === 'api_snapshot') {
     assert.deepEqual(body.response,list);
     assert.deepEqual(body.details,[]);
    }
    return {count:1};
   },
   blockHost: async () => calls.push('block'),
   chrome:{tabs:{
    query: async () => [{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],
    sendMessage: async (_id,m) => {
     calls.push(m.job.request.kind);
     return m.job.request.kind === 'news' ? list : {status,retry_after:'120'};
    }
   }}
  }});
  await r.run();
  assert.deepEqual(calls, ['api_begin','news','api_prepare','news_detail','api_snapshot',...(status === 403 ? ['block'] : [])]);
  assert.deepEqual(r.done.map(([, , failed]) => failed),[false]);
  if (status === 429) assert.ok(r.state.apiCooldownUntil >= Date.now()+119000);
 }
});

test('verification block on the news list does not add per-stream retry backoff', async () => {
 const r = runner({status:403});
 await r.run();
 assert.ok(r.isBlocked());
 assert.equal(r.calls.find(call => call.type === 'api_cancel')?.reason, 'verification_required');
 assert.equal(r.done.length, 0);
});

test('content accepts only bounded ordinary news-list pages', async () => {
 for (const [page, valid] of [['2',true],['100',true],['101',false],['02',false],['2&page=1',false]]) {
  const url = URL_NEWS.replace('page=1', 'page=' + page);
  const response = jsonResponse({page:2, news_list:[]});
  Object.defineProperty(response, 'url', {value:url});
  const c = await content(response);
  const out = await c.send({job:{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+60000).toISOString(),request:{...descriptor,url}}});
  assert.equal(c.calls(), valid ? 1 : 0);
  assert.equal(valid ? out.status : out.error, valid ? 200 : 'api_contract_unverified');
 }
});

test('page failures keep the saved head and finish or block before any later page request', async () => {
 for (const status of [403,429,500]) {
  const events = [];
  const r = runner(undefined, {deps:{
   request: async (type, body) => {
    events.push(type + ':' + body.stream);
    if(type==='api_begin') return {job_id:'b'.repeat(32),request:descriptor};
    if(type==='api_prepare') return {requests:[]};
    if(type==='api_snapshot' && body.stream==='news_all')
     return {count:20,next_page:{job_id:'b'.repeat(32),request:{...descriptor,url:URL_NEWS.replace('page=1','page=2')}}};
    return {count:1};
   },
   chrome:{tabs:{
    query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],
    sendMessage:async(_id,m)=>{const page=new URL(m.job.request.url).searchParams.get('page');
     events.push('fetch:'+page);
     return page==='2' ? {status,error:'api_response_unverified',retry_after:'120'} : {status:200};
    }
   }}
  }});
  await r.run();
  assert.ok(events.indexOf('fetch:2') > events.indexOf('api_snapshot:news_all'));
  assert.equal(r.done[0][2],false);
  if(status!==500) assert.ok(!events.some(e=>e==='api_begin:calendar_2026-10'));
  if(status===403) assert.ok(r.isBlocked());
  if(status===429) assert.ok(r.state.apiCooldownUntil>=Date.now()+119000);
 }
});

test('ordinary optional news-detail transport failure keeps the first page', async () => {
 let saved;
 const r=runner(undefined,{deps:{
  request:async(type,body)=>{
   if(type==='api_begin')return {job_id:'b'.repeat(32),request:descriptor};
   if(type==='api_prepare')return {requests:[{request:{kind:'news_detail'}}]};
   if(type==='api_snapshot'&&body.stream==='news_all')saved=body;
   return {count:1};
  },
  chrome:{tabs:{
   query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],
   sendMessage:async(_id,m)=>{
    if(m.job.request.kind==='news_detail')throw Error('synthetic disconnect');
    return {status:200,data:{news_list:[{id:'demo',title:'합성 기사'}]}};
   }
  }}
 }});
 await r.run();
 assert.equal(saved?.response.data.news_list[0].id,'demo');
 assert.deepEqual(saved.details,[]);
 assert.equal(r.done[0][2],false);
});

test('stop while an optional detail disconnects cancels pending collection', async () => {
 let active = true, saved = false;
 const r=runner(undefined,{deps:{
  active:()=>active,
  request:async type=>{
   if(type==='api_begin')return {job_id:'b'.repeat(32),request:descriptor};
   if(type==='api_prepare')return {requests:[{request:{kind:'news_detail'}}]};
   if(type==='api_snapshot')saved=true;
   return {};
  },
  chrome:{tabs:{
   query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],
   sendMessage:async(_id,m)=>{
    if(m.job.request.kind==='news_detail'){ active=false; throw Error('stopped'); }
    return {status:200};
   }
  }}
 }});
 await r.run();
 assert.equal(saved,false);
});

test('rate limit before saving the list uses the host deadline only', async () => {
 const r = runner({status:429,retry_after:'120'});
 await r.run();
 assert.deepEqual(r.done,[]);
 assert.ok(r.state.apiCooldownUntil>=Date.now()+119000);
});

test('ordinary detail errors allow gap pages and retain a detail diagnostic', async () => {
 for (const failure of [404,500,'disconnect','invalid-enrichment']) {
  const fetched=[];
  const second={job_id:'b'.repeat(32), request:{...descriptor,url:URL_NEWS.replace('page=1','page=2')}};
  const r=runner(undefined,{deps:{
   request:async(type,body)=>{
    if(type==='api_begin')return {job_id:'b'.repeat(32),request:descriptor};
    if(type==='api_prepare')return {requests:[{request:{kind:'news_detail'}}]};
    if(type==='api_snapshot' && body.stream==='news_all') {
     if(failure==='invalid-enrichment' && body.details.length)throw Error('api_detail_unverified');
     return {count:20,next_page:body.news_pages ? second : null};
    }
    return {count:1};
   },
   chrome:{tabs:{
    query:async()=>[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],
    sendMessage:async(_id,m)=>{
     if(m.job.request.kind==='news_detail') {
      if(failure==='disconnect') throw Error('synthetic disconnect');
      if(typeof failure==='number')return {status:failure,error:'api_response_unverified'};
      return {status:200};
     }
     fetched.push(m.job.request.url);
     return {status:200};
    }
   }}
  }});
  await r.run();
  assert.ok(fetched.includes(second.request.url), String(failure));
  assert.equal(r.done[0][2],false);
  assert.equal(r.state.lane_error_stage_news,'detail');
 }
});
