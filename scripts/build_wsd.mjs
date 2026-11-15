// Агрегаты «Структуры системного давления · 7 дней» для бесплатной карты
// (этап 2.1 безопасности, 15.11.2026): docs/wsd_aggregates.json.
//
// Карта сейчас грузит весь docs/signals.json (35 МБ, вместе с pro-процессами)
// только ради этих чисел. Здесь они считаются заранее по ВСЕМ процессам,
// как и сейчас (решение 1 от 15.11), и в публичный файл попадают только
// агрегаты по 5 доменам, без отдельных процессов.
//
// Функция ниже - ДОСЛОВНАЯ копия window._wsdCompute из a-atlas/index.html
// (и risk-map.html). Отпечаток sha256 копии записывается в файл: при
// изменении функции на сайте её нужно скопировать сюда без правок.
// Единственное отличие расчёта: Date.now() берётся в момент прогона, а не
// в момент открытия страницы. Процессы, которым 7 дней исполняется в эти
// минуты, переходят из «новых» в «базу» чуть раньше или позже.
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { createHash } from 'node:crypto';

const window = {};
const FN_SRC = String.raw`
window._wsdCompute = function(signals){
  var DOMS=['climate','geopolitics','economy','technology','social'];
  var DAY=86400000, now=Date.now(), out={};
  DOMS.forEach(function(d){ out[d]={A:null,B:null,stA:'none',stB:'none',base:0,neu:0,nh:0}; });
  (signals||[]).forEach(function(s){
    if(s.status!=='active') return;                    // SPEC §4.0: dormant/fading вне расчёта
    var d=s.primary_domain||((s.domains||[])[0]); if(!out[d]) return;
    var fs=Date.parse(s.first_seen||'')||0; if(!fs) return;
    var o=out[d], old=(now-fs)>=7*DAY;
    if(old){
      o.base++;
      var h=s.severity_history||[];                    // SPEC §4.1: ось B — только по базе
      if(h.length>=2){
        var p=h.slice().sort(function(a,b){ return String(a.t)<String(b.t)?-1:1; });
        var a=parseFloat(p[0].v), b=parseFloat(p[p.length-1].v);
        if(!isNaN(a)&&!isNaN(b)){ o.nh++; o._sum=(o._sum||0)+(b-a); }
      }
    } else o.neu++;                                    // новый процесс = новый очаг (в ось B не входит)
  });
  DOMS.forEach(function(d){
    var o=out[d];
    // ── ось A: приток
    if(o.base>0){ o.A=Math.round(100*o.neu/o.base); o.stA=(o.base<3)?'low':'ok'; }
    else { o.A=null; o.stA='none'; }                   // SPEC §6.1: НЕ +100%
    // ── ось B: три состояния (SPEC §6.2)
    if(o.nh>=3){ o.B=Math.round(10*o._sum/o.nh)/10; o.stB='value'; }
    else if(o.nh>0){ o.B=null; o.stB='forming'; }
    else { o.B=null; o.stB='none'; }
  });
  return out;
};
`;
const compute = new Function('window', FN_SRC + '\nreturn window._wsdCompute;')(window);

let full;
try { full = JSON.parse(readFileSync('docs/signals.json', 'utf8')); }
catch (e) { console.error('  [wsd] docs/signals.json не прочитан: ' + e.message + ', пропуск'); process.exit(0); }

const dom = compute(full && full.signals);
const out = {
  generated_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
  source_updated: full.updated || null,
  basis: 'all_processes',
  fn_sha256: createHash('sha256').update(FN_SRC.trim() + '\n').digest('hex'),
  dom,
};
writeFileSync('docs/wsd_aggregates.json.tmp', JSON.stringify(out));
renameSync('docs/wsd_aggregates.json.tmp', 'docs/wsd_aggregates.json');
console.log('  [wsd] агрегаты по ' + Object.keys(dom).length + ' доменам, ' + (full.signals || []).length + ' процессов');
