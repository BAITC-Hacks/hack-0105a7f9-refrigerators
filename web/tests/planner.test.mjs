import fs from 'node:fs';
import assert from 'node:assert/strict';
import {createPlanner,parseBrief,assess,pricePlan} from '../dist/planner.mjs';
const cat=JSON.parse(fs.readFileSync(new URL('../dist/catalogue.json',import.meta.url))),plan=createPlanner(cat);
const base={city:'Алматы',date:'2026-10-15',category:'Ведущий',event_format:'корпоратив',budget_kzt:1200000,brief:'спокойный ведущий делового форума'};
assert.equal(plan(base).eligible,6);
const december=plan({...base,date:'2026-12-19'});assert.equal(december.eligible,1);assert.equal(december.alternatives[0].patch.date,'2026-12-17');assert.equal(december.alternatives[0].count,3);
const empty=plan({...base,budget_kzt:100000});assert.equal(empty.cards.length,0);assert.equal(empty.alternatives[0].patch.budget_kzt,650000);
const combined=plan({...base,city:'Астана',category:'Флорист',budget_kzt:100000});assert(combined.alternatives.some(a=>a.kind==='combined'));
const short=plan({...base,duration_hours:24});assert.equal(short.alternatives.find(a=>a.kind==='duration').patch.duration_hours,10);
let alternativesChecked=0,scenariosChecked=0;
for(const city of ['Алматы','Астана'])for(const category of ['Ведущий','Флорист','Фотограф','Декоратор'])for(const date of ['2026-10-15','2026-12-19'])for(const budget_kzt of [100000,1200000]){
 const r=plan({...base,city,category,date,budget_kzt});scenariosChecked++;
 assert.equal(r.cards.length+r.excluded.length,r.total);assert(r.cards.length<=3);assert.equal(new Set([...r.cards,...r.excluded].map(p=>p.id)).size,r.total);
 for(const a of r.alternatives){const applied=plan({...r.request,...a.patch});assert.equal(applied.eligible,a.count);assert(applied.eligible>r.eligible);alternativesChecked++;}
}
const named=plan({...base,brief:'Имя начинается на А'});assert.equal(named.cards[0].name,'Аня Форджер');assert(named.cards.every(p=>p.name.toLowerCase().startsWith('а')));
const wishes=plan({...base,brief:'Спокойный ведущий, без конкурсов, на русском'});assert.equal(wishes.request.language,'русский');assert(wishes.cards.every(p=>p.languages.includes('русский')));
assert.throws(()=>plan({...base,language:'английский',brief:'на русском'}));
assert.equal(assess(cat.find(p=>p.name==='Крилин'),parseBrief('без конкурсов'))[0].status,'unknown');
assert.equal(assess(cat.find(p=>p.name==='Нами'),parseBrief('без конкурсов'))[0].status,'conflict');
assert(!parseBrief('Не спокойный ведущий').some(i=>i.key==='calm'));
assert(!parseBrief('не на русском').some(i=>i.key==='language'));
assert.deepEqual(pricePlan({price_from_kzt:100000},20),{base:100000,reserve:20000,low:100000,high:120000,reservePercent:20});assert.throws(()=>pricePlan(cat[0],-1));
for(const patch of [{date:'2027-01-01'},{date:'2026-11-31'},{budget_kzt:0},{duration_hours:-1},{city:'unknown'},{category:'unknown'}])assert.throws(()=>plan({...base,...patch}));
console.log(`Passed: ${scenariosChecked} catalogue scenarios, ${alternativesChecked} reapplied alternatives, exclusion completeness, wish parsing, contradictory evidence, date validation and price calculations.`);
