import fs from 'node:fs';
import assert from 'node:assert/strict';
import {createPlanner,parseBrief,assess} from '../dist/planner.mjs';
const cat=JSON.parse(fs.readFileSync(new URL('../dist/catalogue.json',import.meta.url))),plan=createPlanner(cat);
const base={city:'Алматы',date:'2026-10-15',category:'Ведущий',event_format:'корпоратив',budget_kzt:1200000,brief:'спокойный ведущий делового форума'};
assert.equal(plan(base).eligible,6);assert.equal(plan(base).alternatives.length,0);
const december=plan({...base,date:'2026-12-19'});assert.equal(december.eligible,1);assert.equal(december.alternatives[0].patch.date,'2026-12-17');assert.equal(december.alternatives[0].count,3);
const nearPrice=plan({...base,budget_kzt:600000});assert.equal(nearPrice.alternatives.find(a=>a.kind==='budget').patch.budget_kzt,650000);
assert.equal(plan({...base,budget_kzt:100000}).alternatives.length,0);
const combined=plan({...base,city:'Астана',category:'Флорист',budget_kzt:250000});assert(combined.alternatives.some(a=>a.kind==='combined'));
const formats=plan({...base,event_format:'день рождения'});assert(formats.alternatives.some(a=>a.patch.event_format==='юбилей'));
let proposals=0,scenarios=0;
for(const city of ['Алматы','Астана'])for(const category of ['Ведущий','Флорист','Фотограф','Декоратор'])for(const date of ['2026-10-15','2026-12-19'])for(const budget_kzt of [100000,600000,1200000])for(const event_format of ['корпоратив','день рождения','той']){
 const r=plan({...base,city,category,date,budget_kzt,event_format});scenarios++;
 assert.equal(r.cards.length+r.excluded.length,r.total);assert(r.cards.length<=3);
 assert.equal(new Set([...r.cards,...r.excluded].map(p=>p.id)).size,r.total);
 for(const a of r.alternatives){const applied=plan({...r.request,...a.patch});assert.equal(applied.eligible,a.count);assert(applied.eligible>r.eligible);if(a.patch.date)assert(Math.abs((new Date(a.patch.date)-new Date(r.request.date))/86400000)<=7);if(a.patch.budget_kzt)assert(a.patch.budget_kzt<=r.request.budget_kzt*1.25);proposals++;}
}
assert.equal(plan({...base,brief:'Имя начинается на А'}).cards[0].name,'Аня Форджер');
const brief=plan({...base,brief:'Спокойный ведущий, без конкурсов, на русском'});assert.equal(brief.request.language,'русский');assert(brief.cards.every(p=>p.languages.includes('русский')));
const fieldWins=plan({...base,language:'английский',brief:'на русском'});assert.equal(fieldWins.request.language,'английский');assert(fieldWins.notes.length>0);
const multilingual=plan({...base,brief:'на русском и английском'});assert.equal(multilingual.request.language,'');assert(multilingual.notes.length>0);
assert.equal(assess(cat.find(p=>p.name==='Нами'),parseBrief('без конкурсов'))[0].status,'unknown');
assert(!parseBrief('Не спокойный ведущий').some(i=>i.key==='calm'));
assert(!parseBrief('не на русском').some(i=>i.key==='language'));
for(const patch of [{date:'2027-01-01'},{date:'2026-11-31'},{budget_kzt:0},{duration_hours:-1},{city:'unknown'},{category:'unknown'}])assert.throws(()=>plan({...base,...patch}));
console.log(`PASS: ${scenarios} scenarios, ${proposals} verified nearby alternatives, format suggestions, input handling and simplified UI structure.`);
