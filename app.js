const $=id=>document.getElementById(id);
const bytes=n=>{n=Number(n)||0;const units=['B','KiB','MiB','GiB','TiB'];let i=0;while(n>=1024&&i<4){n/=1024;i++}return `${n.toFixed(i?2:0)} ${units[i]}`};
const sum=x=>(x?.tx||0)+(x?.rx||0);
const label=x=>({'__other__':'其他（超过当日细分上限）','__unknown__':'未知','__invalid_host__':'无法识别的目标'}[x]||x);
const when=x=>x?new Date(x*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无数据';
function query(){return new URLSearchParams({period:$('period').value,kind:$('kind').value,route:$('route').value})}
let requestSerial=0;
async function refresh(){
 const serial=++requestSerial,params=query();
 try{
  const response=await fetch('/api/report?'+params);if(!response.ok)throw Error('读取失败');const d=await response.json();if(serial!==requestSerial)return;
  const t=sum(d.totals.total),o=sum(d.totals.observed),missing=Math.max(0,t-o),h=d.health;
  $('total').textContent=bytes(t);$('observed').textContent=bytes(o);$('missing').textContent=bytes(missing);$('proxy').textContent=bytes(sum(d.routes.find(x=>x.route==='proxy')));
  $('coverage').textContent=t?`覆盖率 ${Math.min(100,o/t*100).toFixed(2)}%${o>t?' · 存在快照边界差异':''}`:'等待产生新流量';
  const fresh=h.last_success&&Date.now()/1000-h.last_success<90&&!h.current_error;
  $('status').textContent=fresh?'采集正常':'等待采集 / 连接中断';$('status').className='badge'+(fresh?'':' warn');
  $('range').textContent=`${d.from} 至 ${d.to}`;$('export').href='/export.csv?'+params;
  const tbody=$('rows');tbody.replaceChildren();const denominator=sum(d.matched_totals);
  for(const r of d.rows.slice(0,100)){const tr=document.createElement('tr');for(const value of [label(r.label),bytes(r.tx),bytes(r.rx),bytes(sum(r)),denominator?(sum(r)/denominator*100).toFixed(1)+'%':'—']){const td=document.createElement('td');td.textContent=value;tr.append(td)}tbody.append(tr)}
  $('empty').hidden=!!d.rows.length;$('empty').textContent='该范围暂时没有记录。采集开始前的数据无法补回。';
  $('table-note').textContent=`页面显示前 100 项，CSV 最多导出前 2000 项。占比以当前路径全部已归属流量为分母。${d.kind==='process'?' 当前进程识别模式：'+(h.process_mode||'未知')+'；未提供进程的连接归为“未知”。':''}`;
  const daily=$('daily');daily.replaceChildren();const days=d.days.filter(x=>x.kind==='total');const max=Math.max(1,...days.map(sum));
  for(const r of days){const div=document.createElement('div');div.className='daily-row';const day=document.createElement('span');day.textContent=r.day;const bar=document.createElement('progress');bar.max=max;bar.value=sum(r);bar.setAttribute('aria-label',r.day+' '+bytes(sum(r)));const total=document.createElement('span');total.textContent=bytes(sum(r));div.append(day,bar,total);daily.append(div)}
  if(!days.length)daily.textContent='暂无每日数据';
  $('health').textContent=`采集开始：${when(h.first_started)} · 最近采样：${when(h.last_success)} · 最近入库：${when(h.last_flush)} · 数据库：${bytes(d.database_bytes)} · 内核：${h.core_version||'未知'} · 采样间隙记录：${h.gaps?.length||0} · 内核重启：${h.core_resets||0}${h.current_error?' · 当前错误：'+h.current_error:''}`;
 }catch(e){if(serial!==requestSerial)return;$('status').textContent='暂时无法读取统计';$('status').className='badge warn'}
}
for(const id of ['period','kind','route'])$(id).addEventListener('change',refresh);$('refresh').addEventListener('click',refresh);refresh();setInterval(refresh,60000);
