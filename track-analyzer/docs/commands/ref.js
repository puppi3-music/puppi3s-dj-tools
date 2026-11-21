// Command reference pages: copy buttons, option filters, current-section highlight, light/dark toggle.
// Shared by every page in docs/commands/ (loaded at the end of <body>).
(function () {
  function fallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove();
  }
  document.querySelectorAll('.cmd').forEach(function (box) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Copy';
    btn.addEventListener('click', function () {
      var text = box.querySelector('pre').innerText;
      var done = function () { btn.textContent = 'Copied'; btn.classList.add('done');
        setTimeout(function () { btn.textContent = 'Copy'; btn.classList.remove('done'); }, 1400); };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
      } else { fallback(text); done(); }
    });
    box.appendChild(btn);
  });

  // <input class="filter" data-table="id">: hide rows that don't contain the typed text
  document.querySelectorAll('input.filter[data-table]').forEach(function (input) {
    var rows = document.querySelectorAll('#' + input.dataset.table + ' tbody tr');
    input.addEventListener('input', function () {
      var q = input.value.toLowerCase().trim();
      rows.forEach(function (r) { r.hidden = !!q && r.textContent.toLowerCase().indexOf(q) < 0; });
    });
  });

  // Highlight the section in view among this page's own links (#…)
  var links = Array.prototype.slice.call(document.querySelectorAll('nav.toc a[href^="#"]'));
  var targets = links.map(function (a) { return document.querySelector(a.getAttribute('href')); });
  function onScroll() {
    var current = -1;
    targets.forEach(function (t, i) { if (t && t.getBoundingClientRect().top < 120) current = i; });
    links.forEach(function (a, i) { a.classList.toggle('active', i === current); });
  }
  if (links.length) { document.addEventListener('scroll', onScroll, { passive: true }); onScroll(); }

  // Options table pre-filtered from a search result (page.html?opt=--flag#all-options)
  var opt = new URLSearchParams(location.search).get('opt');
  var optFilter = document.querySelector('input.filter[data-table="opt-table"]');
  if (opt && optFilter) { optFilter.value = opt; optFilter.dispatchEvent(new Event('input')); }

  // Search box at the top of the contents; search-index.js then search.js (kept in order)
  var brand = document.querySelector('nav.toc .brand');
  if (brand) {
    var box = document.createElement('div');
    box.setAttribute('data-ref-search', '');
    brand.insertAdjacentElement('afterend', box);
    ['search-index.js', 'search.js'].forEach(function (src) {
      var sc = document.createElement('script'); sc.src = src; sc.async = false; document.body.appendChild(sc);
    });
  }

  // Light/dark toggle, remembered per browser (same key as the guide)
  var root = document.documentElement;
  try { var saved = localStorage.getItem('guide-theme'); if (saved) root.setAttribute('data-theme', saved); } catch (e) {}
  var t = document.getElementById('theme');
  if (t) t.addEventListener('click', function () {
    var dark = root.getAttribute('data-theme') ? root.getAttribute('data-theme') === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    var next = dark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('guide-theme', next); } catch (e) {}
  });
})();
