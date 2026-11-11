// Pure deterministic turn / damage rules shared by Cloud Functions and unit tests.
export const ACTIONS = Object.freeze({
  nuclear: { category: 'science', title: '트라이던트 II 핵공격' },
  icbm: { category: 'english', title: '미니트맨 III ICBM' },
  defense: { category: 'history', title: '아이언돔 방어체계' },
  repair: { category: 'general', title: '전투공병 긴급복구' }
});
export const clampHP = hp => Math.max(0, Math.min(100, Math.round(hp * 2) / 2));
export const living = players => players.filter(p => p.hp > 0);
export function nextTurnIndex(players, after) {
  for (let d = 1; d <= players.length; d++) {
    const i = (after + d) % players.length;
    if (players[i].hp > 0) return i;
  }
  return -1;
}
function strike(target, kind, raw) {
  if (target.hp <= 0) return 0;
  const shield = !!target.shield;
  const applied = shield ? (kind === 'nuclear' ? raw * 0.5 : 0) : raw;
  const dealt = Math.min(target.hp, applied);
  target.hp = clampHP(target.hp - dealt);
  if (shield) target.shield = false; // one hit only (including blocked hit)
  if (dealt > 0) target.lastHit = { kind, damage: dealt };
  return dealt;
}
export function applyAction(players, actorIndex, action) {
  if (!ACTIONS[action]) throw new Error('Unknown action');
  const next = players.map(p => ({...p, lastHit:p.lastHit ? {...p.lastHit} : null}));
  const actor = next[actorIndex];
  if (!actor || actor.hp <= 0) throw new Error('Actor is eliminated');
  const effects = [];
  let summary='';
  switch (action) {
    case 'nuclear': {
      for (let i=0;i<next.length;i++) {
        const dealt=strike(next[i], 'nuclear', 5);
        if (dealt || players[i].shield) effects.push({uid:next[i].uid, damage:dealt, kind:'nuclear'});
      }
      summary=`${actor.name}의 트라이던트 II 핵공격! 모든 생존 기지에 충격파가 확산됩니다.`;
      break;
    }
    case 'icbm': {
      const candidates=next.map((p,i)=>({p,i})).filter(({p,i})=>i!==actorIndex && p.hp>0);
      // Highest HP; seat order breaks ties.
      candidates.sort((a,b)=> b.p.hp-a.p.hp || a.i-b.i);
      if (candidates.length) {
        const {p,i}=candidates[0];
        const dealt=strike(p,'icbm',10);
        effects.push({uid:next[i].uid,damage:dealt,kind:'icbm'});
        summary=`${actor.name}의 ICBM이 ${p.name} 기지를 타격했습니다.`;
      } else summary='공격 가능한 상대가 없습니다.';
      break;
    }
    case 'defense': {
      actor.shield=true;
      summary=`${actor.name} 기지에 방어체계를 구축했습니다. 다음 피격 1회에 적용됩니다.`;
      effects.push({uid:actor.uid,shield:true});
      break;
    }
    case 'repair': {
      const prev=actor.lastHit;
      const restored=prev ? Math.min(100-actor.hp, prev.damage*(prev.kind==='nuclear'?0.5:1)) : 0;
      if (prev) actor.lastHit=null; // consume, even at full HP
      actor.hp=clampHP(actor.hp+restored);
      effects.push({uid:actor.uid,heal:restored});
      summary=restored ? `${actor.name} 기지 복구 완료! HP +${restored}` : `${actor.name}의 복구 작전: 회복 가능한 피해 기록이 없습니다.`;
      break;
    }
  }
  const alive=living(next);
  const ended=alive.length<=1;
  const winner=alive.length===1 ? alive[0].uid : null; // null = draw if ended
  return {players:next,effects,summary,ended,winner};
}
