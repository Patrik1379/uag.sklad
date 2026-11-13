(function () {
  // --- Prepínač veľkosti písma ----------------------------------------------
  function markSize() {
    const cur = document.documentElement.dataset.size || "";
    document.querySelectorAll(".fontsize button").forEach((b) => b.classList.toggle("on", b.dataset.size === cur));
  }
  document.querySelectorAll(".fontsize button").forEach((b) => b.addEventListener("click", () => {
    const v = b.dataset.size;
    if (v) document.documentElement.dataset.size = v; else delete document.documentElement.dataset.size;
    try { localStorage.setItem("fontsize", v); } catch (e) {}
    markSize();
  }));
  markSize();

  // --- Nahrávanie obrázkov ťahaním / klikom / zo schránky -----------------
  const csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  function toast(msg, kind) {
    const t = document.createElement("div");
    t.className = "toast " + (kind || "ok");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }
  async function uploadTo(zone, file) {
    if (!file || !file.type.startsWith("image/")) { toast("Toto nie je obrázok.", "error"); return; }
    const fd = new FormData();
    fd.append("image", file, file.name || "obrazok.png");
    zone.classList.add("busy");
    try {
      const r = await fetch(zone.dataset.upload, { method: "POST", body: fd, headers: { "X-CSRF": csrf } });
      let data = {};
      try { data = await r.json(); } catch (e) {}
      if (!r.ok) throw new Error(data.error || "Nahratie zlyhalo (kód " + r.status + ").");
      const old = zone.querySelector(".thumb");
      const img = document.createElement("img");
      img.className = "thumb " + (zone.dataset.size || "");
      img.src = data.thumb; img.dataset.full = data.full; img.alt = "";
      old.replaceWith(img);
      zone.title = "Pretiahnite sem nový obrázok" + (zone.hasAttribute("data-paste") ? " (alebo Ctrl+V)" : "");
      toast("Obrázok bol uložený.");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      zone.classList.remove("busy");
    }
  }
  document.querySelectorAll(".imgdrop[data-upload]").forEach((zone) => {
    const input = zone.querySelector("input[type=file]");
    zone.addEventListener("dragenter", (e) => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove("over"); });
    zone.addEventListener("drop", (e) => {
      e.preventDefault(); e.stopPropagation(); zone.classList.remove("over");
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) uploadTo(zone, f);
    });
    zone.addEventListener("click", (e) => {
      if (e.target.closest(".imgdrop-btn") || e.target.closest(".thumb.ph")) { e.preventDefault(); e.stopPropagation(); input.click(); }
    }, true);
    input.addEventListener("change", () => { if (input.files[0]) uploadTo(zone, input.files[0]); input.value = ""; });
  });
  // súbor pustený mimo miniatúry nesmie otvoriť obrázok namiesto aplikácie
  window.addEventListener("dragover", (e) => { if (!e.target.closest || !e.target.closest(".imgdrop, input[type=file]")) e.preventDefault(); });
  window.addEventListener("drop", (e) => { if (!e.target.closest || !e.target.closest(".imgdrop, input[type=file]")) e.preventDefault(); });
  // Ctrl+V: vloženie obrázka zo schránky (napr. screenshot) na stránke s jednou "paste" miniatúrou
  document.addEventListener("paste", (e) => {
    const zone = document.querySelector(".imgdrop[data-paste]");
    if (!zone || (e.target.closest && e.target.closest("input, textarea"))) return;
    const item = Array.from(e.clipboardData.items || []).find((i) => i.type.startsWith("image/"));
    if (!item) return;
    e.preventDefault();
    const f = item.getAsFile();
    uploadTo(zone, new File([f], "schranka." + (f.type.split("/")[1] || "png"), { type: f.type }));
  });

  // --- Lightbox pre obrázky dielov ------------------------------------------
  const lb = document.createElement("div");
  lb.className = "lightbox";
  lb.innerHTML = "<img alt=''>";
  document.body.appendChild(lb);
  lb.addEventListener("click", () => lb.classList.remove("open"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") lb.classList.remove("open"); });
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-full]");
    if (!t) return;
    e.preventDefault();
    lb.querySelector("img").src = t.dataset.full;
    lb.classList.add("open");
  });

  // --- Tooltip pre grafy -------------------------------------------------------
  const tip = document.createElement("div");
  tip.className = "tip";
  document.body.appendChild(tip);
  document.addEventListener("mousemove", (e) => {
    const t = e.target.closest("[data-tip]");
    if (!t) { tip.style.display = "none"; return; }
    tip.innerHTML = t.dataset.tip;
    tip.style.display = "block";
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  });

  // --- Potvrdenie nebezpečných akcií ----------------------------------------
  document.addEventListener("submit", (e) => {
    const msg = e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // --- Príjem: automatické rozdelenie kusov ---------------------------------
  const total = document.getElementById("qty-total");
  const allocs = Array.from(document.querySelectorAll(".alloc-input"));
  const sumOut = document.getElementById("alloc-sum");
  function updateSum() {
    if (!sumOut) return;
    const s = allocs.reduce((a, i) => a + (parseInt(i.value, 10) || 0), 0);
    sumOut.textContent = s;
  }
  function distribute() {
    let left = parseInt(total.value, 10) || 0;
    allocs.forEach((inp) => {
      if (inp.dataset.free !== undefined) return;
      const need = parseInt(inp.dataset.missing, 10) || 0;
      const take = Math.min(need, left);
      inp.value = take > 0 ? take : "";
      left -= take;
    });
    const free = allocs.find((i) => i.dataset.free !== undefined);
    if (free) free.value = left > 0 ? left : "";
    updateSum();
  }
  if (total) {
    total.addEventListener("input", distribute);
    distribute();
  }
  allocs.forEach((i) => i.addEventListener("input", updateSum));
  updateSum();

  // --- Našepkávač dielov (pridanie do projektu) -----------------------------
  const ac = document.getElementById("part-ac");
  if (ac) {
    const list = document.getElementById("part-ac-list");
    let timer;
    ac.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = ac.value.trim();
        if (!q) return;
        const r = await fetch("/api/diely?q=" + encodeURIComponent(q));
        const rows = await r.json();
        list.innerHTML = "";
        rows.forEach((p) => {
          const o = document.createElement("option");
          o.value = p.code + " — " + (p.order_code || "") + " — " + p.name;
          list.appendChild(o);
        });
      }, 180);
    });
  }
})();
