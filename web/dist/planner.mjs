import {createMatcher,validateRequest,localQuotes} from './matcher.mjs';

export const reasonText={busy:'Занят на выбранную дату',over_budget:'Стартовая цена выше бюджета',wrong_format:'Не указан этот формат',wrong_language:'Не указан нужный язык',too_short:'Длительность выше лимита',name_prefix:'Имя не начинается с нужной буквы',negative:'Описание противоречит пожеланию'};
const norm=s=>s.toLowerCase().replace(/ё/g,'е');
export function parseBrief(text=''){
 const t=norm(text),items=[];
 const styles=[['calm','Спокойный стиль',/спокой|ненавязчив|сдержан|интеллигент/],['creative','Креативный подход',/креатив|необыч|оригинальн/],['energetic','Энергичный стиль',/энергич|динамич|драйв/],['traditional','Традиционный формат',/традицион/],['business','Деловое событие',/делов|бизнес|форум/]];
 for(const [key,label,re] of styles){const match=t.match(re);if(match&&!/(?:не\s+(?:хочу\s+)?|без\s+)(?:очень\s+)?$/.test(t.slice(0,match.index)))items.push({key,label,kind:'preference'});}
 for(const [language,re] of [['русский',/на русском|русскоязыч/],['казахский',/на казахском|казахскоязыч/],['английский',/на английском|англоязыч/]]){const match=t.match(re);if(match&&!/не\s+$/.test(t.slice(0,match.index)))items.push({key:'language',label:'Язык: '+language,value:language,kind:'condition'});}
 if(/без\s+(?:\S+\s+){0,2}конкурс|не\s+нужны\s+конкурс/.test(t))items.push({key:'no_contests',label:'Без конкурсов',kind:'preference'});
 const prefix=t.match(/имя\s+начинается\s+(?:с\s+буквы|на\s+букву|на|с)\s*[«"']?([а-яa-z]+)/u);
 if(prefix)items.push({key:'name_prefix',label:'Имя на «'+prefix[1].toUpperCase()+'»',value:prefix[1],kind:'condition'});
 return items;
}
const evidencePatterns={calm:/спокой|интеллигент|ненавязчив|мягк|не напряжн/i,creative:/креатив|оригинальн|необыч|авторск/i,energetic:/энерги|динамич|драйв/i,traditional:/традиц|обряд/i,business:/делов|бизнес|форум|конференц/i};
export function assess(p,items){return items.map(item=>{let status='unknown',quote='';if(item.key==='language'){status=p.languages.includes(item.value)?'confirmed':'conflict';quote=p.languages.join(', ');}else if(item.key==='name_prefix'){status=norm(p.name).startsWith(item.value)?'confirmed':'conflict';quote=p.name;}else if(item.key==='no_contests'){
 const sentences=p.description.split(/(?<=[.!?])\s+|\n+|•/).filter(s=>/конкурс/i.test(s));
 const exact=sentences.find(s=>/без\s+конкурсов|не\s+(?:провожу|проводим)\s+конкурс/i.test(s));
 const contradiction=sentences.find(s=>/адресными конкурсами|прово(?:жу|дим|дит)\s+конкурс/i.test(s));
 if(exact){status='confirmed';quote=exact;}else if(contradiction){status='conflict';quote=contradiction;}
 }else{const re=evidencePatterns[item.key];const sentences=p.description.split(/(?<=[.!?])\s+|\n+|•/);const found=sentences.find(s=>re?.test(s));if(found){status='related';quote=found;}}
 return {...item,status,quote};});}
export function failures(p,r,items){const reasons=[];if(p.busy_dates.includes(r.date))reasons.push('busy');if(p.price_from_kzt>r.budget_kzt)reasons.push('over_budget');if(!p.event_formats.includes(r.event_format))reasons.push('wrong_format');if(r.language&&!p.languages.includes(r.language))reasons.push('wrong_language');if(r.duration_hours!==null&&p.max_hours!==null&&p.max_hours<r.duration_hours)reasons.push('too_short');if(items.some(i=>i.key==='name_prefix'&&!norm(p.name).startsWith(i.value)))reasons.push('name_prefix');if(assess(p,items).some(i=>i.key==='no_contests'&&i.status==='conflict'))reasons.push('negative');return reasons;}
export function createPlanner(catalogue){const base=createMatcher(catalogue);
 function prepare(input){const parsed=parseBrief(input.brief||''),langs=parsed.filter(i=>i.key==='language').map(i=>i.value);if(langs.length>1)throw Error('В пожеланиях несколько языков. Выберите один в поле «Язык» и уточните текст.');if(langs[0]&&input.language&&norm(input.language.trim())!==langs[0])throw Error('Язык в пожеланиях отличается от поля «Язык». Согласуйте их перед подбором.');const r=validateRequest({...input,language:langs[0]||input.language},catalogue);return {r,parsed};}
 function compute(input){const {r,parsed}=prepare(input);const source=base(r);const pool=catalogue.filter(p=>p.city===r.city&&p.categories.includes(r.category));const scoreOrder=source.allEligible||source.cards;const assessments=new Map(pool.map(p=>[p.id,assess(p,parsed)]));
 const eligible=scoreOrder.filter(p=>!failures(p,r,parsed).length).map((p,index)=>({p,index,score:assessments.get(p.id).filter(a=>a.status==='related'||a.status==='confirmed').length})).sort((a,b)=>b.score-a.score||a.index-b.index).map(x=>x.p);
 const selected=eligible.slice(0,3),quotes=localQuotes(selected,r);const cards=selected.map(p=>({...p,evidence_quote:quotes.get(p.id)}));const chosen=new Set(cards.map(p=>p.id));
 const excluded=pool.filter(p=>!chosen.has(p.id)).map(p=>({...p,failures:failures(p,r,parsed),assessment:assessments.get(p.id)})).sort((a,b)=>a.failures.length-b.failures.length||a.price_from_kzt-b.price_from_kzt);
 return {request:r,parsed,cards,eligible:eligible.length,total:pool.length,excluded,assessments,status:!pool.length?'category_absent':cards.length?'matched':'no_eligible',alternatives:[]};}
 function alternatives(result){const r=result.request,items=result.parsed,baseline=result.eligible,candidates=[],pool=catalogue.filter(p=>p.city===r.city&&p.categories.includes(r.category));const count=request=>catalogue.filter(p=>p.city===request.city&&p.categories.includes(request.category)&&!failures(p,request,items).length).length;
 function add(patch,kind,distance){const request={...r,...patch},n=count(request);if(n>baseline)candidates.push({patch,kind,count:n,distance});}
 if(!pool.length){for(const city of [...new Set(catalogue.map(p=>p.city))])if(city!==r.city)add({city},'city',1);return candidates.sort((a,b)=>b.count-a.count).slice(0,3);}
 const day=86400000,date=new Date(r.date+'T00:00:00Z').getTime();
 for(let offset=1;offset<=99;offset++){const before=candidates.length;for(const sign of [1,-1]){const d=new Date(date+sign*offset*day).toISOString().slice(0,10);if(d>='2026-09-23'&&d<='2026-12-31')add({date:d},'date',offset);}if(candidates.length>before)break;}
 for(const price of [...new Set(pool.map(p=>p.price_from_kzt))].filter(v=>v>r.budget_kzt).sort((a,b)=>a-b)){const n=candidates.length;add({budget_kzt:price},'budget',price-r.budget_kzt);if(candidates.length>n)break;}
 if(r.duration_hours!==null)for(const hours of [...new Set(pool.map(p=>p.max_hours))].filter(v=>v!==null&&v<r.duration_hours).sort((a,b)=>b-a)){const n=candidates.length;add({duration_hours:hours},'duration',r.duration_hours-hours);if(candidates.length>n)break;}
 if(!candidates.length&&baseline===0){for(const p of pool){const reasons=failures(p,r,items);if(reasons.some(k=>!['busy','over_budget','too_short'].includes(k)))continue;const patch={};if(reasons.includes('over_budget'))patch.budget_kzt=p.price_from_kzt;if(reasons.includes('too_short'))patch.duration_hours=p.max_hours;if(reasons.includes('busy')){for(let offset=1;offset<=99&&!patch.date;offset++)for(const sign of [1,-1]){const d=new Date(date+sign*offset*day).toISOString().slice(0,10);if(d>='2026-09-23'&&d<='2026-12-31'&&!p.busy_dates.includes(d)){patch.date=d;break;}}if(!patch.date)continue;}if(Object.keys(patch).length>1)add(patch,'combined',Object.keys(patch).length);}}
 const seen=new Set();return candidates.filter(c=>{const key=JSON.stringify(c.patch);if(seen.has(key))return false;seen.add(key);return true;}).sort((a,b)=>Object.keys(a.patch).length-Object.keys(b.patch).length||b.count-a.count||a.distance-b.distance).slice(0,3);
 }
 return input=>{const result=compute(input);result.alternatives=alternatives(result);return result;};
}

export function pricePlan(p,reservePercent=20){if(!Number.isFinite(reservePercent)||reservePercent<0||reservePercent>50)throw Error('Резерв должен быть от 0 до 50%.');const reserve=Math.round(p.price_from_kzt*reservePercent/100);return {base:p.price_from_kzt,reserve,low:p.price_from_kzt,high:p.price_from_kzt+reserve,reservePercent};}
