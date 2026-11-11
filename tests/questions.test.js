import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const questions=JSON.parse(readFileSync(new URL('../data/questions.json',import.meta.url),'utf8'));
test('each category contains at least 1,000 fully formed 4-choice questions',()=>{
 const counts={};const ids=new Set();
 for(const q of questions){
  counts[q.category]=(counts[q.category]||0)+1;
  assert.equal(typeof q.prompt,'string');assert.ok(q.prompt.length>7);
  assert.ok(!ids.has(q.id),`Duplicate id ${q.id}`);ids.add(q.id);
  assert.equal(q.options.length,4);assert.equal(new Set(q.options).size,4);
  assert.ok(Number.isInteger(q.correctIndex)&&q.correctIndex>=0&&q.correctIndex<=3);
 }
 for(const category of ['science','english','history','general'])assert.ok(counts[category]>=1000,`${category}: ${counts[category]}`);
});
