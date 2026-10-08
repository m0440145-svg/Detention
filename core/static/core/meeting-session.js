(() => {
  const list = document.getElementById('agenda-sort');
  const order = document.getElementById('agenda-order-value');
  const sync = () => { if (list && order) order.value = [...list.querySelectorAll('[data-id]')].map(el => el.dataset.id).join(','); };
  if (list) {
    let dragged;
    list.addEventListener('dragstart', e => { dragged = e.target.closest('[data-id]'); });
    list.addEventListener('dragover', e => { e.preventDefault(); });
    list.addEventListener('drop', e => { e.preventDefault(); const target=e.target.closest('[data-id]'); if (dragged && target && target!==dragged) { list.insertBefore(dragged,target); sync(); } });
    list.addEventListener('click', e => { const button=e.target.closest('[data-agenda-move]'); if (!button) return; const item=button.closest('[data-id]'); if (button.dataset.agendaMove==='up' && item.previousElementSibling) list.insertBefore(item,item.previousElementSibling); if (button.dataset.agendaMove==='down' && item.nextElementSibling) list.insertBefore(item.nextElementSibling,item); sync(); });
    sync(); document.getElementById('agenda-order')?.addEventListener('submit',sync);
  }
  let scale = 1;
  document.querySelectorAll('[data-reading-size]').forEach(button => button.addEventListener('click', () => { scale=Math.max(0.9,Math.min(1.8,scale+(button.dataset.readingSize==='increase'?0.1:-0.1))); document.getElementById('agenda-reading').style.fontSize=scale+'em'; }));
})();
