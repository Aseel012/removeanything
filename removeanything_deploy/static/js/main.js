/* removeanything.in — main.js
   Requires: base.html with window.RA already defined.
   Simple, flat, no IIFE. Every function is independently callable.
*/
'use strict';

/* ═══════════════════════════════════════════════
   FREE USAGE TRACKING
   ═══════════════════════════════════════════════ */
var FREE_LIMIT = 3;

function _setCookie(n, v, days) {
  var d = new Date();
  d.setTime(d.getTime() + days * 864e5);
  document.cookie = n + '=' + encodeURIComponent(v) + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
}
function _getCookie(n) {
  var m = document.cookie.match('(^|;)\\s*' + n + '\\s*=\\s*([^;]+)');
  return m ? decodeURIComponent(m[2]) : null;
}
function _getCount() {
  var c = parseInt(_getCookie('ra_count') || '0', 10);
  var s = parseInt(sessionStorage.getItem('ra_count') || '0', 10);
  return isNaN(c) ? (isNaN(s) ? 0 : s) : Math.max(c, isNaN(s) ? 0 : s);
}
function _bumpCount() {
  var n = _getCount() + 1;
  _setCookie('ra_count', n, 30);
  sessionStorage.setItem('ra_count', String(n));
  return n;
}

/* Wait up to ms for Firebase auth to resolve */
function _waitAuth(ms) {
  return new Promise(function(resolve) {
    if (window.RA && window.RA.authReady) { resolve(); return; }
    var t0 = Date.now();
    var iv = setInterval(function() {
      if ((window.RA && window.RA.authReady) || Date.now() - t0 >= ms) {
        clearInterval(iv); resolve();
      }
    }, 60);
  });
}

async function _checkLimit() {
  /* Wait max 3s for auth state */
  await _waitAuth(3000);
  /* Logged in = always pass */
  if (window.RA && window.RA.user) return true;
  /* Guest check */
  if (_getCount() >= FREE_LIMIT) {
    if (window.RA) window.RA.openAuthLimit();
    return false;
  }
  return true;
}

function _bumpServer() {
  fetch('/usage/bump', {method:'POST', credentials:'include'}).catch(function(){});
}

/* ═══════════════════════════════════════════════
   UTILITIES
   ═══════════════════════════════════════════════ */
window.fmtSize = function(b) {
  if (!b || b < 0) return '0 B';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return Math.round(b/1024) + ' KB';
  return (b/1048576).toFixed(1) + ' MB';
};

window.initTabSwitcher = function(tabsEl, viewIds) {
  if (!tabsEl) return;
  tabsEl.addEventListener('click', function(e) {
    var t = e.target.closest('.ws-tab'); if (!t) return;
    tabsEl.querySelectorAll('.ws-tab').forEach(function(x) { x.classList.remove('active'); });
    viewIds.forEach(function(id) { var el = document.getElementById(id); if (el) el.classList.remove('active'); });
    t.classList.add('active');
    var tgt = document.getElementById(t.dataset.view); if (tgt) tgt.classList.add('active');
  });
};

window.toggleFaq = function(btn) {
  var item = btn.closest('.faq-item');
  var open = item.classList.contains('open');
  document.querySelectorAll('.faq-item.open').forEach(function(x) { x.classList.remove('open'); });
  if (!open) item.classList.add('open');
};

/* Protect result images */
document.addEventListener('contextmenu', function(e) {
  if (e.target.tagName === 'IMG' && e.target.closest('.img-panel,.single-view')) e.preventDefault();
});
document.addEventListener('dragstart', function(e) {
  if (e.target.tagName === 'IMG') e.preventDefault();
});

/* ═══════════════════════════════════════════════
   TOOL CONTROLLER
   Single function that wires up all tool page UI.
   ═══════════════════════════════════════════════ */
window.createToolController = function(cfg) {
  var dzId       = cfg.dzId;
  var fileInputId= cfg.fileInputId;
  var errId      = cfg.errId;
  var wsId       = cfg.workspaceId;
  var mbId       = cfg.modebarId || null;
  var btnProcId  = cfg.btnProcId;
  var btnDlId    = cfg.btnDlId;
  var btnNewId   = cfg.btnNewId;
  var progWId    = cfg.progWrapId;
  var progBId    = cfg.progBarId;
  var statId     = cfg.statId;
  var overlayId  = cfg.overlayId;
  var origPId    = cfg.origPanelId;
  var resPId     = cfg.resPanelId;
  var origMId    = cfg.origMetaId    || null;
  var resMId     = cfg.resMetaId     || null;
  var resPHId    = cfg.resPHId       || null;
  var origFId    = cfg.singleOrigId  || null;
  var resFId     = cfg.singleResId   || null;
  var histLId    = cfg.histListId    || null;
  var histEId    = cfg.histEmptyId   || null;
  var endpoint   = cfg.endpoint;
  var dlName     = cfg.dlName        || '_clean.png';
  var allowed    = cfg.allowedTypes  || ['image/png','image/jpeg','image/webp'];
  var maxSize    = cfg.maxSize       || 20 * 1024 * 1024;
  var tabsId     = cfg.tabsId        || null;
  var viewIds    = cfg.viewIds       || [];
  var extraFD    = cfg.extraFormData  || null;

  /* Get DOM refs */
  var dz      = document.getElementById(dzId);
  var fi      = document.getElementById(fileInputId);
  var errBan  = document.getElementById(errId);
  var ws      = document.getElementById(wsId);
  var mb      = mbId   ? document.getElementById(mbId)   : null;
  var btnProc = document.getElementById(btnProcId);
  var btnDl   = document.getElementById(btnDlId);
  var btnNew  = document.getElementById(btnNewId);
  var progW   = document.getElementById(progWId);
  var progB   = document.getElementById(progBId);
  var stat    = document.getElementById(statId);
  var overlay = document.getElementById(overlayId);
  var origP   = document.getElementById(origPId);
  var resP    = document.getElementById(resPId);
  var origM   = origMId  ? document.getElementById(origMId)  : null;
  var resM    = resMId   ? document.getElementById(resMId)   : null;
  var resPH   = resPHId  ? document.getElementById(resPHId)  : null;
  var origF   = origFId  ? document.getElementById(origFId)  : null;
  var resF    = resFId   ? document.getElementById(resFId)   : null;
  var hlist   = histLId  ? document.getElementById(histLId)  : null;
  var hempty  = histEId  ? document.getElementById(histEId)  : null;

  var file = null, blob = null, blobUrl = null;
  var hist = [], activeIdx = -1;

  /* ── Error banner ── */
  function showErr(msg) { if (errBan) { errBan.textContent = msg; errBan.style.display = 'block'; } }
  function clearErr()   { if (errBan) errBan.style.display = 'none'; }

  /* ── Load file preview ── */
  function loadFile(f) {
    if (!f) return;
    if (allowed.indexOf(f.type) === -1) {
      showErr('Unsupported format. Use: ' + allowed.map(function(t){return t.split('/')[1].toUpperCase();}).join(', '));
      return;
    }
    if (f.size > maxSize) {
      showErr('File too large. Max ' + window.fmtSize(maxSize));
      return;
    }
    clearErr();
    file = f; blob = null; blobUrl = null;

    var reader = new FileReader();
    reader.onload = function(ev) {
      var src = ev.target.result;
      if (origP)   { origP.src = src; origP.classList.remove('hidden'); }
      if (origF)   origF.src = src;
      if (origM)   origM.textContent = f.name + ' \u00b7 ' + window.fmtSize(f.size);
      if (resP)    { resP.src = ''; resP.classList.add('hidden'); }
      if (resF)    { resF.src = ''; resF.classList.add('hidden'); }
      if (resPH)   resPH.classList.remove('hidden');
      if (resM)    resM.textContent = '';
      if (btnDl)   btnDl.classList.add('hidden');
      if (dz)      dz.style.display = 'none';
      if (mb)      mb.classList.remove('hidden');
      if (ws)      ws.style.display = 'block';
      if (btnProc) btnProc.disabled = false;
      /* Reset tabs to first */
      if (tabsId && viewIds.length) {
        var te = document.getElementById(tabsId);
        if (te) te.querySelectorAll('.ws-tab').forEach(function(t,i){ t.classList.toggle('active', i===0); });
        viewIds.forEach(function(id,i){ var el=document.getElementById(id); if(el) el.classList.toggle('active',i===0); });
      }
    };
    reader.readAsDataURL(f);
  }

  /* ── Drag & drop wiring ── */
  if (dz) {
    dz.addEventListener('click', function() { if (fi) fi.click(); });
    dz.addEventListener('dragenter', function(e) { e.preventDefault(); dz.classList.add('drag-over'); });
    dz.addEventListener('dragover',  function(e) { e.preventDefault(); });
    dz.addEventListener('dragleave', function(e) { if (!dz.contains(e.relatedTarget)) dz.classList.remove('drag-over'); });
    dz.addEventListener('drop', function(e) {
      e.preventDefault(); dz.classList.remove('drag-over');
      if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]);
    });
  }
  if (fi) {
    fi.addEventListener('change', function() { if (fi.files[0]) loadFile(fi.files[0]); });
  }

  /* ── Paste ── */
  document.addEventListener('paste', function(e) {
    if (ws && ws.style.display !== 'none') return;
    var items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image/') === 0) { loadFile(items[i].getAsFile()); break; }
    }
  });

  /* ── Process button ── */
  if (btnProc) {
    btnProc.addEventListener('click', async function() {
      if (!file) return;

      /* Check free limit — waits for Firebase auth */
      var ok = await _checkLimit();
      if (!ok) return;

      btnProc.disabled = true;
      if (btnDl)  btnDl.classList.add('hidden');
      blob = null; blobUrl = null;
      if (progW)  { progW.style.display = 'block'; }
      if (progB)  { progB.style.width = '0%'; }
      if (overlay){ overlay.classList.remove('hidden'); }
      if (resP)   { resP.classList.add('hidden'); }
      if (resPH)  { resPH.classList.add('hidden'); }
      if (stat)   { stat.style.display = 'flex'; }
      clearErr();

      /* Fake progress animation */
      var prog = 0;
      var pt = setInterval(function() {
        prog = Math.min(prog + Math.random() * 9 + 2, 82);
        if (progB) progB.style.width = prog + '%';
      }, 380);

      try {
        var fd = new FormData();
        fd.append('image', file);
        if (extraFD) extraFD(fd);

        var res = await fetch(endpoint, {method:'POST', body:fd, credentials:'include'});
        clearInterval(pt);

        if (!res.ok) {
          var msg = 'Processing failed. Please try again.';
          try { var d = await res.json(); msg = d.error || msg; } catch(e2) {}
          if (res.status === 429) msg = 'Too many requests \u2014 wait a moment and retry.';
          if (res.status === 413) msg = 'File too large.';
          throw new Error(msg);
        }

        blob    = await res.blob();
        blobUrl = URL.createObjectURL(blob);

        if (progB) progB.style.width = '100%';
        setTimeout(function() { if(progW) progW.style.display='none'; if(progB) progB.style.width='0%'; }, 500);

        if (overlay) overlay.classList.add('hidden');
        if (resP)  { resP.src = blobUrl; resP.classList.remove('hidden'); }
        if (resPH) resPH.classList.add('hidden');
        if (resF)  { resF.src = blobUrl; resF.classList.remove('hidden'); }
        if (resM)  resM.textContent = window.fmtSize(blob.size) + ' \u00b7 PNG';
        if (btnDl) btnDl.classList.remove('hidden');
        setTimeout(function() { if(stat) stat.style.display='none'; }, 1800);

        /* Count free uses for guests */
        if (!(window.RA && window.RA.user)) {
          var newCount = _bumpCount();
          var rem = FREE_LIMIT - newCount;
          if (rem > 0 && window.RA) {
            window.RA.toast(rem + ' free ' + (rem===1?'try':'tries') + ' left \u2014 sign up for unlimited', 'blue');
          }
        }
        _bumpServer();
        _addHistory();

      } catch(err) {
        clearInterval(pt);
        if (progW)  progW.style.display = 'none';
        if (overlay) overlay.classList.add('hidden');
        if (resPH) resPH.classList.remove('hidden');
        if (stat)  stat.style.display = 'none';
        showErr(err.message || 'An error occurred. Please try again.');
      }

      btnProc.disabled = false;
    });
  }

  /* ── Download ── */
  if (btnDl) {
    btnDl.addEventListener('click', function() {
      if (!blob) return;
      var url = URL.createObjectURL(blob);
      var a   = document.createElement('a');
      a.href  = url;
      a.download = (file ? file.name.replace(/\.[^.]+$/, '') : 'result') + dlName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function() { URL.revokeObjectURL(url); }, 10000);
    });
  }

  /* ── New image ── */
  if (btnNew) {
    btnNew.addEventListener('click', function() {
      if (ws)   ws.style.display  = 'none';
      if (mb)   mb.classList.add('hidden');
      if (dz)   dz.style.display  = 'block';
      if (fi)   fi.value          = '';
      if (btnDl) btnDl.classList.add('hidden');
      if (progW) progW.style.display = 'none';
      if (stat)  stat.style.display  = 'none';
      file = null; blob = null; blobUrl = null;
      clearErr();
    });
  }

  /* ── History ── */
  function _addHistory() {
    if (!hlist || !origP || !blobUrl) return;
    var tc  = document.createElement('canvas');
    tc.width = tc.height = 80;
    var ctx = tc.getContext('2d');
    var img = new Image();
    img.onload = function() {
      var s = Math.max(80/img.naturalWidth, 80/img.naturalHeight);
      ctx.drawImage(img, (80-img.naturalWidth*s)/2, (80-img.naturalHeight*s)/2, img.naturalWidth*s, img.naturalHeight*s);
      hist.unshift({
        name: file.name, w: origP.naturalWidth||0, h: origP.naturalHeight||0,
        thumb: tc.toDataURL('image/jpeg',.7), resUrl: blobUrl, resBlob: blob,
        origSrc: origP.src, time: new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})
      });
      activeIdx = 0;
      _renderHistory();
    };
    img.src = blobUrl;
  }

  function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function _renderHistory() {
    if (!hlist) return;
    hlist.querySelectorAll('.hcard').forEach(function(x){ x.remove(); });
    if (!hist.length) { if(hempty) hempty.style.display='block'; return; }
    if (hempty) hempty.style.display = 'none';
    hist.forEach(function(e, i) {
      var card = document.createElement('div');
      card.className = 'hcard' + (i===activeIdx?' active':'');
      card.innerHTML =
        '<img class="hcard-thumb" src="'+e.thumb+'" alt="">' +
        '<div class="hcard-info">' +
          '<div class="hcard-name">'+_esc(e.name)+'</div>' +
          '<div class="hcard-meta">'+e.w+'\u00d7'+e.h+' \u00b7 '+e.time+'</div>' +
        '</div>' +
        '<button class="hcard-del" aria-label="Remove">\u00d7</button>';
      card.querySelector('.hcard-del').addEventListener('click', function(ev) {
        ev.stopPropagation(); hist.splice(i,1);
        if(activeIdx>=hist.length) activeIdx=hist.length-1;
        _renderHistory();
      });
      card.addEventListener('click', function() {
        activeIdx = i;
        if(origP) { origP.src=e.origSrc; origP.classList.remove('hidden'); }
        if(origF) origF.src = e.origSrc;
        if(resP)  { resP.src=e.resUrl; resP.classList.remove('hidden'); }
        if(resF)  { resF.src=e.resUrl; resF.classList.remove('hidden'); }
        if(resPH) resPH.classList.add('hidden');
        blob=e.resBlob; blobUrl=e.resUrl; file={name:e.name};
        if(btnDl) btnDl.classList.remove('hidden');
        if(dz) dz.style.display='none';
        if(mb) mb.classList.remove('hidden');
        if(ws) ws.style.display='block';
        if(tabsId&&viewIds.length) {
          var te=document.getElementById(tabsId);
          if(te) te.querySelectorAll('.ws-tab').forEach(function(t,j){t.classList.toggle('active',j===0);});
          viewIds.forEach(function(id,j){var el=document.getElementById(id);if(el)el.classList.toggle('active',j===0);});
        }
        _renderHistory();
      });
      hlist.appendChild(card);
    });
  }

  /* ── Init tab switcher ── */
  if (tabsId && viewIds.length) {
    var te = document.getElementById(tabsId);
    if (te) window.initTabSwitcher(te, viewIds);
  }
};
