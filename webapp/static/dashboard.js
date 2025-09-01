// webapp/static/dashboard.js
let dayChart, catChart;
let currentPage = 1;
const pageSize = 20;

function $(id){ return document.getElementById(id); }
function fmt(n){ return Number(n).toLocaleString(); }
function qs(){ 
  function withSeconds(v){
    if (!v) return v;
    // if value is YYYY-MM-DDTHH:MM, add :00
    return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v) ? (v + ':00') : v;
  }

  const user = $('userSel').value;
  const tag = ($('tagInp').value || '').trim();
  const start = withSeconds($('startInp').value);
  const end = withSeconds($('endInp').value);

  const params = new URLSearchParams();
  if (user) params.set('user', user);
  if (tag) params.set('category', tag);
  if (start) params.set('start', start);
  if (end) params.set('end', end);

  return params;
}

async function loadUsers(){
  const r = await fetch('/api/users');
  const users = await r.json();
  const sel = $('userSel');
  sel.innerHTML = '<option value="">All users</option>';
  for(const u of users){
    const opt = document.createElement('option');
    opt.value = u.id;
    opt.textContent = u.name;  // shows @username if we have it
    sel.appendChild(opt);
  }
}

async function loadKPIsAndCharts(){
  const params = qs();

  // table count approximation: ask page=1 (we’ll read total)
  const tableRes = await fetch('/api/reports_table?'+params+'&page=1&page_size='+pageSize);
  const tableJson = await tableRes.json();
  $('kpiCount').textContent = fmt(tableJson.total);

  // compute totals from the current page (quick) + charts from fast endpoints
  const pos = tableJson.rows.filter(r=>r.amount>0).reduce((s,r)=>s+r.amount,0);
  const neg = tableJson.rows.filter(r=>r.amount<0).reduce((s,r)=>s+r.amount,0);
  $('kpiPos').textContent = fmt(pos);
  $('kpiNeg').textContent = fmt(neg);
  $('kpiTotal').textContent = fmt(pos+neg);

  // day chart
  const d = await (await fetch('/api/summary/day_fast?'+params)).json();
  drawLine('dayChart', d.labels, d.values, 'Total');

  // category chart
  const c = await (await fetch('/api/summary/topcats_fast?'+params)).json();
  drawBar('catChart', c.labels, c.values, 'Total');
}

function drawLine(canvasId, labels, values, label){
  const ctx = $(canvasId).getContext('2d');
  if (dayChart && canvasId==='dayChart'){ dayChart.destroy(); }
  dayChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ label, data: values }] },
    options: { responsive: true, scales: { y: { beginAtZero: true } } }
  });
}

function drawBar(canvasId, labels, values, label){
  const ctx = $(canvasId).getContext('2d');
  if (catChart && canvasId==='catChart'){ catChart.destroy(); }
  catChart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ label, data: values }] },
    options: { responsive: true, scales: { y: { beginAtZero: true } } }
  });
}

async function loadTable(page=1){
  currentPage = page;
  const params = qs();
  params.set('page', page);
  params.set('page_size', pageSize);
  const r = await fetch('/api/reports_table?'+params);
  const j = await r.json();

  const tb = $('rowsTbody');
  tb.innerHTML = '';
  if (j.rows.length === 0){
    tb.innerHTML = `<tr><td class="px-4 py-3 text-slate-400" colspan="7">No data</td></tr>`;
  }
  for(const row of j.rows){
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="px-4 py-3">${row.user_name}</td>
      <td class="px-4 py-3">#${row.id}</td>
      <td class="px-4 py-3">${row.when_iso?.replace('T',' ').slice(0,16) ?? ''}</td>
      <td class="px-4 py-3">${row.category || ''}</td>
      <td class="px-4 py-3">
        <input class="w-full px-2 py-1 border rounded" value="${(row.note || '').replaceAll('"','&quot;')}">
      </td>
      <td class="px-4 py-3 text-right">
        <input class="w-28 text-right px-2 py-1 border rounded" value="${row.amount}">
      </td>
      <td class="px-4 py-3 text-center">
        <button class="px-2 py-1 text-xs border rounded saveBtn">Save</button>
        <button class="ml-2 px-2 py-1 text-xs border rounded delBtn">Delete</button>
      </td>
    `;
    // wire actions
    const [noteInp, amtInp] = tr.querySelectorAll('input');
    tr.querySelector('.saveBtn').onclick = async ()=>{
      const fd = new FormData();
      fd.set('user_id', row.user_id);
      fd.set('entry_id', row.id);
      fd.set('note', noteInp.value);
      fd.set('amount', amtInp.value);
      await fetch('/api/report/update', { method:'POST', body: fd });
      await loadKPIsAndCharts();
    };
    tr.querySelector('.delBtn').onclick = async ()=>{
      if (!confirm('Delete this entry?')) return;
      const fd = new FormData();
      fd.set('user_id', row.user_id);
      fd.set('entry_id', row.id);
      await fetch('/api/report/delete', { method:'POST', body: fd });
      await loadTable(currentPage);
      await loadKPIsAndCharts();
    };
    tb.appendChild(tr);
  }

  const pages = Math.max(1, Math.ceil(j.total / j.page_size));
  $('pageInfo').textContent = `Page ${j.page} / ${pages} • ${j.total.toLocaleString()} rows`;
  $('prevBtn').disabled = j.page <= 1;
  $('nextBtn').disabled = j.page >= pages;
}

function bind(){
  $('applyBtn').onclick = async ()=>{
    currentPage = 1;
    await Promise.all([loadKPIsAndCharts(), loadTable(1)]);
    // export link
    const params = qs();
    $('exportBtn').href = '/export.csv?'+params.toString();
  };
  $('prevBtn').onclick = ()=> loadTable(Math.max(1, currentPage-1));
  $('nextBtn').onclick = ()=> loadTable(currentPage+1);
}

(async function init(){
  bind();
  await loadUsers();
  $('applyBtn').click(); // initial load
})();
