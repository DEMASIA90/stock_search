import test from 'node:test';
import assert from 'node:assert/strict';
import {applyAction,nextTurnIndex} from '../functions/engine.js';
const team=(hp=[100,100,100])=>hp.map((n,i)=>({uid:`u${i}`,name:`P${i}`,seat:i,hp:n,shield:false,lastHit:null}));
test('nuclear damages ALL living players including the launcher',()=>{
  const r=applyAction(team(),0,'nuclear'); assert.deepEqual(r.players.map(p=>p.hp),[95,95,95]);
});
test('nuclear shield reduces 5 to 2.5 and is consumed',()=>{
  const ps=team();ps[1].shield=true;
  const r=applyAction(ps,0,'nuclear'); assert.equal(r.players[1].hp,97.5);assert.equal(r.players[1].shield,false);
});
test('ICBM targets strongest opponent; tie broken by seat, shield blocks fully',()=>{
  let ps=team([100,100,90]);ps[1].shield=true;
  const r=applyAction(ps,0,'icbm');assert.deepEqual(r.players.map(p=>p.hp),[100,100,90]);assert.equal(r.players[1].shield,false);
});
test('repair only consumes latest real damage and cannot be spammed',()=>{
  let ps=team();ps=applyAction(ps,0,'nuclear').players;
  ps=applyAction(ps,0,'repair').players;assert.equal(ps[0].hp,97.5);
  ps=applyAction(ps,0,'repair').players;assert.equal(ps[0].hp,97.5);
});
test('ICBM repair restores 100% of actual damage',()=>{
  let ps=team();ps=applyAction(ps,0,'icbm').players;
  ps=applyAction(ps,1,'repair').players;assert.equal(ps[1].hp,100);
});
test('single survivor is winner and simultaneous wipe is draw',()=>{
  const win=applyAction(team([100,5,5]),0,'nuclear');assert.equal(win.ended,true);assert.equal(win.winner,'u0');
  const draw=applyAction(team([5,5,5]),0,'nuclear');assert.equal(draw.ended,true);assert.equal(draw.winner,null);
});
test('nextTurnIndex skips eliminated players',()=>{
  assert.equal(nextTurnIndex(team([100,0,50]),0),2);
});
