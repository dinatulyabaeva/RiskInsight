'use strict';
const $ = id => document.getElementById(id);
const state = {context:null, dashboard:null, busy:false, token:'', loadId:0};
const ru = new Intl.NumberFormat('ru-RU');
const pct = v => v == null ? '—' : (v*100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+'%';
const money = v => {
  if (v == null) return '—';
  const unit = Math.abs(v)>=1e9 ? [1e9,'млрд ₽'] : Math.abs(v)>=1e6 ? [1e6,'млн ₽'] : [1,'₽'];
  return (v/unit[0]).toLocaleString('ru-RU',{maximumFractionDigits:2})+' '+unit[1];
};
const dateLabel = s => new Date(s+'T12:00:00Z').toLocaleDateString('ru-RU',{month:'short',year:'2-digit',timeZone:'UTC'}).replace(' г.','');
const colors = ['#6be3bd','#82a5c7','#a2a3dd','#e1ba7b','#d58489'];
function element(tag, className, text) { const e=document.createElement(tag); if(className)e.className=className; if(text!==undefined)e.textContent=text; return e; }
function showError(text) { $('global-error').textContent=text; $('global-error').hidden=!text; }
async function api(path, options={}) {
  const headers={'Content-Type':'application/json',...options.headers};
  if(state.token) headers.Authorization='Bearer '+state.token;
  const response=await fetch(path,{...options,headers});
  if(!response.ok){
    if(response.status===401 && !$('access-dialog').open) $('access-dialog').showModal();
    let data;try{data=await response.json();}catch{data={};}
    throw new Error(typeof data.detail==='string'?data.detail:`Ошибка HTTP ${response.status}`);
  }
  return response.headers.get('content-type')?.includes('application/json')?response.json():response.text();
}
function scope(){return {as_of:$('date-filter').value||null,filters:{product:$('product-filter').value||null,region:$('region-filter').value||null,risk_band:$('rating-filter').value||null}};}
function setBusy(busy){state.busy=busy;document.querySelectorAll('[data-scenario]').forEach(b=>b.disabled=busy);$('send-button').disabled=busy||state.context?.mode!=='llm'||!state.context?.llm_configured;}
function fillSelect(id,values,placeholder){const select=$(id);select.replaceChildren();if(placeholder)select.add(new Option(placeholder,''));values.forEach(v=>select.add(new Option(v,v)));}
async function initialize(){
  try{
    showError('');
    const health=await api('/api/health');
    if(health.auth_required&&!state.token){$('access-dialog').showModal();return;}
    const ctx=await api('/api/context');state.context=ctx;
    fillSelect('date-filter',[...ctx.dates].reverse());fillSelect('product-filter',ctx.filters.product,'Все продукты');fillSelect('region-filter',ctx.filters.region,'Все регионы');fillSelect('rating-filter',ctx.filters.risk_band,'Все рейтинги');
    const live=ctx.mode==='llm';$('mode-badge').textContent=live?'LLM':'DEMO';$('mode-badge').classList.toggle('live',live);
    $('mode-note').textContent=live?`Модель: ${ctx.model||'не задана'}. Ответы основаны на MCP-инструментах; выводы требуют проверки.`:'Без LLM: готовые сценарии и шаблонные отчеты. MCP и расчеты настоящие. Для свободных вопросов настройте APP_MODE=llm.';
    $('question').disabled=!live||!ctx.llm_configured;$('composer-note').textContent=live?'Новый вопрос — независимый анализ текущей области':'DEMO: используйте готовые сценарии';
    $('connection-dot').classList.add('online');$('connection-label').textContent='MCP подключен · stdio';setBusy(false);await loadDashboard();
  }catch(error){showError(error.message);$('connection-dot').classList.remove('online');$('connection-label').textContent='Нет подключения';}
}
async function loadDashboard(){
  const loadId=++state.loadId;const s=scope();const params=new URLSearchParams();
  Object.entries({as_of:s.as_of,...s.filters}).forEach(([k,v])=>{if(v)params.set(k,v);});
  try{
    showError('');$('export-dashboard').disabled=true;
    const d=await api('/api/dashboard?'+params.toString());if(loadId!==state.loadId)return;
    state.dashboard=d;renderDashboard(d);$('export-dashboard').disabled=false;
  }catch(error){if(loadId===state.loadId)showError(error.message);}
}
function renderDashboard(d){
  const m=d.summary.metrics;$('kpi-balance').textContent=money(m.balance_rub);$('kpi-npl').textContent=pct(m.npl90_ratio);$('kpi-count').textContent=ru.format(m.loan_count);$('kpi-el').textContent=money(m.el_rub);$('kpi-rate').textContent='Средневзвешенная ставка '+pct(m.weighted_rate);
  const delta=d.trend.npl90_delta_pp;
  $('kpi-delta').textContent=delta==null?'Недостаточно данных':`${delta>=0?'+':''}${delta.toLocaleString('ru-RU',{maximumFractionDigits:2})} п.п. за ${d.trend.points.length} срезов`;
  $('kpi-delta').classList.toggle('up',delta>0);$('period-label').textContent=d.trend.points.length+' срезов';
  $('trend-caption').textContent=dateLabel(d.trend.start_date)+' — '+dateLabel(d.trend.end_date);drawTrend(d.trend.points);
  drawBuckets(d.delinquency.buckets);drawSegments(d.segments.segments);
}
const ns='http://www.w3.org/2000/svg';
function svgEl(tag,attrs,text){const el=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));if(text!==undefined)el.textContent=text;return el;}
function drawTrend(points){
  const container=$('trend-chart');container.replaceChildren();
  const available=points.filter(p=>p.npl90_ratio!=null);if(!available.length){container.append(element('div','loading-placeholder','Нет данных для выбранной области'));return;}
  const width=530,height=190,pad={left:48,right:18,top:20,bottom:32};
  const svg=svgEl('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'DPD 90+ по месячным срезам'});
  const max=Math.max(...available.map(p=>p.npl90_ratio*100));const top=Math.max(1,Math.ceil(max*1.2*2)/2);
  const x=i=>pad.left+i*(width-pad.left-pad.right)/Math.max(1,points.length-1);
  const y=v=>height-pad.bottom-v/top*(height-pad.top-pad.bottom);
  for(let i=0;i<=3;i++){const v=top*i/3;svg.append(svgEl('line',{x1:pad.left,y1:y(v),x2:width-pad.right,y2:y(v),stroke:'#2b3843','stroke-width':1,'stroke-dasharray':'3 5'}));svg.append(svgEl('text',{x:pad.left-9,y:y(v)+3,'text-anchor':'end',fill:'#8096a6','font-size':9},v.toFixed(1).replace('.',',')+'%'));}
  const defs=svgEl('defs',{}),gradient=svgEl('linearGradient',{id:'trendFill',x1:0,y1:0,x2:0,y2:1});gradient.append(svgEl('stop',{offset:'0%','stop-color':'#6be3bd','stop-opacity':'.18'}),svgEl('stop',{offset:'100%','stop-color':'#6be3bd','stop-opacity':'0'}));defs.append(gradient);svg.append(defs);
  // Split on missing values: a null observation must not be drawn as zero or interpolated.
  let segment=[];const chunks=[];points.forEach((p,i)=>{if(p.npl90_ratio==null){if(segment.length)chunks.push(segment);segment=[];}else segment.push([x(i),y(p.npl90_ratio*100)]);});if(segment.length)chunks.push(segment);
  chunks.forEach(chunk=>{const line=chunk.map((p,i)=>(i?'L':'M')+p.join(',')).join(' ');svg.append(svgEl('path',{d:line+` L${chunk.at(-1)[0]},${y(0)} L${chunk[0][0]},${y(0)} Z`,fill:'url(#trendFill)'}));svg.append(svgEl('path',{d:line,fill:'none',stroke:'#6be3bd','stroke-width':2.5,'stroke-linejoin':'round'}));});
  points.forEach((p,i)=>{svg.append(svgEl('text',{x:x(i),y:height-10,'text-anchor':'middle',fill:'#8096a6','font-size':9},dateLabel(p.as_of)));if(p.npl90_ratio!=null){const c=svgEl('circle',{cx:x(i),cy:y(p.npl90_ratio*100),r:i===points.length-1?4:3,fill:'#6be3bd',stroke:'#14232b','stroke-width':2});c.append(svgEl('title',{},`${p.as_of}: ${pct(p.npl90_ratio)}`));svg.append(c);}});container.append(svg);
}
function drawBuckets(rows){
  let position=0;const stops=[];$('bucket-legend').replaceChildren();
  rows.forEach((r,i)=>{const share=r.balance_share||0;stops.push(`${colors[i]} ${position}% ${position+share*100}%`);position+=share*100;
    const item=element('div','bucket-row');const dot=element('i');dot.style.background=colors[i];const bar=element('span','bar');const fill=element('span');fill.style.width=share*100+'%';fill.style.background=colors[i];bar.append(fill);item.append(dot,element('span','b-label','DPD '+r.bucket),bar,element('strong','',pct(r.balance_share)));$('bucket-legend').append(item);
  });$('donut').style.background=position?'conic-gradient('+stops.join(',')+')':'#243945';$('performing').textContent=pct(rows[0]?.balance_share);
}
function drawSegments(rows){$('segments-body').replaceChildren();rows.forEach(r=>{const tr=element('tr');tr.append(element('td','',r.segment),element('td','',money(r.balance_rub)));const td=element('td');td.append(element('span','ratio-chip',pct(r.npl90_ratio)));tr.append(td,element('td','',pct(r.portfolio_share)));$('segments-body').append(tr);});if(!rows.length){const tr=element('tr');const td=element('td','','Нет данных');td.colSpan=4;tr.append(td);$('segments-body').append(tr);}}
function download(name,content,type){const blob=new Blob([content],{type});const url=URL.createObjectURL(blob);const a=element('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function appendReferencedText(container,text,traceElements){const parts=text.split(/(\[T\d+\])/g);parts.forEach(p=>{const match=p.match(/^\[(T\d+)\]$/);if(match&&traceElements[match[1]]){const b=element('button','source-ref',p);b.type='button';b.addEventListener('click',()=>{const {outer,inner}=traceElements[match[1]];outer.open=true;inner.open=true;inner.scrollIntoView({behavior:'smooth',block:'nearest'});});container.append(b);}else container.append(document.createTextNode(p));});}
function renderResult(result,container){
  const head=element('div','answer-head');head.append(element('span','',result.mode==='llm'?'✧ RiskInsight · LLM':'▦ RiskInsight · шаблонный DEMO'),element('small','',`${(result.duration_ms/1000).toFixed(1)} c · ${result.trace.length} вызова MCP`));container.append(head);
  const outer=element('details','trace');outer.append(element('summary','',`Источники и вызовы MCP · ${result.trace.length}`));const body=element('div','trace-body');const traceElements={};
  result.trace.forEach(t=>{const detail=element('details','trace-call');detail.append(element('summary','',`[${t.ref}] ${t.ok?'✓':'!'} ${t.tool} · ${t.duration_ms} мс`));detail.append(element('pre','',JSON.stringify({arguments:t.arguments,result:t.result,error:t.error},null,2)));body.append(detail);traceElements[t.ref]={outer,inner:detail};});
  const protocol=element('details','trace-call');protocol.append(element('summary','','MCP handshake / JSON-RPC'));protocol.append(element('pre','',JSON.stringify(result.protocol,null,2)));body.append(protocol);outer.append(body);
  const answer=element('div','answer');appendReferencedText(answer,result.answer,traceElements);container.append(answer);
  result.warnings.forEach(w=>container.append(element('div','warning',w)));container.append(outer);
  const actions=element('div','result-actions');const md=element('button','','↓ Отчет .md');md.addEventListener('click',()=>download('riskinsight-report.md',`# RiskInsight\n\nРежим: ${result.mode}\nRequest: ${result.request_id}\n\n${result.answer}\n\n## Источники MCP\n\n\`\`\`json\n${JSON.stringify(result.trace,null,2)}\n\`\`\`\n`,'text/markdown;charset=utf-8'));const js=element('button','','↓ Журнал .json');js.addEventListener('click',()=>download('riskinsight-evidence.json',JSON.stringify(result,null,2),'application/json'));actions.append(md,js);container.append(actions);
}
async function analyze(scenario=null){
  if(state.busy||!state.context)return;
  const question=scenario?state.context.scenarios[scenario]:$('question').value.trim();if(question.length<3)return;
  const conv=$('conversation');conv.querySelector('.welcome')?.remove();conv.append(element('div','user-message',question));
  const scopeLabel=element('div','section-label','Срез '+($('date-filter').value||'последний')+' · '+($('product-filter').value||'все продукты')+' · '+($('region-filter').value||'все регионы'));conv.append(scopeLabel);
  const loading=element('div','analysis-loading',state.context.mode==='llm'?'LLM анализирует вопрос и вызывает MCP…':'Выполняются MCP-инструменты сценария…');conv.append(loading);setBusy(true);conv.scrollTop=conv.scrollHeight;
  try{const result=await api('/api/chat',{method:'POST',body:JSON.stringify({question,scenario,...scope()})});loading.remove();const block=element('div','result-block');renderResult(result,block);conv.append(block);block.scrollIntoView({behavior:'smooth',block:'nearest'});$('question').value='';}
  catch(error){loading.remove();conv.append(element('div','analysis-error',error.message));}
  finally{setBusy(false);}
}
['date-filter','product-filter','region-filter','rating-filter'].forEach(id=>$(id).addEventListener('change',loadDashboard));
$('reset-filters').addEventListener('click',()=>{$('date-filter').value=state.context.latest_date;['product-filter','region-filter','rating-filter'].forEach(id=>$(id).value='');loadDashboard();});
document.querySelectorAll('[data-scenario]').forEach(b=>b.addEventListener('click',()=>analyze(b.dataset.scenario)));
$('chat-form').addEventListener('submit',e=>{e.preventDefault();analyze();});
$('question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(!$('send-button').disabled)analyze();}});
$('export-dashboard').addEventListener('click',()=>{if(state.dashboard)download('riskinsight-dashboard.json',JSON.stringify(state.dashboard,null,2),'application/json');});
$('method-button').addEventListener('click',async()=>{$('method-dialog').showModal();try{$('method-text').textContent=await api('/api/methodology');}catch(e){$('method-text').textContent=e.message;}});
$('access-button').addEventListener('click',()=>$('access-dialog').showModal());
$('access-form').addEventListener('submit',e=>{e.preventDefault();state.token=$('access-token').value.trim();$('access-token').value='';$('access-dialog').close();initialize();});
document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
initialize();
