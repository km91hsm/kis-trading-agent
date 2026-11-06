import test from 'node:test';
import assert from 'node:assert/strict';
import { beginHtmlTrial, htmlTrialStep } from '../html-refresh.js';
const TOKEN='a'.repeat(32), BASE=1000000, MIN=60000;
function fixture(overrides={}) {
 const state={htmlRefreshTest:true,htmlRefreshTrial:beginHtmlTrial(BASE),lastApiHealthyAt:BASE+4*MIN},calls=[];let current=true;
 const deps={state,token:TOKEN,now:BASE+4*MIN,active:()=>current,busy:false,
 store:async value=>{calls.push({type:'store',value});Object.assign(state,value);},
 halt:async result=>{calls.push({type:'halt',...result});},blockHost:async()=>{calls.push({type:'block'});},
 chrome:{tabs:{query:async()=>{calls.push({type:'query'});return [{id:7,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}];},sendMessage:async(id,message)=>{calls.push({type:'probe',id,message});return {status:200,ready:true};},reload:async id=>{calls.push({type:'reload',id});}}},...overrides};
 return {state,calls,deps,stop:()=>{current=false;},run:()=>htmlTrialStep(deps)};
}
test('default off, not due, busy and cooldown do not navigate',async()=>{
 for(const change of [{htmlRefreshTest:false},{htmlRefreshTrial:beginHtmlTrial(BASE+MIN)},{apiCooldownUntil:BASE+11*MIN},{hostBlocked:true},{blockedLanes:['news']}]){const f=fixture();Object.assign(f.state,change);await f.run();assert.ok(!f.calls.some(x=>x.type==='reload'||x.type==='probe'));}
 const f=fixture({busy:true});await f.run();assert.deepEqual(f.calls,[]);
});
test('healthy exact owned tab reserves one reload before navigation without focus',async()=>{
 const f=fixture();assert.equal(await f.run(),true);assert.deepEqual(f.calls.map(c=>c.type),['query','probe','store','reload']);assert.equal(f.calls.at(-1).id,7);assert.equal(f.state.htmlRefreshTrial.reloadCount,1);assert.equal(f.state.htmlRefreshTrial.pendingAt,f.deps.now);
 const restarted=fixture({state:f.state,now:f.deps.now+5000});await restarted.run();assert.ok(!restarted.calls.some(x=>x.type==='reload'));assert.equal(restarted.state.htmlRefreshTrial.pendingAt,null);
});
test('expired trial, invalid state, exhausted budget and stale health stop before navigation',async()=>{
 const cases=[f=>f.deps.now=BASE+35*MIN,f=>f.state.htmlRefreshTrial.startedAt=f.deps.now+1,f=>f.state.htmlRefreshTrial.reloadCount=8,f=>f.state.lastApiHealthyAt=BASE];
 for(const change of cases){const f=fixture();change(f);await f.run();assert.ok(f.calls.some(x=>x.type==='halt'));assert.ok(!f.calls.some(x=>x.type==='reload'||x.type==='query'));}
});
test('challenge probe blocks whole host and never reloads',async()=>{
 const f=fixture();f.deps.chrome.tabs.sendMessage=async()=>({status:403});await f.run();assert.ok(f.calls.some(x=>x.type==='block'));assert.ok(f.calls.some(x=>x.type==='halt'&&x.status==='failed'));assert.ok(!f.calls.some(x=>x.type==='reload'));
});
test('missing/ambiguous owner and challenge query stop without probing or reloading',async()=>{
 for(const tabs of [[],[{id:1,url:'https://www.saveticker.com/news#personal'}],[{id:1,url:'https://www.saveticker.com/news?challenge=1#trader-saveticker-api='+TOKEN}],[{id:1,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN},{id:2,url:'https://www.saveticker.com/news#trader-saveticker-api='+TOKEN}]]){const f=fixture();f.deps.chrome.tabs.query=async()=>tabs;await f.run();assert.ok(f.calls.some(x=>x.type==='halt'));assert.ok(!f.calls.some(x=>x.type==='reload'||x.type==='probe'));}
});
test('stop during probe or persisted reservation prevents reload',async()=>{
 for(const point of ['probe','store']){const f=fixture();if(point==='probe')f.deps.chrome.tabs.sendMessage=async()=>{f.stop();return {status:200,ready:true};};else f.deps.store=async value=>{Object.assign(f.state,value);f.stop();};await f.run();assert.ok(!f.calls.some(x=>x.type==='reload'));}
});
test('unavailable content after reload waits boundedly then stops without retrying navigation',async()=>{
 const f=fixture();f.state.htmlRefreshTrial={...f.state.htmlRefreshTrial,reloadCount:1,pendingAt:f.deps.now-10000,pendingTabId:7,lastReloadAt:f.deps.now-10000};f.deps.chrome.tabs.sendMessage=async()=>{throw Error('no listener');};await f.run();assert.ok(!f.calls.some(x=>x.type==='halt'||x.type==='reload'));
 f.deps.now+=30000;await f.run();assert.ok(f.calls.some(x=>x.type==='halt'));assert.ok(!f.calls.some(x=>x.type==='reload'));
});
test('late worker recovery after reload deadline stops even if a page now looks ready',async()=>{
 const f=fixture();f.state.htmlRefreshTrial={...f.state.htmlRefreshTrial,reloadCount:1,lastReloadAt:f.deps.now-31000,pendingAt:f.deps.now-31000,pendingTabId:7};await f.run();assert.ok(f.calls.some(x=>x.type==='halt'));assert.ok(!f.calls.some(x=>x.type==='probe'||x.type==='reload'));
});
test('challenge query persists whole-host barrier rather than allowing ordinary start',async()=>{
 const f=fixture();f.deps.chrome.tabs.query=async()=>[{id:7,url:'https://www.saveticker.com/news?__cf_chl_rt_tk=test#trader-saveticker-api='+TOKEN}];await f.run();assert.ok(f.calls.some(x=>x.type==='block'));assert.ok(!f.calls.some(x=>x.type==='reload'));
});

test('four minute boundary and eight reloads stay inside the thirty five minute trial',async()=>{
 const f=fixture();f.deps.now=BASE+4*MIN-1;await f.run();assert.equal(f.calls.length,0);
 for(let n=1;n<=8;n++){
  f.deps.now=BASE+n*4*MIN;f.state.lastApiHealthyAt=f.deps.now;await f.run();
  assert.equal(f.state.htmlRefreshTrial.reloadCount,n);f.deps.now+=1000;await f.run();
 }
 assert.equal(f.calls.filter(x=>x.type==='reload').length,8);
 f.deps.now=BASE+35*MIN;await f.run();assert.ok(f.calls.some(x=>x.type==='halt'&&x.status==='finished'));
});
test('a failed reload retains the reserved attempt count in its terminal result',async()=>{
 const f=fixture();f.deps.chrome.tabs.reload=async()=>{throw Error('reload failed');};await f.run();
 const result=f.calls.find(x=>x.type==='halt');assert.equal(result.reason,'reload_failed');assert.equal(result.reloadCount,1);
});
