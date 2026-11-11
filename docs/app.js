const $ = s => document.querySelector(s);
const COLORS=['#79dec1','#e7bd7b','#90baff','#ff947b'];
const ACTIONS={
 nuclear:{name:'트라이던트 II 핵공격',category:'SCIENCE',ko:'과학',glyph:'☢',color:'#4fdbc9',desc:'전 플레이어 HP -5 (자신 포함)'},
 icbm:{name:'미니트맨 III ICBM',category:'ENGLISH',ko:'영어',glyph:'➤',color:'#fe916b',desc:'체력 최상위 적 1명 HP -10'},
 defense:{name:'아이언돔 방어체계',category:'HISTORY',ko:'역사',glyph:'⛨',color:'#90baff',desc:'다음 핵 50% · ICBM 100% 방어'},
 repair:{name:'전투공병 긴급복구',category:'GENERAL',ko:'일반상식',glyph:'✚',color:'#bbef9f',desc:'마지막 핵피해 50% · ICBM 100% 복구'}
};
const CAT_KO={science:'과학',english:'영어',history:'역사',general:'일반상식'};
const escapes={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>escapes[c]);
const DEMO_BANK={
 science:[
 ['물의 화학식은 무엇인가요?',['H₂O','CO₂','NaCl','O₂'],0],
 ['진공 속 빛의 속도에 가장 가까운 값은?',['초속 3천 km','초속 30만 km','초속 3만 km','초속 3백만 km'],1],
 ['지구의 유일한 자연 위성은?',['화성','금성','달','태양'],2],
 ['전기 저항의 SI 단위는?',['암페어','와트','볼트','옴'],3],
 ['식물이 광합성할 때 주로 흡수하는 기체는?',['이산화탄소','산소','헬륨','질소'],0],
 ['원자번호 6번 원소는?',['철','탄소','산소','규소'],1]],
 english:[
 ['“resilience”에 가장 가까운 뜻은?',['호기심','회복탄력성','가능성','우연'],1],
 ['“협상”에 해당하는 영어 단어는?',['negotiation','generation','observation','distribution'],0],
 ['빈칸에 알맞은 말: She ___ to school every day.',['go','going','goes','gone'],2],
 ['“temporary”의 반의어는?',['brief','short','quick','permanent'],3],
 ['“accurate”의 뜻은?',['정확한','피곤한','유쾌한','성급한'],0],
 ['“지속가능성”의 영어 표현은?',['sensitivity','sustainability','security','simplicity'],1]],
 history:[
 ['훈민정음이 반포된 해는?',['1392년','1418년','1446년','1592년'],2],
 ['프랑스 혁명이 시작된 해는?',['1789년','1815년','1914년','1776년'],0],
 ['대한민국 정부가 수립된 해는?',['1919년','1945년','1950년','1948년'],3],
 ['아폴로 11호가 달에 착륙한 해는?',['1957년','1969년','1975년','1981년'],1],
 ['다음 중 가장 먼저 일어난 사건은?',['제1차 세계대전','제2차 세계대전','한국전쟁','프랑스 혁명'],3],
 ['베를린 장벽이 붕괴된 해는?',['1989년','1979년','1999년','1991년'],0]],
 general:[
 ['캐나다의 수도는?',['토론토','밴쿠버','오타와','몬트리올'],2],
 ['1 km는 몇 미터인가요?',['10m','100m','1000m','10000m'],2],
 ['태평양과 대서양 중 면적이 더 큰 바다는?',['대서양','태평양','면적이 같다','알 수 없다'],1],
 ['피사의 사탑이 위치한 나라는?',['이탈리아','프랑스','독일','스페인'],0],
 ['일주일은 며칠로 이루어져 있나요?',['5일','6일','8일','7일'],3],
 ['국제연합(UN) 본부 소재 도시는?',['런던','뉴욕','제네바','파리'],1]]
};
let mode='demo',room=null,myUid='local',online=null,unsub=null,lastResultAt=null,pending=false,toastTimer=null;
let autoTimeoutKey='',autoAdvanceKey='',quizTimer=null,demoBusy=false;
const capCount=()=>room?.capacity||4;
const myPlayer=()=>room?.players.find(p=>p.uid===myUid);
const currentPlayer=()=>room?.players?.[room.turnIndex];
const mine=()=>room?.status==='active' && currentPlayer()?.uid===myUid;
const koHP=n=>Number.isInteger(n)?String(n):n.toFixed(1);
const isConnected=()=>!!(window.KB_FIREBASE_CONFIG?.apiKey&&window.KB_FIREBASE_CONFIG?.projectId&&window.KB_FIREBASE_CONFIG?.appId);
function toast(msg,bad=false){clearTimeout(toastTimer);const node=$('#toast');node.textContent=msg;node.className='toast'+(bad?' error':'');toastTimer=setTimeout(()=>node.classList.add('hidden'),3400);}
function safeName(){const val=$('#nickname').value.trim().slice(0,16);if(!val){toast('사령관 이름을 입력하세요.',true);throw new Error('닉네임 누락');}localStorage.setItem('kb-name',val);return val;}
function humanTime(ms){return new Date(ms).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false});}
function setRoomTag(){const tag=$('#room-tag');tag.innerHTML=`ROOM <b>${esc(room?.code||'DEMO')}</b> <span>⧉</span>`;}
async function copyCode(){const code=room?.code;if(!code||code==='DEMO')return;try{await navigator.clipboard.writeText(code);toast(`방 코드 ${code} 복사 완료`);}catch{toast(`방 코드: ${code}`);}}
$('#room-tag').addEventListener('click',copyCode);$('#lobby-code').addEventListener('click',copyCode);
function renderPlayers(){
 if(!room){$('#player-roster').innerHTML='<div class="intel-box">입장 후 전술 정보가 표시됩니다.</div>';return;}
 const cp=currentPlayer();
 $('#player-roster').innerHTML=room.players.map((p,i)=>{
  const color=COLORS[i],hp=koHP(p.hp),mineTag=p.uid===myUid?'YOU':`P${i+1}`;
  return `<div class="player-card ${cp?.uid===p.uid&&room.status==='active'?'active':''} ${p.hp===0?'eliminated':''}" style="--pc:${color}">
    <div class="player-row"><span class="player-id">◈ ${mineTag} / ${['ALPHA','BRAVO','CHARLIE','DELTA'][i]}</span><span class="player-badge">${p.hp===0?'LOST':p.shield?'SHIELD':'ONLINE'}</span></div>
    <div class="player-name">${esc(p.name)}</div><div class="hp-track"><div class="hp-fill" style="--hp:${p.hp}%"></div></div>
    <div class="hp-meta"><span class="hp-number">${hp}<small> / 100</small></span><span class="hp-label">BASE HEALTH</span></div>
    ${p.shield?'<div class="shield-chip">⛨ 방어 활성화</div>':''}</div>`;
 }).join('');
}
const sectorFor=(seat,capacity)=>capacity===2?[0,3][seat]:capacity===3?[0,1,3][seat]:seat;
const BUILDINGS=[['17%','33%','15%','18%',false],['38%','25%','11%','29%',true],['59%','36%','14%','19%',false],['24%','61%','13%','17%',false],['50%','60%','13%','20%',true],['71%','48%','8%','14%',false],['38%','46%','10%','13%',false]];
function islandMarkup(player,seat,capacity){
 const sector=sectorFor(seat,capacity),color=COLORS[seat];
 const damage=player.hp>=80?0:player.hp>=60?1:player.hp>=35?2:player.hp>=15?3:4;
 const buildings=BUILDINGS.map(([x,y,w,h,tower])=>`<div class="base-building ${tower?'tower':''}" style="--x:${x};--y:${y};--w:${w};--h:${h}"></div>`).join('');
 const craters=[[24,32,21],[57,64,16],[38,48,25],[62,35,17]].map(([x,y,s])=>`<i class="crater" style="--x:${x}%;--y:${y}%;--s:${s}%"></i>`).join('');
 const smoke=[[34,32],[55,36],[42,65],[66,56]].map(([x,y],i)=>`<i class="smoke-plume" style="--x:${x}%;--y:${y}%;--delay:${i*.35}s"></i>`).join('');
 const fire=[[39,45],[65,63],[22,65]].map(([x,y])=>`<i class="fire-spark" style="--x:${x}%;--y:${y}%"></i>`).join('');
 return `<div class="island-sector seat-${sector} damage-${damage} ${player.hp===0?'dead':''} ${room?.status==='active'&&currentPlayer()?.uid===player.uid?'my-turn':''}" id="island-${esc(player.uid)}" style="--pc:${color}">
  <div class="island-body"><div class="land-inner"></div><div class="terrain-road"></div><div class="city">${buildings}</div><div>${craters}</div><div>${fire}</div><div>${smoke}</div></div>
  <div class="island-hud"><div class="label"><span class="marker"></span><div><small>${['ALPHA','BRAVO','CHARLIE','DELTA'][seat]}</small><br><strong>${esc(player.name)}</strong></div></div><div class="hp">${koHP(player.hp)} <small>HP</small></div></div></div>`;
}
function renderMap(){
 if(!room){$('#island-container').innerHTML='';return;}
 $('#island-container').innerHTML=room.players.map((p,i)=>islandMarkup(p,i,room.capacity)).join('');
}
function renderActions(){
 if(room?.status==='finished'){
  $('#action-list').innerHTML=`<div class="intel-box" style="margin:0 0 10px"><span class="intel-icon">★</span><div><strong>${room.winnerUid?esc(room.players.find(p=>p.uid===room.winnerUid)?.name)+' 승리':'무승부'}</strong><p>모든 전투가 종료되었습니다.</p></div></div><button class="primary-button" id="new-battle">새로운 전투 시작 →</button>`;
  $('#new-battle').addEventListener('click',()=>{history.replaceState({},'',location.pathname);location.reload();});return;
 }
 const canSelect=mine()&&room.phase==='choose'&&!pending;
 $('#action-list').innerHTML=Object.entries(ACTIONS).map(([k,a],i)=>`<button class="action-card" data-action="${k}" style="--ac:${a.color}" ${canSelect?'':'disabled'}>
 <div class="action-glyph">${a.glyph}</div><div class="action-text"><div class="action-top"><span class="num">0${i+1} / COMMAND</span><span class="action-category">${a.category}</span></div><div class="action-title">${a.name}</div><div class="action-desc">${a.ko} 퀴즈 · ${a.desc}</div></div></button>`).join('');
 $$('#action-list button').forEach(el=>el.addEventListener('click',()=>selectAction(el.dataset.action)));
}
function $$(s){return [...document.querySelectorAll(s)];}
function renderLog(){
 $('#battle-log').innerHTML=(room?.events||[]).map(ev=>`<div class="log-entry ${esc(ev.kind||'')}"><div class="log-icon">${ev.kind==='attack'?'◆':ev.kind==='miss'?'×':'◇'}</div><div class="log-body">${esc(ev.message)}<div class="log-time">${humanTime(ev.time)} · COMMS</div></div></div>`).join('') || '<div class="log-entry">기록 없음</div>';
}
function renderHeader(){
 const playing=room?.status==='active',finished=room?.status==='finished';
 $('#status-label').innerHTML=`<span class="live-dot"></span>${!room?' 대기 중':finished?' 전투 종료':playing?' 교전 중':' 대기실'}`;
 $('#round-label').textContent=room?.round?String(room.round).padStart(2,'0'):'—';
 $('#alive-label').textContent=room?`${room.players.filter(p=>p.hp>0).length} / ${room.players.length}`:'—';
 setRoomTag();
 const cp=currentPlayer();$('#turn-name').textContent=finished?(room.winnerUid?`${room.players.find(p=>p.uid===room.winnerUid)?.name} 승리`:'무승부'):cp?.name||'플레이어 대기 중';
 $('#turn-hint').textContent=finished?'최종 작전이 종료되었습니다.':room?.phase==='quiz'?'퀴즈 진행 중':room?.phase==='reveal'?'정답 및 전투 결과 공개':mine()?'명령을 선택하세요.':'다른 플레이어의 턴입니다.';
 $('#footer-turn').textContent=finished?'FINAL RESULT':room?.phase==='quiz'?`${cp?.name} 퀴즈 풀이 중`:room?.status==='active'?`${cp?.name} · TURN ${room.round}`:'작전 준비 중';
 $('#signal-text').textContent=mode==='demo'?'AI 훈련망 연결':'Firebase 실시간 동기화';
}
function renderQuiz(){
 const el=$('#quiz-overlay'),resEl=$('#result-overlay');
 if(!room || !['quiz','locked','reveal'].includes(room.phase)){el.classList.add('hidden');resEl.classList.add('hidden');return;}
 const q=room.question;if(!q){el.classList.add('hidden');return;}
 const r=room.result,revealed=room.phase==='reveal';
 const afterReveal=revealed&&(Date.now()-(r?.at||0)>2400);
 resEl.classList.toggle('hidden',!afterReveal);
 if(afterReveal){
  el.classList.add('hidden');
  resEl.innerHTML=`<strong>${r.correct?'MISSION COMPLETE':'MISSION FAILED'}</strong><span>${esc(r.summary)}</span>`;
  return;
 }
 el.classList.remove('hidden');
 const eligible=mine()&&room.phase==='quiz'&&!pending&&Date.now()<q.deadline;
 const options=q.options.map((opt,i)=>{
  const picked=q.pickedIndex===i,hit=revealed&&r?.correctIndex===i;
  const wrong=revealed&&picked&&!r.correct;
  return `<button class="quiz-opt ${picked?'selected':''} ${hit?'correct':''} ${wrong?'wrong':''}" data-option="${i}" ${eligible?'':'disabled'}>
  <span class="opt-letter">${'ABCD'[i]}</span><span>${esc(opt)}</span></button>`;
 }).join('');
 const sec=room.phase==='quiz'?Math.max(0,Math.ceil((q.deadline-Date.now())/1000)):0;
 const statusText=revealed ? (r.correct?'정답! 작전 실행 완료':'오답! 작전 실패') : room.phase==='locked'?'선택한 답안을 전체 플레이어에게 공개 중':eligible?'정답을 선택하면 작전이 실행됩니다.':`${esc(currentPlayer()?.name)} 사령관의 선택을 관전 중`;
 el.innerHTML=`<div class="quiz-card"><div class="quiz-topline"><span class="quiz-class">◈ CLASSIFIED INTELLIGENCE / ${esc(CAT_KO[q.category]||q.category)} QUIZ</span><div class="quiz-timer ${sec<=8?'urgent':''}" id="quiz-countdown">${room.phase==='quiz'?String(sec).padStart(2,'0')+'s':revealed?'RESULT':'LOCKED'}</div></div>
 <div class="quiz-command">COMMAND · ${esc(ACTIONS[room.selectedAction]?.name)} / ${esc(currentPlayer()?.name)}</div>
 <h2>${esc(q.prompt)}</h2><div class="options-grid">${options}</div>
 <div class="quiz-footer"><span class="${revealed?'quiz-result'+(!r.correct?' bad':''):''}">${statusText}</span><span class="quiz-dots"><i></i><i></i><i></i><i></i></span></div></div>`;
 $$('#quiz-overlay .quiz-opt').forEach(el=>el.addEventListener('click',()=>submitAnswer(Number(el.dataset.option))));
}
function renderLobby(){
 const isLobby=mode==='online' && room?.status==='lobby';$('#lobby-backdrop').classList.toggle('hidden',!isLobby);
 if(!isLobby) return;
 $('#lobby-code').innerHTML=`${esc(room.code)} <span>⧉</span>`;
 $('#lobby-players').innerHTML=room.players.map((p,i)=>`<div class="lobby-person" style="--pc:${COLORS[i]}"><i></i>${esc(p.name)} ${p.uid===room.hostUid?'· HOST':''} ${p.uid===myUid?'· YOU':''}</div>`).join('');
 $('#lobby-count').textContent=`연결됨 ${room.players.length}/${room.capacity} · 최소 2명 참가 시 시작 가능`;
 $('#start-button').disabled=room.hostUid!==myUid||room.players.length<2||pending;
}
function render(){renderHeader();renderPlayers();renderMap();renderActions();renderLog();renderQuiz();renderLobby();}
function addEvent(text,kind='system') {room.events=[{id:Date.now()+'-'+Math.random(),time:Date.now(),message:text,kind},...(room.events||[])].slice(0,14);}
function nextIdx(){for(let n=1;n<=room.players.length;n++){const idx=(room.turnIndex+n)%room.players.length;if(room.players[idx].hp>0)return idx;}return -1;}
function demoStrike(player,kind,raw){if(player.hp===0)return 0;const damage=player.shield?(kind==='nuclear'?raw*.5:0):raw;const actual=Math.min(player.hp,damage);player.hp=Math.max(0,Math.round((player.hp-actual)*2)/2);if(player.shield)player.shield=false;if(actual>0)player.lastHit={kind,damage:actual};return actual;}
function demoApply(action){
 const actor=room.players[room.turnIndex],effects=[];let summary='';
 if(action==='nuclear'){
  room.players.forEach(p=>{const shield=p.shield,damage=demoStrike(p,'nuclear',5);if(damage||shield)effects.push({uid:p.uid,damage,kind:'nuclear'});});
  summary=`${actor.name}의 트라이던트 II 핵공격! 모든 기지에 충격파.`;
 }else if(action==='icbm'){
  const enemy=room.players.filter(p=>p.uid!==actor.uid&&p.hp>0).sort((a,b)=>b.hp-a.hp||a.seat-b.seat)[0];
  if(enemy){const damage=demoStrike(enemy,'icbm',10);effects.push({uid:enemy.uid,damage,kind:'icbm'});summary=`${actor.name}의 ICBM이 ${enemy.name} 기지를 공격!`;}else summary='공격 가능한 상대가 없습니다.';
 }else if(action==='defense'){
  actor.shield=true;effects.push({uid:actor.uid,shield:true});summary=`${actor.name}이 방어체계를 구축했습니다.`;
 }else{
  const hit=actor.lastHit;const heal=hit?Math.min(100-actor.hp,hit.damage*(hit.kind==='nuclear'?.5:1)):0;
  if(hit)actor.lastHit=null;actor.hp=Math.min(100,Math.round((actor.hp+heal)*2)/2);
  effects.push({uid:actor.uid,heal});summary=heal?`${actor.name} 기지 HP +${heal} 복구`:'복구 가능한 최근 피해가 없습니다.';
 }
 const alive=room.players.filter(p=>p.hp>0);
 if(alive.length<=1){room.status='finished';room.winnerUid=alive.length===1?alive[0].uid:null;}
 return {effects,summary};
}
function chooseDemoQuestion(action){
 const cat={nuclear:'science',icbm:'english',defense:'history',repair:'general'}[action];
 const samples=DEMO_BANK[cat],q=samples[Math.floor(Math.random()*samples.length)];
 return {id:cat+'-demo-'+Math.random().toString(36).slice(2,6),category:cat,prompt:q[0],options:q[1],correctIndex:q[2],pickedIndex:null,deadline:Date.now()+30000};
}
async function beginDemo(){
 let name;try{name=safeName();}catch{return;}
 mode='demo';myUid='local';const cap=Number($('#capacity').value);
 room={code:'DEMO',capacity:cap,hostUid:myUid,memberUids:[myUid],status:'active',phase:'choose',turnIndex:0,round:1,
 players:Array.from({length:cap},(_,i)=>({uid:i===0?'local':'bot'+i,name:i===0?name:['ATLAS AI','PHANTOM AI','SPECTER AI'][i-1],seat:i,hp:100,shield:false,lastHit:null})),
 question:null,result:null,events:[]};
 addEvent('AI 워게임 시작 · 전술 훈련망 연결','start');$('#entry-backdrop').classList.add('hidden');history.replaceState({},'',location.pathname);
 render();toast('AI 전투 시작! 오른쪽에서 명령을 선택하세요.');
}
async function selectAction(action){
 if(!mine()||room.phase!=='choose'||pending)return;
 if(mode==='demo'){
  room.selectedAction=action;room.question=chooseDemoQuestion(action);room.phase='quiz';addEvent(`${currentPlayer().name} · ${ACTIONS[action].name} 선택`,'select');render();return;
 }
 await callOnline('selectAction',{code:room.code,action});
}
async function submitAnswer(option){
 if(!room||room.phase!=='quiz'||pending)return;
 if(mode==='online'){
  await callOnline('submitAnswer',{code:room.code,option});return;
 }
 if(!mine()&&option!==-1)return;
 demoAnswer(option);
}
async function demoAnswer(option){
 if(demoBusy||room.phase!=='quiz')return;
 demoBusy=true;
 const q=room.question,correct=option===q.correctIndex;
 q.pickedIndex=option;room.phase='locked';render();
 await new Promise(r=>setTimeout(r,1050));
 if(!room||room.question!==q){demoBusy=false;return;}
 const outcome=correct?demoApply(room.selectedAction):{summary:`${currentPlayer().name} · 오답! 작전이 취소되었습니다.`,effects:[]};
 room.result={correct,pickedIndex:option,correctIndex:q.correctIndex,action:room.selectedAction,effects:outcome.effects,summary:outcome.summary,actorUid:currentPlayer().uid,at:Date.now()};
 addEvent(outcome.summary,correct?'attack':'miss');room.phase='reveal';demoBusy=false;render();
 if(correct)setTimeout(()=>animateEffects(room?.result?.effects||[]),2600);
 const resultAt=room.result.at;
 setTimeout(()=>{if(!room||room.result?.at!==resultAt||room.status==='finished')return;const next=nextIdx();if(next<0)return;room.round+=next<=room.turnIndex?1:0;room.turnIndex=next;room.phase='choose';room.question=null;room.result=null;render();maybeBotTurn();},4600);
}
function maybeBotTurn(){
 if(mode!=='demo'||!room||room.status!=='active'||mine()||room.phase!=='choose')return;
 const actor=currentPlayer(),uid=actor.uid;
 setTimeout(()=>{
  if(!room||room.phase!=='choose'||currentPlayer()?.uid!==uid)return;
  const possible=actor.hp<75?['repair','icbm','nuclear','defense']:['nuclear','icbm','defense','repair'];
  const action=possible[Math.floor(Math.random()*possible.length)];room.selectedAction=action;room.question=chooseDemoQuestion(action);room.phase='quiz';addEvent(`${actor.name} · ${ACTIONS[action].name} 선택`,'select');render();
  setTimeout(()=>{
    if(room?.phase!=='quiz'||currentPlayer()?.uid!==uid)return;
    // 72% answer accuracy for demo opponents.
    const correct=Math.random()<.72;
    const idx=correct?room.question.correctIndex:(room.question.correctIndex+1+Math.floor(Math.random()*3))%4;
    demoAnswer(idx);
  },2600);
 },1100);
}
function centerOf(uid){
 const map=$('#battle-map'),node=document.getElementById('island-'+uid);if(!node)return null;
 const a=map.getBoundingClientRect(),b=node.getBoundingClientRect();return{x:(b.left+b.width*.5-a.left)/a.width*100,y:(b.top+b.height*.5-a.top)/a.height*100};
}
function impact(x,y,cls=''){const el=document.createElement('div');el.className='impact-flash '+cls;el.style.left=x+'%';el.style.top=y+'%';$('#fx-layer').append(el);setTimeout(()=>el.remove(),1300);}
function heal(x,y){const el=document.createElement('div');el.className='heal-effect';el.style.left=x+'%';el.style.top=y+'%';$('#fx-layer').append(el);setTimeout(()=>el.remove(),1500);}
function shoot(from,to){
 if(!from||!to)return;const map=$('#battle-map'),rect=map.getBoundingClientRect(),a={x:from.x*rect.width/100,y:from.y*rect.height/100},b={x:to.x*rect.width/100,y:to.y*rect.height/100};
 const dx=b.x-a.x,dy=b.y-a.y,length=Math.sqrt(dx*dx+dy*dy),deg=Math.atan2(dy,dx)*180/Math.PI;
 const streak=document.createElement('div');streak.className='missile-streak';streak.style.cssText=`left:${a.x}px;top:${a.y}px;width:${Math.max(25,length*.25)}px;transform:rotate(${deg}deg) scaleX(.1)`;$('#fx-layer').append(streak);
 streak.animate([{transform:`rotate(${deg}deg) translateX(0) scaleX(.1)`,opacity:0},{transform:`rotate(${deg}deg) translateX(${length*.45}px) scaleX(1)`,opacity:1},{transform:`rotate(${deg}deg) translateX(${length*.8}px) scaleX(.2)`,opacity:0}],{duration:850,easing:'ease-in-out'});
 setTimeout(()=>streak.remove(),900);
}
function animateEffects(effects){
 if(!room||!effects?.length)return;const from=centerOf(room.result?.actorUid)||{x:50,y:50};
 for(const fx of effects){const pos=centerOf(fx.uid);if(!pos)continue;
  if(fx.kind){shoot(from,pos);setTimeout(()=>impact(pos.x,pos.y,fx.damage===0?'shielded':''),700);}
  else if(fx.heal>0)heal(pos.x,pos.y);else if(fx.shield)impact(pos.x,pos.y,'shielded');
 }
}
let firebaseModule=null;
async function ensureOnline(){
 if(firebaseModule)return firebaseModule;
 if(!isConnected())throw new Error('Firebase 웹 앱 설정이 비어 있습니다. docs/firebase-config.js를 설정하세요.');
 const base='https://www.gstatic.com/firebasejs/12.4.0/';
 const [app,auth,store,func]=await Promise.all([import(base+'firebase-app.js'),import(base+'firebase-auth.js'),import(base+'firebase-firestore.js'),import(base+'firebase-functions.js')]);
 const fbapp=app.initializeApp(window.KB_FIREBASE_CONFIG),fbAuth=auth.getAuth(fbapp);
 const signed=await auth.signInAnonymously(fbAuth);myUid=signed.user.uid;
 firebaseModule={auth,store,func,db:store.getFirestore(fbapp),functions:func.getFunctions(fbapp,window.KB_FUNCTIONS_REGION||'asia-northeast3')};
 return firebaseModule;
}
async function callOnline(action,data){
 pending=true;renderActions();renderLobby();
 try{const fm=await ensureOnline();const res=await fm.func.httpsCallable(fm.functions,action)(data);return res.data;}
 catch(err){console.error(err);toast(err.message?.replace(/FirebaseError: /g,'')||'서버 연결에 실패했습니다.',true);return null;}
 finally{pending=false;renderActions();renderLobby();}
}
async function connectRoom(code){
 const fm=await ensureOnline();if(unsub)unsub();
 mode='online';lastResultAt=null;
 const ref=fm.store.doc(fm.db,'rooms',code);
 unsub=fm.store.onSnapshot(ref,snap=>{
  if(!snap.exists){toast('작전실이 삭제되었거나 없습니다.',true);return;}
  const prior=room;room=snap.data();
  const resultAt=room.result?.at;
  const isNewResult=!!(prior?.code===room.code&&resultAt&&resultAt!==lastResultAt&&room.phase==='reveal');
  render();$('#entry-backdrop').classList.add('hidden');
  if(isNewResult&&room.result?.correct)setTimeout(()=>{if(room?.result?.at===resultAt)animateEffects(room.result.effects);},2450);
  if(resultAt)lastResultAt=resultAt;
 },error=>{toast('실시간 연결 오류: '+error.message,true);});
 history.replaceState({},'',`?room=${encodeURIComponent(code)}`);
}
async function createOnline(){let name;try{name=safeName();}catch{return;}
 const cap=Number($('#capacity').value);
 const res=await callOnline('createRoom',{name,capacity:cap});if(res?.code)await connectRoom(res.code).catch(e=>toast(e.message,true));
}
async function joinOnline(){let name;try{name=safeName();}catch{return;}
 const code=$('#join-code').value.trim().toUpperCase();if(!/^[A-HJ-NP-Z2-9]{6}$/.test(code)){toast('6자리 방 코드를 입력하세요.',true);return;}
 const res=await callOnline('joinRoom',{name,code});if(res?.code)await connectRoom(res.code).catch(e=>toast(e.message,true));
}
async function startOnline(){if(room?.hostUid!==myUid)return;await callOnline('startGame',{code:room.code});}
function checkClock(){
 if(!room)return;
 if(['quiz','locked','reveal'].includes(room.phase)){
  const q=room.question;
  if(room.phase==='quiz'){
   const sec=Math.max(0,Math.ceil((q.deadline-Date.now())/1000));const el=$('#quiz-countdown');if(el){el.textContent=String(sec).padStart(2,'0')+'s';el.classList.toggle('urgent',sec<=8);}
   if(sec===0){const key=room.code+'|'+q.id;
    if(autoTimeoutKey!==key){autoTimeoutKey=key;if(mode==='demo')demoAnswer(-1);else callOnline('submitAnswer',{code:room.code,option:-1});}
   }
  }
  if(room.phase==='reveal'){
   if(Date.now()-(room.result?.at||0)>2400 && !$('#quiz-overlay').classList.contains('hidden'))renderQuiz();
   if(mode==='online'&&room.status==='active'&&Date.now()>=room.revealUntil){const key=room.code+'|'+room.result?.at;if(autoAdvanceKey!==key){autoAdvanceKey=key;callOnline('advanceTurn',{code:room.code});}}
   if(room.status==='finished'&&Date.now()-(room.result?.at||0)>4700){renderQuiz();}
  }
 }
}
$('#demo-button').addEventListener('click',beginDemo);
$('#create-button').addEventListener('click',createOnline);
$('#join-button').addEventListener('click',()=>$('#join-input').classList.toggle('hidden'));
$('#join-confirm').addEventListener('click',joinOnline);
$('#start-button').addEventListener('click',startOnline);
$('#nickname').value=localStorage.getItem('kb-name')||'COMMANDER';
if(!isConnected()){$('#online-actions').classList.add('hidden');$('#entry-online-status').textContent='AI 시뮬레이션 준비 완료';}
else $('#entry-online-status').textContent='온라인 멀티플레이 가능';
render();quizTimer=setInterval(checkClock,280);
const fromUrl=new URL(location.href).searchParams.get('room');
if(fromUrl&&isConnected()){
 ensureOnline().then(()=>connectRoom(fromUrl.toUpperCase())).catch(e=>toast('기존 작전실 연결 실패: '+e.message,true));
}
