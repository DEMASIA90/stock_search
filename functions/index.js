import { onCall, HttpsError } from 'firebase-functions/v2/https';
import { initializeApp } from 'firebase-admin/app';
import { getFirestore } from 'firebase-admin/firestore';
import { randomInt } from 'node:crypto';
import { ACTIONS, applyAction, nextTurnIndex, living } from './engine.js';

initializeApp();
const db = getFirestore();
const OPTS={region:'asia-northeast3',memory:'256MiB',maxInstances:10};
const ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const CATEGORIES={science:1200,english:1200,history:1200,general:1204};
const cleanName = name => String(name||'').trim().slice(0,16).replace(/[<>]/g,'');
const authUid = request => {
  if (!request.auth?.uid) throw new HttpsError('unauthenticated','익명 로그인이 필요합니다.');
  return request.auth.uid;
};
const roomCode = value => String(value||'').trim().toUpperCase();
const requireMember = (room,uid) => {
  if (!room.memberUids.includes(uid)) throw new HttpsError('permission-denied','참가자가 아닙니다.');
};
const fail = (code,message) => {throw new HttpsError(code,message);};
const event = (room,message,kind='system') => [{id:`${Date.now()}-${randomInt(1e6)}`,time:Date.now(),message,kind},...(room.events||[])].slice(0,14);
const getRoomRef = data => {
  const code=roomCode(data.code);
  if (!/^[A-HJ-NP-Z2-9]{6}$/.test(code)) fail('invalid-argument','6자리 방 코드를 확인하세요.');
  return db.collection('rooms').doc(code);
};

export const createRoom = onCall(OPTS,async request=>{
  const uid=authUid(request);
  const name=cleanName(request.data?.name);
  const capacity=Number(request.data?.capacity);
  if (!name || ![2,3,4].includes(capacity)) fail('invalid-argument','닉네임과 인원(2~4)을 확인하세요.');
  for(let attempt=0;attempt<8;attempt++) {
    const code=Array.from({length:6},()=>ALPHABET[randomInt(ALPHABET.length)]).join('');
    const room={code,capacity,hostUid:uid,memberUids:[uid],createdAt:Date.now(),status:'lobby',phase:'lobby',turnIndex:0,round:1,
      players:[{uid,name,seat:0,hp:100,shield:false,lastHit:null}],question:null,result:null,
      events:[{id:`${Date.now()}-host`,time:Date.now(),message:`${name} 작전실 개설`,kind:'system'}]};
    try { await db.collection('rooms').doc(code).create(room); return {code}; }
    catch(e) { if(e.code!==6 && e.code!=='already-exists') throw e; }
  }
  fail('resource-exhausted','방 코드 생성에 실패했습니다. 다시 시도하세요.');
});

export const joinRoom = onCall(OPTS,async request=>{
  const uid=authUid(request), ref=getRoomRef(request.data||{}), name=cleanName(request.data?.name);
  if(!name) fail('invalid-argument','닉네임이 필요합니다.');
  await db.runTransaction(async tx=>{
    const snap=await tx.get(ref);
    if(!snap.exists) fail('not-found','존재하지 않는 작전실입니다.');
    const room=snap.data();
    if(room.memberUids.includes(uid)) return;
    if(room.status!=='lobby') fail('failed-precondition','이미 시작된 게임입니다.');
    if(room.players.length>=room.capacity) fail('resource-exhausted','정원이 찼습니다.');
    const seat=room.players.length;
    tx.update(ref,{
      memberUids:[...room.memberUids,uid],
      players:[...room.players,{uid,name,seat,hp:100,shield:false,lastHit:null}],
      events:event(room,`${name} 합류 완료`)
    });
  });
  return {code:ref.id};
});

export const startGame = onCall(OPTS,async request=>{
  const uid=authUid(request),ref=getRoomRef(request.data||{});
  await db.runTransaction(async tx=>{
    const snap=await tx.get(ref); if(!snap.exists) fail('not-found','방이 없습니다.');
    const room=snap.data();
    requireMember(room,uid);
    if(room.hostUid!==uid) fail('permission-denied','방장만 시작할 수 있습니다.');
    if(room.status!=='lobby' || room.players.length<2) fail('failed-precondition','최소 2명이 필요합니다.');
    tx.update(ref,{status:'active',phase:'choose',round:1,turnIndex:0,turnStartedAt:Date.now(),events:event(room,'전투 개시! 첫 번째 플레이어의 턴입니다.','start')});
  });
  return {ok:true};
});

export const selectAction = onCall(OPTS,async request=>{
  const uid=authUid(request),ref=getRoomRef(request.data||{});
  const action=String(request.data?.action||'');
  if(!ACTIONS[action]) fail('invalid-argument','알 수 없는 행동입니다.');
  const cat=ACTIONS[action].category, i=randomInt(1,CATEGORIES[cat]+1), id=`${cat}-${String(i).padStart(4,'0')}`;
  const qref=db.collection('questions').doc(id);
  await db.runTransaction(async tx=>{
    const [snapshot,qSnap]=await Promise.all([tx.get(ref),tx.get(qref)]);
    if(!snapshot.exists) fail('not-found','방이 없습니다.');
    if(!qSnap.exists) fail('failed-precondition','문제 DB가 없습니다. README의 seed 과정을 진행하세요.');
    const room=snapshot.data(),actor=room.players[room.turnIndex];
    requireMember(room,uid);
    if(room.status!=='active' || room.phase!=='choose' || actor?.uid!==uid) fail('failed-precondition','현재 행동을 선택할 수 없습니다.');
    const q=qSnap.data();
    tx.update(ref,{phase:'quiz',selectedAction:action,
      question:{id,prompt:q.prompt,options:q.options,category:cat,pickedIndex:null,deadline:Date.now()+30000},
      result:null,events:event(room,`${actor.name} · ${ACTIONS[action].title} 선택`,'select')});
  });
  return {ok:true};
});

export const submitAnswer = onCall(OPTS,async request=>{
  const uid=authUid(request),ref=getRoomRef(request.data||{});
  const picked=Number(request.data?.option);
  if(!Number.isInteger(picked) || picked < -1 || picked>3) fail('invalid-argument','보기 선택이 잘못되었습니다.');
  let lastQuestionId,actorUid;
  await db.runTransaction(async tx=>{
    const snap=await tx.get(ref);if(!snap.exists) fail('not-found','방이 없습니다.');
    const room=snap.data();requireMember(room,uid);
    if(room.status!=='active' || room.phase!=='quiz') fail('failed-precondition','답변 가능한 문제가 없습니다.');
    actorUid=room.players[room.turnIndex].uid;
    const expired=Date.now()>=room.question.deadline;
    if(!expired && (uid!==actorUid || picked===-1)) fail('permission-denied','현재 플레이어만 답할 수 있습니다.');
    if(!expired && picked===-1) fail('failed-precondition','아직 제한시간이 남았습니다.');
    // 30초 초과는 정답 선택을 허용하지 않음. 참가자 누구나 타임아웃 처리가 가능.
    const selected=expired ? -1 : picked;
    lastQuestionId=room.question.id;
    tx.update(ref,{phase:'locked',question:{...room.question,pickedIndex:selected},lockAt:Date.now()});
  });
  // Give all observers a real-time intermediate frame showing the selection.
  await new Promise(resolve=>setTimeout(resolve,1050));
  await db.runTransaction(async tx=>{
    const snap=await tx.get(ref);
    if(!snap.exists) return;
    const room=snap.data();
    if(room.phase!=='locked' || room.question?.id!==lastQuestionId || room.players[room.turnIndex]?.uid!==actorUid) return;
    const qSnap=await tx.get(db.collection('questions').doc(lastQuestionId));
    if(!qSnap.exists) fail('failed-precondition','문제 데이터 누락');
    const correctIndex=qSnap.data().correctIndex;
    const correct=room.question.pickedIndex===correctIndex;
    const actor=room.players[room.turnIndex];
    const effects= correct ? applyAction(room.players,room.turnIndex,room.selectedAction) :
      {players:room.players,effects:[],summary:`${actor.name} · 오답! 작전이 취소되었습니다.`,ended:false,winner:null};
    const result={correct,pickedIndex:room.question.pickedIndex,correctIndex,
      action:room.selectedAction,effects:effects.effects,summary:effects.summary,actorUid:actor.uid,at:Date.now()};
    tx.update(ref,{phase:'reveal',status:effects.ended?'finished':'active',players:effects.players,result,
      revealUntil:Date.now()+4000,
      ...(effects.ended?{winnerUid:effects.winner}:{ }),
      events:event(room,effects.ended ? `${effects.summary} ${effects.winner ? '최후의 생존자가 결정되었습니다!' : '전원 전멸! 무승부입니다.'}` : effects.summary,correct?'attack':'miss')});
  });
  return {ok:true};
});

export const advanceTurn = onCall(OPTS,async request=>{
  const uid=authUid(request),ref=getRoomRef(request.data||{});
  await db.runTransaction(async tx=>{
    const snap=await tx.get(ref);if(!snap.exists) fail('not-found','방이 없습니다.');
    const room=snap.data();requireMember(room,uid);
    if(room.status!=='active' || room.phase!=='reveal') return;
    if(Date.now()<room.revealUntil) return;
    const idx=nextTurnIndex(room.players,room.turnIndex);
    if(idx<0) return;
    const round=room.round+(idx<=room.turnIndex?1:0);
    tx.update(ref,{phase:'choose',turnIndex:idx,round,question:null,result:null,selectedAction:null,turnStartedAt:Date.now()});
  });
  return {ok:true};
});
