import test from 'node:test';
import assert from 'node:assert/strict';
import { recoverRecheck } from '../recheck.js';
const TOKEN='a'.repeat(32),NOW=1000000;
function fixture(){const state={autoRecheck:true,backend:'api',hostBlocked:true,recheckRecovery:null},calls=[];let active=true;const d={state,token:TOKEN,now:NOW,active:()=>active,store:async v=>{calls.push('store');Object.assign(state,v)},stop:async()=>calls.push('stop'),resume:async()=>calls.push('resume'),chrome:{tabs:{query:async()=>[{id:7,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}],sendMessage:async()=>({status:403,page_state:'app_recheck'}),reload:async()=>calls.push('reload')}}};return{state,calls,d,run:()=>recoverRecheck(d),cancel:()=>active=false};}
test('only exact app recheck can reserve one normal reload',async()=>{const f=fixture();await f.run();assert.deepEqual(f.calls,['store','reload']);assert.equal(f.state.recheckRecovery.pendingAt,NOW);await f.run();assert.equal(f.calls.filter(x=>x==='reload').length,1);});
test('healthy authenticated page resumes once and next incident stops without another reload',async()=>{const f=fixture();await f.run();f.d.now+=5000;f.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:true});await f.run();assert.equal(f.calls.at(-1),'resume');assert.equal(f.state.recheckRecovery.pendingAt,null);await f.run();assert.equal(f.calls.at(-1),'stop');assert.equal(f.calls.filter(x=>x==='reload').length,1);});
test('deadline, missing owner, unclassified rejection and challenge cannot trigger a reload',async()=>{for(const change of [f=>{f.state.recheckRecovery={attempted:true,pendingAt:NOW-45001,tabId:7}},f=>{f.d.chrome.tabs.query=async()=>[]},f=>{f.d.chrome.tabs.sendMessage=async()=>({status:403})},f=>{f.d.chrome.tabs.sendMessage=async()=>({status:403,page_state:'challenge'})}]){const f=fixture();change(f);await f.run();assert.ok(f.calls.includes('stop'));assert.ok(!f.calls.includes('reload'));}});
test('default off, busy and cooldown make no navigation; stop during probe/store prevents reload',async()=>{for(const change of [f=>f.state.autoRecheck=false,f=>f.d.busy=true,f=>f.state.apiCooldownUntil=NOW+10000]){const f=fixture();change(f);await f.run();assert.ok(!f.calls.includes('reload'));}for(const point of ['probe','store']){const f=fixture();if(point==='probe')f.d.chrome.tabs.sendMessage=async()=>{f.cancel();return{status:403,page_state:'app_recheck'}};else f.d.store=async()=>f.cancel();await f.run();assert.ok(!f.calls.includes('reload'));}});
test('pending recovery never navigates challenge and never resumes a logged out page',async()=>{const f=fixture();await f.run();f.d.now+=5000;f.d.chrome.tabs.sendMessage=async()=>({status:403,page_state:'challenge'});await f.run();assert.equal(f.calls.filter(x=>x==='reload').length,1);f.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:false});await f.run();assert.equal(f.calls.at(-1),'stop');});
test('late successful probe and unanswered probe cannot exceed readiness deadline',async()=>{
 const f=fixture();await f.run();f.d.clock=()=>f.d.now;f.d.chrome.tabs.sendMessage=async()=>{f.d.now+=45001;return{status:200,ready:true,authenticated:true}};await f.run();assert.equal(f.calls.at(-1),'stop');assert.ok(!f.calls.includes('resume'));
 const g=fixture();await g.run();g.d.now+=44999;g.d.clock=()=>g.d.now;g.d.chrome.tabs.sendMessage=()=>new Promise(()=>{});await g.run();assert.equal(g.calls.at(-1),'stop');
});
test('API rejection can precede the app modal: wait boundedly without requests or early reload',async()=>{
 const f=fixture();f.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:true});await f.run();assert.equal(f.state.recheckRecovery.attempted,false);assert.equal(f.state.recheckRecovery.observedAt,NOW);assert.ok(!f.calls.includes('reload'));assert.ok(!f.calls.includes('stop'));
 f.d.now+=5000;f.d.chrome.tabs.sendMessage=async()=>({status:403,page_state:'app_recheck'});await f.run();assert.equal(f.calls.at(-1),'reload');assert.equal(f.state.recheckRecovery.attempted,true);
 const g=fixture();g.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:true});await g.run();g.d.now+=15000;await g.run();assert.equal(g.calls.at(-1),'stop');assert.ok(!g.calls.includes('reload'));
});
test('explicit challenged-response marker permits one normal page reload without waiting for modal',async()=>{
 const f=fixture();f.state.blockedCause='cloudflare_challenge';f.state.blockedAt=NOW;f.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:true});await f.run();assert.equal(f.calls.at(-1),'reload');assert.equal(f.state.recheckRecovery.attempted,true);
 const g=fixture();g.state.blockedCause='cloudflare_challenge';g.state.blockedAt=NOW;g.d.chrome.tabs.sendMessage=async()=>({status:403,page_state:'challenge'});await g.run();assert.ok(!g.calls.includes('reload'));assert.equal(g.calls.at(-1),'stop');
});

test('stale challenged-response marker does not grant an automatic reload',async()=>{const f=fixture();f.state.blockedCause='cloudflare_challenge';f.state.blockedAt=NOW-60001;f.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:true});await f.run();assert.ok(!f.calls.includes('reload'));assert.equal(f.state.recheckRecovery.attempted,false);});
test('a new incident after ten healthy minutes gets one new reload, but an immediate recurrence stops',async()=>{
 const f=fixture();f.state.repeatRecheck=true;await f.run();f.d.now+=5000;f.d.chrome.tabs.sendMessage=async()=>({status:200,ready:true,authenticated:true});await f.run();const done=f.state.recheckRecovery.completedAt;
 f.d.now=done+601010;Object.assign(f.state,{blockedAt:f.d.now,blockedCause:'cloudflare_challenge',apiHealthySince:done+1,lastApiHealthyAt:f.d.now-1000});await f.run();assert.equal(f.calls.filter(x=>x==='reload').length,2);assert.equal(f.state.recheckRecovery.reloadCount,2);
 f.d.now+=1000;await f.run();f.state.blockedAt=f.d.now;await f.run();assert.equal(f.calls.at(-1),'stop');assert.equal(f.calls.filter(x=>x==='reload').length,2);
});

test('terminal reason is reported before disconnect; failed diagnostics still stop',async()=>{
 for (const fails of [false, true]) {
  const f=fixture();f.state.recheckRecovery={attempted:true,pendingAt:NOW-45000,tabId:7,reloadCount:2};
  f.d.report=async result=>{f.calls.push(result);if(fails)throw Error('disconnected');};
  await f.run();
  assert.deepEqual(f.calls,[ 'store', {category:'recheck',reason:'recovery_timeout',reload_count:2}, 'stop' ]);
 }
});
test('cancellation during diagnostic delivery cannot stop a new session',async()=>{
 const f=fixture();f.state.recheckRecovery={attempted:true,pendingAt:NOW-45000,tabId:7};
 f.d.report=async()=>f.cancel();await f.run();assert.ok(!f.calls.includes('stop'));
});
test('a failed reload reports the newly reserved attempt count',async()=>{
 const f=fixture();f.d.chrome.tabs.reload=async()=>{throw Error('reload failed');};let diagnostic;
 f.d.report=async value=>{diagnostic=value;};await f.run();
 assert.equal(diagnostic.reason,'reload_failed');assert.equal(diagnostic.reload_count,1);assert.equal(f.state.recheckRecovery.reloadCount,1);
});
