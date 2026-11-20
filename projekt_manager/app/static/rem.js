// Pripomienky: zobrazenie poľa s časom a voľby e-mailu, vloženie fotky cez Ctrl+V
(function () {
  function sync(form) {
    const checked = form.querySelector('[data-when] input[name=when]:checked');
    const v = checked ? checked.value : 'nie';
    const time = form.querySelector('.rem-time'), mail = form.querySelector('[data-email]');
    if (time) {
      time.hidden = v !== 'cas';
      time.required = v === 'cas';
      if (v === 'cas' && !time.value) {
        const d = new Date(Date.now() + 3600e3); d.setMinutes(0, 0, 0);
        const p = (n) => String(n).padStart(2, '0');
        time.value = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:00`;
      }
    }
    if (mail) mail.hidden = v === 'nie';
  }
  document.querySelectorAll('.rem-form').forEach((form) => {
    form.querySelectorAll('[data-when] input[name=when]').forEach((r) => r.addEventListener('change', () => sync(form)));
    sync(form);
    // bez projektu nemá zmysel voľba „členovia projektu“
    const proj = form.querySelector('[data-project]'), vis = form.querySelector('select[name=visibility]');
    if (proj && vis) {
      const opt = vis.querySelector('option[value=projekt]');
      const upd = () => { if (opt) { opt.disabled = !proj.value; if (!proj.value && vis.value === 'projekt') vis.value = 'ja'; } };
      proj.addEventListener('change', upd); upd();
    }
  });
  let lastForm = null;
  document.addEventListener('focusin', (e) => { const f = e.target.closest && e.target.closest('.rem-form'); if (f) lastForm = f; });
  document.addEventListener('paste', (e) => {
    if (!lastForm || !e.clipboardData) return;
    const file = Array.from(e.clipboardData.files || []).find((f) => f.type.startsWith('image/'));
    const input = lastForm.querySelector('input[type=file][name=image]');
    if (!file || !input) return;
    const dt = new DataTransfer(); dt.items.add(new File([file], 'foto.png', { type: file.type })); input.files = dt.files;
  });
})();
