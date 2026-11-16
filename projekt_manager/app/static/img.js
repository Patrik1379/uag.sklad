/* Obrázky projektov: nahratie ťahaním / kliknutím / Ctrl+V a zväčšenie po kliknutí. */
(function () {
  "use strict";
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrf = csrfMeta ? csrfMeta.content : "";

  function toast(msg, kind) {
    var t = document.createElement("div");
    t.className = "toast " + (kind || "ok");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, kind === "error" ? 6000 : 2500);
  }

  function uploadTo(zone, file) {
    if (!file || !/^image\//.test(file.type)) { toast("Toto nie je obrázok.", "error"); return; }
    var fd = new FormData();
    fd.append("image", file, file.name || "obrazok.png");
    zone.classList.add("busy");
    fetch(zone.dataset.upload, { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf }, credentials: "same-origin" })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(data.error || "Nahratie zlyhalo (kód " + r.status + ").");
          return data;
        });
      })
      .then(function (data) {
        var old = zone.querySelector(".pthumb");
        var img = document.createElement("img");
        img.className = "pthumb " + (zone.dataset.size || "");
        img.src = data.thumb; img.dataset.full = data.full; img.alt = "";
        old.replaceWith(img);
        zone.title = "Pretiahnite sem nový obrázok";
        toast("Obrázok bol uložený.");
      })
      .catch(function (err) { toast(err.message, "error"); })
      .finally(function () { zone.classList.remove("busy"); });
  }

  document.querySelectorAll(".imgdrop[data-upload]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("over"); });
    });
    zone.addEventListener("dragleave", function (e) { if (!zone.contains(e.relatedTarget)) zone.classList.remove("over"); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault(); e.stopPropagation(); zone.classList.remove("over");
      var f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) uploadTo(zone, f);
    });
    zone.addEventListener("click", function (e) {
      if (e.target.closest(".imgdrop-btn") || e.target.closest(".pthumb.ph")) {
        e.preventDefault(); e.stopPropagation(); input.click();
      }
    }, true);
    input.addEventListener("change", function () { if (input.files[0]) uploadTo(zone, input.files[0]); input.value = ""; });
  });

  // súbor pustený mimo miniatúry nesmie otvoriť obrázok namiesto aplikácie
  if (document.querySelector(".imgdrop")) {
    window.addEventListener("dragover", function (e) { if (!e.target.closest || !e.target.closest(".imgdrop, input[type=file]")) e.preventDefault(); });
    window.addEventListener("drop", function (e) { if (!e.target.closest || !e.target.closest(".imgdrop, input[type=file]")) e.preventDefault(); });
  }

  // Ctrl+V – vloženie obrázka zo schránky (na detaile projektu)
  document.addEventListener("paste", function (e) {
    var zone = document.querySelector(".imgdrop[data-paste]");
    if (!zone) return;
    var item = Array.prototype.find.call((e.clipboardData && e.clipboardData.items) || [], function (i) { return /^image\//.test(i.type); });
    if (!item) return;
    var f = item.getAsFile();
    e.preventDefault();
    uploadTo(zone, new File([f], "schranka." + (f.type.split("/")[1] || "png"), { type: f.type }));
  });

  // zväčšenie obrázka po kliknutí
  var lb = document.createElement("div");
  lb.className = "lightbox";
  lb.innerHTML = "<img alt=''>";
  document.body.appendChild(lb);
  lb.addEventListener("click", function () { lb.classList.remove("open"); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") lb.classList.remove("open"); });
  document.addEventListener("click", function (e) {
    var t = e.target.closest("img.pthumb[data-full]");
    if (!t || t.closest("a.dl-card")) return;     // na kartách deadlinov klik otvorí projekt
    e.preventDefault(); e.stopPropagation();
    lb.querySelector("img").src = t.dataset.full;
    lb.classList.add("open");
  }, true);
})();
