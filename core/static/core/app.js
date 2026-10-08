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

