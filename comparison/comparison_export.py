import json
import math
from datetime import date, datetime
from html import escape

from plotly.offline import get_plotlyjs


def build_comparison_html(records, years, periods, filters, management_insights):
    def json_safe(value):
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if hasattr(value, "item"):
            return json_safe(value.item())
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return value

    data_json = json.dumps(json_safe(records), ensure_ascii=True, separators=(",", ":"), default=str)
    data_json = data_json.replace("</", "<\\/")
    years_json = json.dumps(list(years), separators=(",", ":"))
    periods_json = json.dumps(list(periods), ensure_ascii=True, separators=(",", ":"))
    filters_json = json.dumps(filters, ensure_ascii=True, separators=(",", ":"), default=str)
    plotly_js = get_plotlyjs().replace("</script", "<\\/script")
    insights_html = "".join(
        f'<article class="management-insight {escape(tone, quote=True)}">'
        f'<strong>{escape(title)}</strong><span>{escape(message)}</span></article>'
        for tone, title, message in management_insights
    )

    template = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hospital Performance Comparison Dashboard</title>
<style>
:root{color-scheme:light;--ink:#16324f;--muted:#63768a;--line:#dfe7ef;--teal:#087e8b;--green:#4c9a72;--paper:#f4f7fb;--white:#fff}
*{box-sizing:border-box}html{overflow-x:clip}body{margin:0;overflow-x:clip;background:var(--paper);color:var(--ink);font-family:"Segoe UI",Tahoma,sans-serif;font-size:14px;line-height:1.45}
main{width:min(1500px,100%);margin:0 auto;padding:22px clamp(12px,2vw,28px) 34px}
.hero{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:22px 24px;border:1px solid rgba(8,126,139,.2);border-radius:12px;background:linear-gradient(110deg,#0b3954,#087e8b);color:#fff}
.hero h1{margin:0;font-size:clamp(20px,2vw,28px);line-height:1.2}.hero p{margin:5px 0 0;color:rgba(255,255,255,.82);font-size:13px}.context{max-width:45%;padding:9px 12px;border:1px solid rgba(255,255,255,.25);border-radius:8px;background:rgba(255,255,255,.12);text-align:right;font-size:12px;font-weight:700;overflow-wrap:anywhere}
.filters{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:10px;margin:16px 0 10px;padding:14px;background:#fff;border:1px solid var(--line);border-radius:10px}
.field{display:flex;min-width:0;flex-direction:column;gap:5px}.field label{color:#52657b;font-size:11px;font-weight:700}.field select,.field input{width:100%;height:38px;min-width:0;padding:7px 9px;border:1px solid #cfdbe4;border-radius:7px;background:#fff;color:var(--ink);font:inherit}.filter-actions{display:flex;align-items:end}.reset{height:38px;padding:0 14px;border:1px solid #cfdbe4;border-radius:7px;background:#f8fafc;color:var(--ink);font:inherit;font-size:12px;font-weight:700;cursor:pointer}.reset:hover{border-color:var(--teal);color:var(--teal)}
.hint{margin:0 2px 15px;color:var(--muted);font-size:12px}
.section-title{margin:17px 0 8px;padding-bottom:6px;border-bottom:1px solid #dce7ed;color:var(--ink);font-size:13px;font-weight:800;text-transform:uppercase;line-height:1.3}
.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}.kpi{min-width:0;padding:11px;border:1px solid var(--line);border-top:3px solid var(--accent,var(--teal));border-radius:9px;background:linear-gradient(180deg,color-mix(in srgb,var(--accent,var(--teal)) 7%,white),white 55%);box-shadow:0 2px 8px rgba(22,50,79,.05)}.kpi:nth-child(2){--accent:#3b82a0}.kpi:nth-child(3){--accent:#4c9a72}.kpi:nth-child(4){--accent:#6b8eae}.kpi:nth-child(5){--accent:#c48a3a}.kpi:nth-child(6){--accent:#8b6fae}.kpi:nth-child(7){--accent:#b9655c}.kpi:nth-child(8){--accent:#64748b}.kpi h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;color:#52657b}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.value-box{min-width:0;padding:7px;border:1px solid #e9eff3;border-radius:7px;background:#f8fafc}.value-box small{display:block;min-height:30px;color:var(--muted);font-size:10px;overflow-wrap:anywhere}.value-box strong{display:block;font-size:15px;line-height:1.25;overflow-wrap:anywhere}.delta{margin-top:8px;font-size:11px;font-weight:700}.positive{color:#248253}.negative{color:#b13e42}.neutral{color:#718096}
.totals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:8px 0}.total{padding:11px 13px;border-left:4px solid var(--teal);border-radius:7px;background:#fff;box-shadow:0 2px 8px rgba(22,50,79,.05)}.total:nth-child(2){border-left-color:var(--green)}.total span{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase}.total strong{display:block;margin-top:3px;font-size:19px;overflow-wrap:anywhere}
.chart-wrap{min-width:0;padding:7px 8px 2px;border:1px solid var(--line);border-radius:9px;background:#fff}.chart{width:100%;min-height:330px}
.overview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:stretch}.overview-panel{min-width:0}
.management-insights{display:flex;height:340px;box-sizing:border-box;flex-direction:column;gap:6px;padding:10px;overflow:auto;border:1px solid var(--line);border-radius:9px;background:#fff;box-shadow:0 2px 8px rgba(22,50,79,.04)}
.management-insight{display:flex;min-height:0;flex:1 1 0;flex-direction:column;justify-content:center;padding:6px 8px;border-left:3px solid var(--teal);border-radius:6px;background:#f5f8fb;color:#334b62;font-size:11px;line-height:1.3}
.management-insight strong{margin-bottom:2px;color:var(--ink);font-size:11px;font-weight:800}.management-insight.positive{border-left-color:#3b956d;background:#f0f8f3}.management-insight.attention{border-left-color:#d6a400;background:#fff9df}.management-insight.negative{border-left-color:#c74b45;background:#fff1f0}
.table-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:stretch}.table-grid.paired-grid{gap:10px}.paired-grid>div{min-width:0;height:340px;min-height:340px}.table-wrap{width:100%;height:100%;min-width:0;overflow:auto;scrollbar-gutter:stable;border:1px solid var(--line);border-radius:8px;background:#fff;box-shadow:0 2px 8px rgba(22,50,79,.04)}.paired-grid table{width:100%;min-width:520px;height:max-content;table-layout:fixed;border-collapse:collapse;font-size:11px}.paired-grid col:first-child{width:40%}.paired-grid col:nth-child(2),.paired-grid col:nth-child(3),.paired-grid col:nth-child(4){width:20%}.paired-grid thead{position:sticky;top:0;z-index:1;background:var(--teal);color:#fff}.paired-grid th,.paired-grid td{height:40px;padding:8px 8px;text-align:left;vertical-align:middle;line-height:1.25;overflow-wrap:anywhere;white-space:normal}.paired-grid th{height:58px;min-height:58px;font-size:10px}.paired-grid th:not(:first-child),.paired-grid td:not(:first-child){text-align:right;font-variant-numeric:tabular-nums}.paired-grid tbody tr:nth-child(even){background:#f5f9fb}.paired-grid tbody td{color:#334b62;border-top:1px solid #edf1f4}.paired-grid tbody td:first-child{font-weight:600;color:var(--ink)}.paired-grid .empty{display:grid;place-items:center;padding:16px;text-align:center;color:var(--muted)}.foot{margin-top:20px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}
.table-grid.paired-grid.single-grid{grid-template-columns:minmax(0,1fr)}
@media(max-width:1050px){.filters{grid-template-columns:repeat(3,minmax(130px,1fr))}.kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:900px){.overview-grid{grid-template-columns:1fr}}
@media(max-width:650px){main{padding:12px 10px 24px}.hero{align-items:flex-start;flex-direction:column;padding:16px}.context{max-width:100%;text-align:left}.filters{grid-template-columns:repeat(2,minmax(0,1fr));padding:10px}.table-grid.paired-grid:not(.single-grid){grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.paired-grid>div{height:320px;min-height:320px}.paired-grid col:first-child{width:36%}.paired-grid col:nth-child(2),.paired-grid col:nth-child(3),.paired-grid col:nth-child(4){width:21.33%}.paired-grid th,.paired-grid td{padding:7px 5px;font-size:10px}.paired-grid th{font-size:9px}.chart{min-height:320px}.totals{gap:7px}.total strong{font-size:16px}.kpi{padding:9px}.value-box{padding:6px}.value-box strong{font-size:13px}}
@media(max-width:360px){.filters,.kpi-grid{grid-template-columns:1fr}.totals{grid-template-columns:1fr}}
</style>
</head>
<body>
<main>
<header class="hero"><div><h1>Hospital Performance Comparison</h1><p>Executive comparison across operations, revenue, collections, and services</p></div><div class="context" id="context"></div></header>
<section class="filters" aria-label="Comparison filters"><div class="field"><label for="compareBy">Compare By</label><select id="compareBy"></select></div><div id="modeControls" style="display:contents"></div><div class="field"><label for="hospital">Hospital</label><select id="hospital"></select></div><div class="filter-actions"><button type="button" id="reset" class="reset">Reset filters</button></div></section>
<p class="hint">Filters update the embedded tables and chart immediately. Management Key Insights reflect the Comparison Dashboard filters selected when this file was downloaded.</p>
<h2 class="section-title">Performance Comparison</h2><section class="kpi-grid" id="kpis"></section>
<section class="overview-grid"><div class="overview-panel"><h2 class="section-title" id="trendTitle"></h2><section class="totals" id="totals"></section><section class="chart-wrap"><div id="trendChart" class="chart" role="img" aria-label="IPD revenue comparison chart"></div></section></div><div class="overview-panel"><h2 class="section-title">Management Key Insights</h2><section class="management-insights">__INSIGHTS__</section></div></section>
<h2 class="section-title">Department + Doctor-Wise IPD Discharge</h2><section class="table-grid paired-grid"><div id="dischargeDept"></div><div id="dischargeDoctor"></div></section>
<h2 class="section-title">Department + Doctor-Wise IPD Admission</h2><section class="table-grid paired-grid"><div id="admissionDept"></div><div id="admissionDoctor"></div></section>
<h2 class="section-title">OPD Consultation</h2><section class="table-grid paired-grid"><div id="opdDoctorLeft"></div><div id="opdDoctorRight"></div></section>
<h2 class="section-title">IPD Revenue</h2><section class="table-grid paired-grid"><div id="ipdDoctor"></div><div id="ipdDept"></div></section>
<h2 class="section-title">Panel Performance</h2><section class="table-grid paired-grid single-grid"><div id="panels"></div></section>
<h2 class="section-title">OPD Services</h2><section class="table-grid paired-grid single-grid"><div id="services"></div></section>
<footer class="foot">Hospital Performance Comparison Dashboard · Standalone offline HTML</footer>
</main>
<script>__PLOTLY__</script>
<script type="application/json" id="embeddedData">__DATA__</script>
<script>
const ROWS=JSON.parse(document.getElementById('embeddedData').textContent);
const YEARS=__YEARS__, PERIODS=__PERIODS__, INITIAL=__FILTERS__;
const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
const MODES=['Year','Month','From-To','Period','Week','Date'];
const HOSPITALS=['All Hospitals','Surya Hospital','NIMMS Hospital'];
const DEFAULTS={compare_by:'Year',hospital:'All Hospitals',year_1:YEARS[Math.max(0,YEARS.length-2)],year_2:YEARS[YEARS.length-1],month:'All Months',year:YEARS[YEARS.length-1],month_1:'January',month_2:'August',week_month:'January',week_1:1,week_2:2,period_1:PERIODS[0],period_2:PERIODS[1],date_1:`${YEARS[0]}-01-01`,date_2:`${YEARS[YEARS.length-1]}-01-01`,from_1:`${YEARS[0]}-01-01`,to_1:`${YEARS[0]}-03-31`,from_2:`${YEARS[YEARS.length-1]}-01-01`,to_2:`${YEARS[YEARS.length-1]}-03-31`};
const state={...DEFAULTS,...INITIAL};
const moneyFmt=new Intl.NumberFormat('en-IN',{maximumFractionDigits:0});
const numberFmt=new Intl.NumberFormat('en-IN',{maximumFractionDigits:2});
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const numeric=value=>{const number=Number(value);return Number.isFinite(number)?number:0};
const money=value=>'₹ '+moneyFmt.format(numeric(value));
const cellNumber=value=>{const number=numeric(value);return Number.isInteger(number)?moneyFmt.format(number):numberFmt.format(number)};
function parseDate(value){if(!value)return null;const match=String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);if(match)return new Date(+match[1],+match[2]-1,+match[3]);const date=new Date(value);return Number.isNaN(date.getTime())?null:new Date(date.getFullYear(),date.getMonth(),date.getDate())}
const isoDate=date=>date?`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`:'';
const eventDate=row=>{const type=String(row.Type??'').trim().toLowerCase();return parseDate(type==='admission'||type==='opd'?row.DOA:type==='discharge'?row.DOD:null)};
const monthIndex=value=>MONTHS.indexOf(value);
const periodBounds=value=>{const start=Number(String(value).split(' ')[0].split('-')[0]);const end=Number(String(value).split(' ')[0].split('-')[1]);return[start,end]};
const startOfPeriod=(year,month,period)=>{const [start,end]=periodBounds(period);return new Date(year,month,start)};
const endOfPeriod=(year,month,period)=>{const [start,end]=periodBounds(period);return new Date(year,month,Math.min(end,new Date(year,month+1,0).getDate()))};
const pad=value=>String(value).padStart(2,'0');
const displayDate=value=>{const date=parseDate(value);return date?`${pad(date.getDate())} ${MONTHS[date.getMonth()].slice(0,3)} ${date.getFullYear()}`:''};
const monthDays=(year,month)=>new Date(year,monthIndex(month)+1,0).getDate();
function putSelect(parent,label,key,options,format=value=>value){const wrap=document.createElement('div');wrap.className='field';const id=`filter_${key}`;wrap.innerHTML=`<label for="${id}">${esc(label)}</label><select id="${id}" data-key="${esc(key)}">${options.map(value=>`<option value="${esc(value)}">${esc(format(value))}</option>`).join('')}</select>`;parent.appendChild(wrap);const input=wrap.querySelector('select');const selected=String(state[key]);if(!options.some(value=>String(value)===selected))state[key]=String(options[0]);else state[key]=selected;input.value=state[key];input.addEventListener('change',()=>{state[key]=input.value;if(key==='compare_by')renderControls();renderDashboard()})}
function putDate(parent,label,key){const wrap=document.createElement('div');wrap.className='field';const id=`filter_${key}`;wrap.innerHTML=`<label for="${id}">${esc(label)}</label><input id="${id}" type="date" data-key="${esc(key)}" value="${esc(state[key]||'')}"/>`;parent.appendChild(wrap);wrap.querySelector('input').addEventListener('change',event=>{state[key]=event.target.value;renderDashboard()})}
function renderControls(){const mode=document.getElementById('compareBy');mode.innerHTML=MODES.map(value=>`<option value="${value}">${value==='From-To'?'From–To':value}</option>`).join('');mode.value=state.compare_by;mode.onchange=()=>{state.compare_by=mode.value;renderControls();renderDashboard()};const box=document.getElementById('modeControls');box.replaceChildren();const years=YEARS.map(String),months=MONTHS;
if(state.compare_by==='Year'){putSelect(box,'Month','month',['All Months',...months]);putSelect(box,'Year 1','year_1',years);putSelect(box,'Year 2','year_2',years)}
else if(state.compare_by==='Month'){putSelect(box,'Month 1','month_1',months);putSelect(box,'Month 2','month_2',months);putSelect(box,'Year','year',years)}
else if(state.compare_by==='From-To'){putDate(box,'Range 1 From','from_1');putDate(box,'Range 1 To','to_1');putDate(box,'Range 2 From','from_2');putDate(box,'Range 2 To','to_2')}
else if(state.compare_by==='Period'){putSelect(box,'Month','week_month',months);putSelect(box,'Year','year',years);putSelect(box,'Period 1','period_1',PERIODS);putSelect(box,'Period 2','period_2',PERIODS)}
else if(state.compare_by==='Week'){putSelect(box,'Month','week_month',months);putSelect(box,'Year','year',years);const count=Math.ceil(monthDays(Number(state.year),state.week_month)/7);const weeks=Array.from({length:count},(_,index)=>index+1);putSelect(box,'Week 1','week_1',weeks,value=>`Week ${value}`);putSelect(box,'Week 2','week_2',weeks,value=>`Week ${value}`)}
else{putDate(box,'Date 1','date_1');putDate(box,'Date 2','date_2')}
const hospital=document.getElementById('hospital');hospital.innerHTML=HOSPITALS.map(value=>`<option>${value}</option>`).join('');hospital.value=HOSPITALS.includes(state.hospital)?state.hospital:HOSPITALS[0];state.hospital=hospital.value;hospital.onchange=()=>{state.hospital=hospital.value;renderDashboard()}}
function filteredRows(side){const selected=ROWS.filter(row=>state.hospital==='All Hospitals'||row.__hospital_name===state.hospital);const mode=state.compare_by;let start=null,end=null,year=null,month=null,period=null,week=null,exact=null;
if(mode==='Year'){year=Number(state[`year_${side}`]);month=state.month==='All Months'?null:state.month}
else if(mode==='Month'){year=Number(state.year);month=state[`month_${side}`]}
else if(mode==='Week'){year=Number(state.year);month=state.week_month;week=Number(state[`week_${side}`])}
else if(mode==='Period'){year=Number(state.year);month=state.week_month;period=state[`period_${side}`]}
else if(mode==='Date'){exact=state[`date_${side}`];const date=parseDate(exact);year=date?.getFullYear();month=date?MONTHS[date.getMonth()]:null}
else{start=parseDate(state[`from_${side}`]);end=parseDate(state[`to_${side}`])}
if(mode==='From-To'&&(!start||!end||end<start))return[];
return selected.filter(row=>{const date=eventDate(row);if(!date)return false;if(mode==='From-To')return date>=start&&date<=end;if(date.getFullYear()!==year)return false;if(month&&date.getMonth()!==monthIndex(month))return false;if(exact&&isoDate(date)!==exact)return false;if(week&&Math.floor((date.getDate()-1)/7)+1!==week)return false;if(period){const [first,last]=periodBounds(period);if(date.getDate()<first||date.getDate()>last)return false}return true})}
function periodNames(){const mode=state.compare_by;if(mode==='Year'){const left=Number(state.year_1),right=Number(state.year_2);return[state.month==='All Months'?String(left):`${MONTHS[monthIndex(state.month)].slice(0,3)} ${left}`,state.month==='All Months'?String(right):`${MONTHS[monthIndex(state.month)].slice(0,3)} ${right}`]}
if(mode==='Month')return[`${MONTHS[monthIndex(state.month_1)].slice(0,3)} ${state.year}`,`${MONTHS[monthIndex(state.month_2)].slice(0,3)} ${state.year}`];
if(mode==='Week')return[`Week ${state.week_1} ${state.week_month} ${state.year}`,`Week ${state.week_2} ${state.week_month} ${state.year}`];
if(mode==='Period')return[`${state.period_1} ${state.week_month} ${state.year}`,`${state.period_2} ${state.week_month} ${state.year}`];
if(mode==='Date')return[displayDate(state.date_1),displayDate(state.date_2)];
const range=side=>`${displayDate(state[`from_${side}`])}–${displayDate(state[`to_${side}`])}`;return[range('1'),range('2')]}
function sum(rows,predicate,key){return rows.reduce((total,row)=>predicate(row)?total+numeric(row[key]):total,0)}
function metrics(rows,hospital){const discharge=row=>String(row.Type??'').trim().toLowerCase()==='discharge';const revenue=sum(rows,row=>row.Type==='OPD'||row.Type==='Discharge','Service Final Amount');const bedDays=sum(rows,discharge,'Bed Days');const discharges=rows.filter(discharge).length;const doaDays=new Set(rows.map(row=>parseDate(row.DOA)).filter(Boolean).map(isoDate));const beds=hospital==='NIMMS Hospital'?50:hospital==='All Hospitals'?205:155;return{revenue,cash:sum(rows,discharge,'Cash Due'),credit:sum(rows,discharge,'Credit Due'),opd:rows.filter(row=>row.Type==='OPD'&&String(row['Service Type']??'').toLowerCase()==='consultation').length,admissions:rows.filter(row=>row.Type==='Admission').length,discharges,occupancy:doaDays.size&&beds?bedDays/(beds*doaDays.size)*100:0,alos:discharges?bedDays/discharges:0}}
function change(current,previous){if(previous===0)return current===0?'0.0%':current>0?'New':'N/A';return`${((current-previous)/previous*100)>=0?'+':''}${(((current-previous)/previous)*100).toFixed(1)}%`}
function card(label,left,right,leftLabel,rightLabel,format){const delta=change(right,left);const inverse=['Cash Due','Credit Due','ALOS'].includes(label);const numericDelta=delta==='New'?1:parseFloat(delta);const tone=delta==='N/A'?'neutral':delta==='New'?(inverse?'negative':'positive'):numericDelta===0?'neutral':(numericDelta>0)!==inverse?'positive':'negative';return`<article class="kpi"><h3>${esc(label)}</h3><div class="pair"><div class="value-box"><small>${esc(leftLabel)}</small><strong>${format(left)}</strong></div><div class="value-box"><small>${esc(rightLabel)}</small><strong>${format(right)}</strong></div></div><div class="delta ${tone}">Change ${esc(delta)}</div></article>`}
function renderKpis(left,right,labels){const spec=[['Revenue','revenue',money],['Admissions','admissions',cellNumber],['Discharges','discharges',cellNumber],['OPD Consultation','opd',cellNumber],['Occupancy','occupancy',value=>`${numeric(value).toFixed(1)}%`],['ALOS','alos',value=>`${numeric(value).toFixed(2)} days`],['Cash Due','cash',money],['Credit Due','credit',money]];document.getElementById('kpis').innerHTML=spec.map(([label,key,format])=>card(label,left[key],right[key],labels[0],labels[1],format)).join('')}
function groupValues(rows,predicate,key,valueFn,options={}){const values=new Map();for(const row of rows){if(!predicate(row))continue;let name=key(row);if(options.unknown&&(!String(name??'').trim()))name=options.unknown;if(name===undefined||name===null)continue;const label=String(name);values.set(label,(values.get(label)||0)+valueFn(row))}return values}
const discharge=row=>String(row.Type??'').trim().toLowerCase()==='discharge';
function comparisons(leftMap,rightMap,metric){const names=[...new Set([...leftMap.keys(),...rightMap.keys()])];if(!names.length)return[{name:'No data',left:0,right:0,delta:'0.0%'}];const rows=names.map(name=>{const left=leftMap.get(name)||0,right=rightMap.get(name)||0;return{name,left,right,delta:change(right,left)}});rows.sort((a,b)=>b.right-a.right);return rows}
function tableHtml(rows,leftLabel,rightLabel,options={}){const first=options.first||'Metric',leftHead=options.leftHead||leftLabel,rightHead=options.rightHead||rightLabel;const body=rows.length?rows.map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(cellNumber(row.left))}</td><td>${esc(cellNumber(row.right))}</td><td>${esc(row.delta)}</td></tr>`).join(''):`<tr><td class="empty" colspan="4">${esc(options.emptyText||'No data')}</td></tr>`;return`<div class="table-wrap"><table><colgroup><col><col><col><col></colgroup><thead><tr><th>${esc(first)}</th><th>${esc(leftHead)}</th><th>${esc(rightHead)}</th><th>Δ %</th></tr></thead><tbody>${body}</tbody></table></div>`}
function dimensionCount(rows,type,column,unknown){return groupValues(rows,row=>row.Type===type,row=>{const value=row[column];return value===undefined?null:value},()=>1,{unknown})}
function revenueBy(rows,column,fallback){return groupValues(rows,discharge,row=>row[column]===undefined?row[fallback]:row[column],row=>numeric(row['Service Final Amount']))}
function countBreakdown(rows,type,column,unknown){return dimensionCount(rows,type,column,unknown)}
function getServiceTable(rows){const counts=groupValues(rows,row=>String(row.Type??'').trim().toLowerCase()==='opd'&&String(row['Service Name']??'').trim()!==''&&String(row['Service Name']??'').toLowerCase()!=='consultation',row=>String(row['Service Name']).trim(),()=>1);const common=[...counts.entries()].filter(([,count])=>count>=2).sort((a,b)=>b[1]-a[1]);const singletons=[...counts.entries()].filter(([,count])=>count===1);if(singletons.length)common.push(['Others',singletons.length]);return new Map(common)}
function renderTables(left,right,labels){const rows=(l,r)=>comparisons(l,r);document.getElementById('dischargeDept').innerHTML=tableHtml(rows(countBreakdown(left,'Discharge','Dept. Name','Unknown'),countBreakdown(right,'Discharge','Dept. Name','Unknown')),labels[0],labels[1],{first:'Department'});document.getElementById('dischargeDoctor').innerHTML=tableHtml(rows(countBreakdown(left,'Discharge','Doctor Name','Unknown'),countBreakdown(right,'Discharge','Doctor Name','Unknown')),labels[0],labels[1],{first:'Doctor'});document.getElementById('admissionDept').innerHTML=tableHtml(rows(countBreakdown(left,'Admission','Dept. Name','Unknown'),countBreakdown(right,'Admission','Dept. Name','Unknown')),labels[0],labels[1],{first:'Department'});document.getElementById('admissionDoctor').innerHTML=tableHtml(rows(countBreakdown(left,'Admission','Doctor Name','Unknown'),countBreakdown(right,'Admission','Doctor Name','Unknown')),labels[0],labels[1],{first:'Doctor'});const opdFilter=row=>row.Type==='OPD'&&String(row['Service Type']??'').toLowerCase()==='consultation';const opdRows=rows(groupValues(left,opdFilter,row=>String(row['Doctor Name']??'').trim()||'Unknown / Not Assigned',()=>1),groupValues(right,opdFilter,row=>String(row['Doctor Name']??'').trim()||'Unknown / Not Assigned',()=>1));const opdSplit=Math.ceil(opdRows.length/2);document.getElementById('opdDoctorLeft').innerHTML=tableHtml(opdRows.slice(0,opdSplit),labels[0],labels[1],{first:'Doctor'});document.getElementById('opdDoctorRight').innerHTML=tableHtml(opdRows.slice(opdSplit),labels[0],labels[1],{first:'Doctor',emptyText:'No additional doctors'});document.getElementById('ipdDoctor').innerHTML=tableHtml(rows(revenueBy(left,'Doctor Name','Doctor'),revenueBy(right,'Doctor Name','Doctor')),labels[0],labels[1],{first:'Doctor'});document.getElementById('ipdDept').innerHTML=tableHtml(rows(revenueBy(left,'Dept. Name','Department'),revenueBy(right,'Dept. Name','Department')),labels[0],labels[1],{first:'Department'});const panel=(items)=>groupValues(items,row=>discharge(row)&&Boolean(parseDate(row.DOD))&&Object.prototype.hasOwnProperty.call(row,'UHID'),row=>String(row['Panel Name']??'').trim()||'Unknown',row=>numeric(row['Service Final Amount']));document.getElementById('panels').innerHTML=tableHtml(rows(panel(left),panel(right)),labels[0],labels[1],{first:'Panel'});document.getElementById('services').innerHTML=tableHtml(rows(getServiceTable(left),getServiceTable(right)),labels[0],labels[1],{first:'Service'})}
function dailyRevenue(rows){return groupValues(rows,row=>row.Type==='Discharge'&&Boolean(parseDate(row.DOD)),row=>isoDate(parseDate(row.DOD)),row=>numeric(row['Service Final Amount']))}
function trendValues(rows,side){const mode=state.compare_by;const values=new Map();if(mode==='Month'){const breakdown=groupValues(rows,discharge,row=>{const date=parseDate(row.DOD);return date?PERIODS[Math.min(2,Math.floor((date.getDate()-1)/10))]:null},row=>numeric(row['Service Final Amount']));return new Map(PERIODS.map(period=>[period,breakdown.get(period)||0]))}
if(mode==='Period'){const selected=state[`period_${side}`],range=periodBounds(selected);const total=sum(rows,row=>discharge(row)&&Boolean(parseDate(row.DOD))&&parseDate(row.DOD).getDate()>=range[0]&&parseDate(row.DOD).getDate()<=range[1],'Service Final Amount');return new Map([[selected,total]])}
if(mode==='Week'){const key=`Week ${state[`week_${side}`]}`;return new Map([[key,[...dailyRevenue(rows).values()].reduce((a,b)=>a+b,0)]])}
if(mode==='Year'){const daily=dailyRevenue(rows);return new Map(MONTHS.map((name,index)=>[name.slice(0,3),[...daily.entries()].filter(([date])=>parseDate(date).getMonth()===index).reduce((total,item)=>total+item[1],0)]))}
if(mode==='From-To'){const start=parseDate(state[`from_${side}`]),end=parseDate(state[`to_${side}`]);if(!start||!end)return values;const months=[];for(let date=new Date(start.getFullYear(),start.getMonth(),1);date<=end;date=new Date(date.getFullYear(),date.getMonth()+1,1))months.push(date);const daily=dailyRevenue(rows);return new Map(months.map(date=>{const key=`${MONTHS[date.getMonth()].slice(0,3)} ${date.getFullYear()}`;const revenue=[...daily.entries()].filter(([day])=>{const parsed=parseDate(day);return parsed.getFullYear()===date.getFullYear()&&parsed.getMonth()===date.getMonth()}).reduce((total,item)=>total+item[1],0);return[key,revenue]}))}
return dailyRevenue(rows)}
function trendSettings(){const settings={Year:['IPD Revenue Trend by Month','Month'],Month:['IPD Revenue Trend by Period','Period'],'From-To':['IPD Revenue Trend by Month','Month'],Period:['IPD Revenue for Selected Period','Period'],Week:['IPD Revenue Trend by Week','Week of Month'],Date:['Daily IPD Revenue','Date']};return settings[state.compare_by]}
function drawTrend(left,right,labels){const [title,axis]=trendSettings();document.getElementById('trendTitle').textContent=title;const first=trendValues(left,'1'),second=trendValues(right,'2');const buckets=[...new Set([...first.keys(),...second.keys()])];const y1=buckets.map(key=>first.get(key)||0),y2=buckets.map(key=>second.get(key)||0);const total1=[...first.values()].reduce((a,b)=>a+b,0),total2=[...second.values()].reduce((a,b)=>a+b,0);document.getElementById('totals').innerHTML=`<div class="total"><span>Total IPD Revenue · ${esc(labels[0])}</span><strong>${money(total1)}</strong></div><div class="total"><span>Total IPD Revenue · ${esc(labels[1])}</span><strong>${money(total2)}</strong></div>`;const maximum=Math.max(0,...y1,...y2);const chart=document.getElementById('trendChart');const traces=[{type:'bar',name:labels[0],x:buckets,y:y1,marker:{color:'#087e8b'},texttemplate:'₹%{y:,.0f}',textposition:'outside',cliponaxis:false,hovertemplate:'%{x}<br>IPD Revenue: ₹%{y:,.0f}<extra></extra>'},{type:'bar',name:labels[1],x:buckets,y:y2,marker:{color:'#4c9a72'},texttemplate:'₹%{y:,.0f}',textposition:'outside',cliponaxis:false,hovertemplate:'%{x}<br>IPD Revenue: ₹%{y:,.0f}<extra></extra>'}];const layout={height:340,autosize:true,barmode:'group',showlegend:false,paper_bgcolor:'#fff',plot_bgcolor:'#fff',margin:{l:56,r:20,t:18,b:62},font:{family:'Segoe UI, Tahoma, sans-serif',color:'#52657b',size:11},xaxis:{title:axis,tickangle:-35,automargin:true},yaxis:{title:'IPD Revenue (₹)',tickformat:',.0f',rangemode:'tozero',range:[0,maximum?maximum*1.28:1],automargin:true,gridcolor:'#e9eff3'}};Plotly.react(chart,traces,layout,{responsive:true,displayModeBar:false})}
function renderDashboard(){const labels=periodNames();document.getElementById('context').textContent=`${state.hospital} · ${labels[0]} vs ${labels[1]}`;const left=filteredRows('1'),right=filteredRows('2');renderKpis(metrics(left,state.hospital),metrics(right,state.hospital),labels);renderTables(left,right,labels);drawTrend(left,right,labels)}
function resetFilters(){Object.assign(state,DEFAULTS);renderControls();renderDashboard()}
document.getElementById('reset').addEventListener('click',resetFilters);
renderControls();renderDashboard();
</script>
</body>
</html>'''
    return (
        template.replace("__DATA__", data_json)
        .replace("__YEARS__", years_json)
        .replace("__PERIODS__", periods_json)
        .replace("__FILTERS__", filters_json)
        .replace("__INSIGHTS__", insights_html)
        .replace("__PLOTLY__", plotly_js)
    )
