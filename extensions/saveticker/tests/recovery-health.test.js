import test from 'node:test';
import assert from 'node:assert/strict';
import { recordHealth, repeatEligible } from '../recovery-health.js';
const NOW=2000000;
test('successful cadence persists across restart, but gaps and backward clock reset health',()=>{
 const h=recordHealth({},NOW);assert.equal(h.apiHealthySince,NOW);assert.equal(h.lastApiHealthyAt,NOW);
 assert.equal(recordHealth(h,NOW+60000).apiHealthySince,NOW);
 assert.equal(recordHealth(h,NOW+120001).apiHealthySince,NOW+120001);
 assert.equal(recordHealth(h,NOW-1).apiHealthySince,NOW-1);
});
test('repeat requires ten healthy minutes after successful recovery and a fresh distinct incident',()=>{
 const old={attempted:true,pendingAt:null,reason:'recovered',completedAt:NOW-700000};
 const state={repeatRecheck:true,blockedAt:NOW,apiHealthySince:NOW-601000,lastApiHealthyAt:NOW-1000};
 assert.equal(repeatEligible(state,old,NOW),true);
 for(const patch of [{repeatRecheck:false},{apiHealthySince:NOW-600999},{apiHealthySince:NOW-800000},{lastApiHealthyAt:NOW-120001},{blockedAt:NOW-60001},{blockedAt:NOW+1}])assert.equal(repeatEligible({...state,...patch},old,NOW),false);
 assert.equal(repeatEligible(state,{...old,reason:'recovery_timeout'},NOW),false);
 assert.equal(repeatEligible(state,{...old,pendingAt:NOW-1},NOW),false);
});

test('eight minutes of saves plus two idle minutes cannot rearm recovery',()=>{const old={reason:'recovered',pendingAt:null,completedAt:NOW-700000};assert.equal(repeatEligible({repeatRecheck:true,blockedAt:NOW,apiHealthySince:NOW-600000,lastApiHealthyAt:NOW-120000},old,NOW),false);});
