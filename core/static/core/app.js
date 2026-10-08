'use strict';
document.querySelector('.mobile-toggle')?.addEventListener('click',()=>document.querySelector('.sidebar').classList.toggle('open'));
document.querySelectorAll('.sidebar nav>a').forEach(a=>{const u=new URL(a.href);if(u.pathname===location.pathname)a.classList.add('active');});
function showTab(id){const target=document.getElementById(id);if(!target?.classList.contains('tab-content'))return;document.querySelectorAll('.tab-content').forEach(p=>p.hidden=p.id!==id);document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('selected',b.dataset.tab===id);b.setAttribute('aria-selected',b.dataset.tab===id);});}
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{showTab(b.dataset.tab);history.replaceState(null,'','#'+b.dataset.tab);}));
const hash=location.hash.slice(1);if(hash==='escalate'){showTab('details');document.getElementById('escalate')?.setAttribute('open','');}else showTab(hash);
document.querySelectorAll('[data-confirm]').forEach(f=>f.addEventListener('submit',e=>{if(!confirm(f.dataset.confirm))e.preventDefault();}));
document.querySelectorAll('[data-export]').forEach(a=>{const u=new URL(location.href);u.searchParams.set('export',a.dataset.export);a.href=u.toString();});
document.querySelectorAll('[data-page]').forEach(a=>{const u=new URL(location.href);u.searchParams.set('page',a.dataset.page);a.href=u.toString();});
document.querySelector('[data-print]')?.addEventListener('click',()=>window.print());


document.querySelectorAll("[data-bulk-task]").forEach(box=>box.addEventListener("change",()=>{document.querySelectorAll("[data-bulk-task]").forEach(other=>{if(other.dataset.bulkTask===box.dataset.bulkTask)other.checked=box.checked;});}));
// Accessible editing tools preserve the textarea as the canonical submitted value.
document.addEventListener('DOMContentLoaded',()=>{
const text=document.querySelector('textarea[name="body"]'),rich=document.querySelector('input[name="rich_text"]');
if(text&&rich){const tools=document.createElement('div');tools.className='fields-row';tools.setAttribute('role','toolbar');tools.setAttribute('aria-label','أدوات تحرير النص');
for(const [label,start,end] of [['فقرة','<p>','</p>'],['عريض','<strong>','</strong>'],['عنوان','<h2>','</h2>'],['جدول','<table><tr><th>عنوان</th><th>عنوان</th></tr><tr><td>','</td><td>بيانات</td></tr></table>']]){const b=document.createElement('button');b.type='button';b.className='btn secondary';b.textContent=label;b.addEventListener('click',()=>{const a=text.selectionStart,z=text.selectionEnd;text.setRangeText(start+text.value.slice(a,z)+end,a,z,'end');rich.checked=true;text.focus();});tools.append(b);}text.before(tools);}
document.addEventListener('keydown',e=>{if(e.altKey&&e.key.toLowerCase()==='n'){e.preventDefault();location.href='/communications/register/';}if(e.altKey&&e.key.toLowerCase()==='s'){e.preventDefault();document.querySelector('input[name="q"]')?.focus();}});
});
