/* ==========================================================================
   Groww Weekly Review Pulse — app.js
   Vanilla JS + d3 v7. No framework required.

   Data flow:
     window.PULSE_DATA (data/pulse.js)  ──►  store  ──►  derived values (store.calc)
                                                   └──►  draw functions (store.onData)
   Filters live in store.params (theme, sentiment, kw, q). Every setParams()
   re-runs all draw functions. To use a real API, replace loadData() below.
   ========================================================================== */

const store = (() => {
  const DEFAULT_PARAMS = { theme: null, sentiment: 'All', kw: null, q: null };
  const sources = {};      // id -> rows
  const calcs = [];        // [{id, inputs, fn}]
  const derived = {};      // id -> result
  const listeners = [];
  let params = { ...DEFAULT_PARAMS };
  let status = 'loading';

  const recompute = () => {
    for (const c of calcs) derived[c.id] = c.fn(...c.inputs.map((i) => (i in sources ? sources[i] : derived[i])));
  };
  const emit = () => listeners.forEach((fn) => fn());

  // Series colours come from CSS so they follow light/dark mode.
  const colors = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);

  return {
    colors,
    /** Load raw datasets: { themes: [...], quotes: [...], actions: [...] } */
    load(data) { Object.assign(sources, data); status = 'ok'; recompute(); emit(); },
    fail() { status = 'error'; emit(); },
    /** Register a derived dataset computed from other datasets. */
    calc(id, { inputs, fn }) { calcs.push({ id, inputs, fn }); if (status === 'ok') { recompute(); } },
    /** Read a dataset or derived value: { status: 'loading'|'ok'|'error', data } */
    data(id) {
      if (status !== 'ok') return { status, data: [] };
      const d = id in sources ? sources[id] : derived[id];
      return { status: 'ok', data: d ?? [] };
    },
    params: () => ({ ...params }),
    setParams(p) { params = { ...params, ...p }; emit(); },
    resetParams() { params = { ...DEFAULT_PARAMS }; emit(); },
    /** Run fn now and on every data, filter or resize change. */
    onData(fn) { listeners.push(fn); fn(); },
  };
})();

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => store.setParams({}), 120);
});

function updateHeader(data) {
  if (data && data.week) {
    try {
      const parts = String(data.week).split('-');
      if (parts.length === 3) {
        const d = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
        const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
        const eyebrow = document.querySelector('.gx-eyebrow');
        if (eyebrow) {
          eyebrow.textContent = `Groww · Play Store review pulse · Week of ${dateStr}`;
        }
      }
    } catch (_) {}
  }
}

/** Load data from API / data/pulse.json when on HTTP, or fall back to window.PULSE_DATA */
async function loadData() {
  let loaded = false;
  if (window.location.protocol.startsWith('http')) {
    try {
      const res = await fetch('data/pulse.json');
      if (res.ok) {
        const json = await res.json();
        updateHeader(json);
        store.load(json);
        loaded = true;
      }
    } catch (_) {
      // Fall back to window.PULSE_DATA
    }
  }
  if (!loaded) {
    if (window.PULSE_DATA) {
      updateHeader(window.PULSE_DATA);
      store.load(window.PULSE_DATA);
    } else {
      store.fail();
    }
  }
}

/**
 * Binding helper: records which dataset/field an element displays as
 * data-source / data-field / data-where attributes. Purely descriptive —
 * useful for QA and automated tests (e.g. query [data-field="reviews"]).
 */
(() => {
  store.calc('summary', {
    inputs: ['themes'],
    title: 'Top-theme totals',
    description: 'Reviews across the top three themes, the leading theme, its share, and its lead over the runner-up.',
    fn: (themes) => {
      const rows = themes.slice().sort((a, b) => Number(b.reviews) - Number(a.reviews));
      const total = rows.reduce((s, r) => s + Number(r.reviews), 0);
      const top = rows[0];
      const second = rows[1] || { reviews: 0 };
      return [{ total, top_theme: top.theme, top_reviews: Number(top.reviews), top_share: Number(top.reviews) / total, lead_margin: Number(top.reviews) - Number(second.reviews) }];
    },
  });

  store.calc('shares', {
    inputs: ['themes'],
    title: 'Theme shares',
    description: "Each theme's reviews as a share of the top-3 total.",
    fn: (themes) => {
      const total = themes.reduce((s, r) => s + Number(r.reviews), 0);
      return themes.map((t) => ({ theme: t.theme, rank: Number(t.rank), reviews: Number(t.reviews), share: Number(t.reviews) / total }));
    },
  });

  store.calc('coverage', {
    inputs: ['themes', 'quotes', 'actions'],
    title: 'Quotes and actions per theme',
    description: 'How many featured quotes and recommended actions are tagged to each top theme.',
    fn: (themes, quotes, actions) => themes.map((t) => ({
      theme: t.theme,
      rank: Number(t.rank),
      quotes: quotes.filter((q) => q.theme === t.theme).length,
      actions: actions.filter((a) => a.theme === t.theme).length,
    })),
  });

  store.calc('sentiment', {
    inputs: ['quotes'],
    title: 'Quotes by sentiment',
    description: 'Count and share of featured quotes per sentiment tag.',
    fn: (quotes) => ['Negative', 'Positive'].map((s) => {
      const n = quotes.filter((q) => q.sentiment === s).length;
      return { sentiment: s, n, share: quotes.length ? n / quotes.length : 0 };
    }),
  });

  store.calc('keywords', {
    inputs: ['quotes'],
    title: 'Keywords in quotes',
    description: 'How many featured quotes mention each term in a fixed list of product terms Claude chose.',
    fn: (quotes) => {
      const terms = [['withdraw', 'withdrawals'], ['fee', 'fees'], ['charge', 'charges'], ['support', 'support'], ['service', 'service'], ['update', 'updates'], ['interface', 'interface'], ['experience', 'experience'], ['p/l', 'P/L'], ['return', 'returns'], ['fund', 'funds'], ['money', 'money'], ['smooth', 'smooth'], ['useful', 'useful']];
      return terms
        .map(([stem, word]) => ({ word, stem, n: quotes.filter((q) => q.quote.toLowerCase().includes(stem)).length }))
        .filter((r) => r.n > 0)
        .sort((a, b) => b.n - a.n || a.word.localeCompare(b.word));
    },
  });

  const fmt = d3.format(',');
  const pct = d3.format('.0%');
  const enc = encodeURIComponent;
  const $ = (s) => document.querySelector(s);
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const mark = (e, source, field, where) => {  // see "Binding helper" note above
    e.dataset.source = source;
    e.dataset.field = field;
    if (where) e.dataset.where = where;
    return e;
  };
  const fail = (box) => {
    box.classList.remove('is-loading');
    box.replaceChildren(el('div', 'gx-fail', 'Failed to load'));
  };
  const P = () => {
    const p = store.params();
    return { theme: p.theme || null, sentiment: p.sentiment || 'All', kw: p.kw || null, q: p.q || '' };
  };
  const toggle = (name, value) => store.setParams({ [name]: P()[name] === value ? null : value });

  let rowsT = [];
  let hoverTheme = null;
  let introDone = false;
  const colorOf = (theme) => {
    const t = rowsT.find((r) => r.theme === theme);
    return t ? store.colors[(Number(t.rank) - 1) % store.colors.length] : 'var(--cds-chart-muted)';
  };

  // Hero colours
  const hero = $('#overview');
  const c = store.colors;
  hero.style.background = `linear-gradient(125deg, color-mix(in srgb, ${c[0]} 22%, var(--color-panel)) 0%, color-mix(in srgb, ${c[1]} 16%, var(--color-panel)) 50%, color-mix(in srgb, ${c[2]} 22%, var(--color-panel)) 100%)`;
  document.querySelectorAll('.gx-kpi').forEach((k) => { k.style.borderTopColor = c[Number(k.dataset.accent) % c.length]; });

  // Controls
  const search = $('#q');
  search.addEventListener('input', () => {
    const v = search.value.trim();
    store.setParams(v ? { q: v, kw: null } : { q: null });
  });
  search.addEventListener('keydown', (e) => { if (e.key === 'Enter') setMenu(null); });
  $('#clear-all').addEventListener('click', () => { store.resetParams(); search.value = ''; });
  $('#voice-reset').addEventListener('click', () => { store.setParams({ theme: null, sentiment: 'All', kw: null, q: null }); search.value = ''; });

  // Sentence menus
  let openMenu = null;
  const voice = $('#voice');
  function setMenu(m) {
    openMenu = m;
    voice.querySelectorAll('.gv-slot').forEach((slot) => {
      const pop = slot.querySelector('.gv-pop');
      const on = slot.dataset.menu === m;
      pop.hidden = !on;
      slot.querySelector('.gv-pill').setAttribute('aria-expanded', String(on));
      if (on) {
        pop.style.left = '0px';
        const r = pop.getBoundingClientRect();
        const box = voice.getBoundingClientRect();
        if (r.right > box.right - 8) pop.style.left = Math.max(box.left + 8 - r.left, box.right - 8 - r.right) + 'px';
      }
    });
    if (m === 'term') setTimeout(() => search.focus(), 0);
    else if (m) setTimeout(() => { const f = voice.querySelector(`.gv-slot[data-menu="${m}"] .gv-opt`); if (f) f.focus(); }, 0);
  }
  voice.querySelectorAll('.gv-pill').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const m = b.closest('.gv-slot').dataset.menu;
    setMenu(openMenu === m ? null : m);
  }));
  document.addEventListener('click', (e) => { if (openMenu && !e.target.closest('.gv-slot')) setMenu(null); });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !openMenu) return;
    const pill = voice.querySelector(`.gv-slot[data-menu="${openMenu}"] .gv-pill`);
    setMenu(null);
    if (pill) pill.focus();
  });
  const pick = (params) => { store.setParams(params); setMenu(null); };
  const opt = (label, checked, onPick, extra) => {
    const b = el('button', 'gv-opt');
    b.type = 'button';
    b.setAttribute('role', 'menuitemradio');
    b.setAttribute('aria-checked', String(checked));
    if (extra && extra.dot) b.append(extra.dot);
    b.append(el('span', null, label));
    if (extra && extra.n) b.append(extra.n);
    b.addEventListener('click', (e) => { e.stopPropagation(); onPick(); });
    return b;
  };

  // Focus highlighting (hover or selected theme) without a full redraw
  function applyFocus() {
    const f = hoverTheme || P().theme;
    document.querySelectorAll('#pulse [data-theme-key]').forEach((n) => {
      n.classList.toggle('gx-dim', !!f && n.dataset.themeKey !== f);
    });
  }
  const hover = (theme) => { hoverTheme = theme; applyFocus(); };

  // Tooltip
  const tip = el('div', 'gx-tip');
  tip.hidden = true;
  const showTip = (box, e, head, line) => {
    tip.replaceChildren(el('div', 'gx-tip-h', head), el('div', null, line));
    if (tip.parentNode !== box) box.append(tip);
    tip.hidden = false;
    const r = box.getBoundingClientRect();
    tip.style.left = Math.max(0, Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth)) + 'px';
    tip.style.top = (e.clientY - r.top + 12) + 'px';
  };

  // Count-up for headline numbers, once
  function setNum(node, v) {
    const f = node.dataset.num === 'pct' ? pct : fmt;
    if (!reduce && !node.dataset.counted) {
      node.dataset.counted = '1';
      const i = d3.interpolateNumber(0, v);
      d3.select(node).transition().duration(900).ease(d3.easeCubicOut)
        .tween('text', () => (t) => { node.textContent = f(node.dataset.num === 'pct' ? i(t) : Math.round(i(t))); });
    } else {
      d3.select(node).interrupt();
      node.textContent = f(v);
    }
  }

  function drawKpis() {
    const S = store.data('summary');
    const SE = store.data('sentiment');
    document.querySelectorAll('[data-num]').forEach((node) => {
      const src = node.dataset.source === 'summary' ? S : SE;
      if (src.status === 'ok' || src.status === 'error') node.classList.remove('is-loading');
      let row = null;
      if (src.status === 'ok') row = node.dataset.source === 'summary' ? src.data[0] : src.data.find((d) => d.sentiment === 'Negative');
      if (!row) { node.textContent = '—'; return; }
      setNum(node, row[node.dataset.field]);
    });
  }

  function drawTake() {
    const S = store.data('summary');
    const take = $('#takeaway');
    if (S.status === 'ok' && rowsT.length) {
      take.classList.remove('is-loading');
      const s = S.data[0];
      const b = (field, text) => mark(el('b', null, text), 'summary', field, null);
      const tied = s.lead_margin <= 2;
      const nums = rowsT.slice().sort((a, b2) => a.rank - b2.rank);
      const parts = ['', b('top_theme', s.top_theme), tied ? ' leads by just ' : ' leads by ', b('lead_margin', fmt(s.lead_margin)), s.lead_margin === 1 ? ' review' : ' reviews'];
      parts.forEach((p) => { if (p && p.dataset) p.dataset.row = '0'; });
      if (tied) {
        parts.push(', so the top three themes are a near three-way tie at ');
        nums.forEach((t, i) => {
          parts.push(mark(el('b', null, fmt(t.reviews)), 'themes', 'reviews', 'theme=' + enc(t.theme)));
          parts.push(i === nums.length - 2 ? ' and ' : i < nums.length - 1 ? ', ' : '.');
        });
        const runnerUps = nums.slice(1).map((t) => t.theme);
        if (runnerUps.length) {
          parts.push(` ${runnerUps.join(' and ')} deserve as much attention as ${s.top_theme} this week.`);
        }
      } else {
        parts.push('.');
      }
      take.replaceChildren(...parts.filter((p) => p !== ''));
    } else if (S.status === 'error') {
      take.classList.remove('is-loading');
      take.textContent = '';
    }
  }

  function drawActive() {
    const p = P();
    const pills = [];
    const add = (label, name) => {
      const b = el('button', 'gx-pill');
      b.type = 'button';
      b.append(el('span', null, label), el('span', null, '×'));
      b.setAttribute('aria-label', 'Remove filter ' + label);
      b.addEventListener('click', () => { store.setParams({ [name]: name === 'sentiment' ? 'All' : null }); if (name === 'q') search.value = ''; });
      pills.push(b);
    };
    if (p.theme) add(p.theme, 'theme');
    if (p.sentiment !== 'All') add(p.sentiment, 'sentiment');
    if (p.kw) add('Keyword: ' + p.kw, 'kw');
    if (p.q) add('“' + p.q + '”', 'q');
    $('#active-pills').replaceChildren(...pills);
    $('#active').hidden = pills.length === 0;
  }

  function drawDonut() {
    const SH = store.data('shares');
    const box = $('#donut');
    if (SH.status === 'error') { fail(box); return; }
    if (SH.status !== 'ok') { box.replaceChildren(); return; }
    box.classList.remove('is-loading');
    const p = P();
    const rows = SH.data.slice().sort((a, b) => a.rank - b.rank);
    const w = box.clientWidth || 240;
    const size = Math.max(170, Math.min(230, w));
    const total = d3.sum(rows, (d) => d.reviews);
    const pie = d3.pie().value((d) => d.reviews).sort(null).padAngle(0.03);
    const arc = d3.arc().innerRadius(size * 0.3).outerRadius(size / 2 - 8).cornerRadius(6);
    const arcBig = d3.arc().innerRadius(size * 0.3).outerRadius(size / 2).cornerRadius(6);
    const svg = d3.create('svg').attr('viewBox', [-size / 2, -size / 2, size, size]).attr('width', '100%')
      .style('max-width', size + 'px').style('display', 'block').style('margin', '0 auto').attr('role', 'img')
      .attr('aria-label', 'Theme share donut');
    svg.selectAll('path').data(pie(rows)).join('path')
      .attr('d', (d) => (p.theme === d.data.theme ? arcBig(d) : arc(d)))
      .style('fill', (d) => colorOf(d.data.theme))
      .attr('data-theme-key', (d) => d.data.theme)
      .style('animation-delay', (d, i) => (i * 90) + 'ms')
      .on('pointermove', (e, d) => { hover(d.data.theme); showTip(box, e, d.data.theme, fmt(d.data.reviews) + ' reviews · ' + pct(d.data.share)); })
      .on('pointerleave', () => { hover(null); tip.hidden = true; })
      .on('click', (e, d) => toggle('theme', d.data.theme));
    const sel = rows.find((r) => r.theme === p.theme);
    const g = svg.append('g').attr('text-anchor', 'middle').style('pointer-events', 'none');
    g.append('text').attr('y', 4).style('font-family', 'var(--font-serif)').style('font-size', size * 0.17 + 'px')
      .style('fill', 'var(--color-fg)').text(fmt(sel ? sel.reviews : total));
    g.append('text').attr('y', size * 0.11).style('font-size', '11px').style('fill', 'var(--color-fg-muted)')
      .text(sel ? pct(sel.share) + ' of reviews' : 'reviews');
    box.replaceChildren(svg.node());
  }

  function drawLegend() {
    const SH = store.data('shares');
    const CV = store.data('coverage');
    const box = $('#legend');
    if (SH.status !== 'ok') { box.replaceChildren(); return; }
    const p = P();
    const rows = SH.data.slice().sort((a, b) => a.rank - b.rank);
    const maxShare = d3.max(rows, (d) => d.share) || 1;
    box.replaceChildren(...rows.map((d) => {
      const w = 'theme=' + enc(d.theme);
      const b = el('button', 'gx-leg');
      b.type = 'button';
      b.dataset.themeKey = d.theme;
      b.setAttribute('aria-pressed', String(p.theme === d.theme));
      const dot = el('span', 'gx-dot');
      dot.style.background = colorOf(d.theme);
      const name = el('span', 'gx-leg-name', d.theme);
      const num = el('span', 'gx-leg-num');
      num.append(mark(el('b', null, fmt(d.reviews)), 'shares', 'reviews', w), ' · ', mark(el('span', null, pct(d.share)), 'shares', 'share', w));
      const bar = el('span', 'gx-leg-bar');
      const fill = el('span');
      fill.style.width = (d.share / maxShare * 100) + '%';
      fill.style.background = colorOf(d.theme);
      bar.append(fill);
      const meta = el('span', 'gx-leg-meta');
      const cv = CV.status === 'ok' ? CV.data.find((r) => r.theme === d.theme) : null;
      if (cv) {
        meta.append(mark(el('span', null, fmt(cv.quotes)), 'coverage', 'quotes', w), cv.quotes === 1 ? ' quote · ' : ' quotes · ',
          mark(el('span', null, fmt(cv.actions)), 'coverage', 'actions', w), cv.actions === 1 ? ' action' : ' actions');
      }
      b.append(dot, name, num, bar, meta);
      b.addEventListener('pointerenter', () => hover(d.theme));
      b.addEventListener('pointerleave', () => hover(null));
      b.addEventListener('click', () => toggle('theme', d.theme));
      return b;
    }));
  }

  function drawMap() {
    const T = store.data('themes');
    const A = store.data('actions');
    const map = $('#map');
    const L = $('#map-l');
    const R = $('#map-r');
    const svg = $('#map-svg');
    if (T.status === 'error' || A.status === 'error') { map.classList.remove('is-loading'); L.replaceChildren(el('div', 'gx-fail', 'Failed to load')); R.replaceChildren(); svg.replaceChildren(); return; }
    if (T.status !== 'ok' || A.status !== 'ok') { L.replaceChildren(); R.replaceChildren(); svg.replaceChildren(); return; }
    map.classList.remove('is-loading');
    const p = P();
    L.replaceChildren(...rowsT.map((t) => {
      const n = el('button', 'gx-node');
      n.type = 'button';
      n.dataset.themeKey = t.theme;
      n.style.borderLeftColor = colorOf(t.theme);
      n.setAttribute('aria-pressed', String(p.theme === t.theme));
      n.append(el('span', 'gx-node-name', t.theme), el('span', 'gx-node-n', fmt(t.reviews) + ' reviews'));
      n.addEventListener('pointerenter', () => hover(t.theme));
      n.addEventListener('pointerleave', () => hover(null));
      n.addEventListener('focus', () => hover(t.theme));
      n.addEventListener('blur', () => hover(null));
      n.addEventListener('click', () => toggle('theme', t.theme));
      return n;
    }));
    const acts = A.data.slice().sort((a, b) => a.rank - b.rank);
    R.replaceChildren(...acts.map((a) => {
      const li = el('li', 'gx-act');
      li.dataset.themeKey = a.theme;
      li.id = 'action-' + a.id;
      const num = el('span', 'gx-act-num', String(a.rank));
      num.style.background = colorOf(a.theme);
      const body = el('div', 'gx-act-text');
      body.append(mark(el('span', null, a.action), 'actions', 'action', 'id=' + enc(a.id)));
      const inTop = rowsT.some((t) => t.theme === a.theme);
      body.append(el('span', 'gx-act-tag', inTop ? '→ ' + a.theme : a.theme + ' · not a top-3 theme'));
      li.append(num, body);
      return li;
    }));
    requestAnimationFrame(() => {
      const sr = svg.getBoundingClientRect();
      if (!sr.width) return;
      svg.setAttribute('viewBox', `0 0 ${sr.width} ${sr.height}`);
      const paths = [];
      acts.forEach((a) => {
        const node = [...L.children].find((n) => n.dataset.themeKey === a.theme);
        const target = document.getElementById('action-' + a.id);
        if (!node || !target) return;
        const nr = node.getBoundingClientRect();
        const tr = target.getBoundingClientRect();
        const y1 = nr.top + nr.height / 2 - sr.top;
        const y2 = tr.top + tr.height / 2 - sr.top;
        const x2 = sr.width;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M0,${y1} C${x2 * 0.55},${y1} ${x2 * 0.45},${y2} ${x2},${y2}`);
        path.style.stroke = colorOf(a.theme);
        path.dataset.themeKey = a.theme;
        paths.push(path);
      });
      svg.replaceChildren(...paths);
      applyFocus();
    });
  }

  function drawGaps() {
    const CV = store.data('coverage');
    const box = $('#gaps');
    if (CV.status !== 'ok') { box.replaceChildren(); return; }
    const items = [];
    CV.data.slice().sort((a, b) => a.rank - b.rank).forEach((r) => {
      const w = 'theme=' + enc(r.theme);
      if (r.actions === 0) {
        const li = el('li');
        li.append('No recommended action targets ', mark(el('b', null, r.theme), 'coverage', 'theme', w), r.rank === 1 ? ', even though it is the #1 theme.' : '.');
        items.push(li);
      }
      if (r.quotes === 0) {
        const li = el('li');
        li.append(mark(el('b', null, r.theme), 'coverage', 'theme', w), ' has no featured user quote this week.');
        items.push(li);
      }
    });
    if (!items.length) items.push(el('li', null, 'Every top theme has at least one quote and one action.'));
    box.replaceChildren(...items);
  }

  function highlight(node, text, terms) {
    const ts = terms.filter(Boolean).map((t) => t.toLowerCase());
    if (!ts.length) { node.textContent = text; return; }
    const re = new RegExp('(' + ts.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
    const parts = text.split(re);
    node.replaceChildren(...parts.filter((s) => s !== '').map((s) => (ts.includes(s.toLowerCase()) ? el('mark', 'gx-hl', s) : document.createTextNode(s))));
  }

  function drawVoice() {
    const p = P();
    const Q = store.data('quotes');
    const CV = store.data('coverage');
    const KW = store.data('keywords');
    const SE = store.data('sentiment');
    if (search !== document.activeElement) search.value = p.q;
    const kws = KW.status === 'ok' ? KW.data : [];
    const sents = SE.status === 'ok' ? SE.data : [];

    // Mood bar: the sentiment split, clickable
    const mood = $('#mood');
    mood.replaceChildren(...['Negative', 'Positive'].map((s) => {
      const row = sents.find((d) => d.sentiment === s);
      const b = el('button', s === 'Negative' ? 'neg' : 'pos');
      b.type = 'button';
      b.style.flexGrow = row ? String(Math.max(row.n, 0.4)) : '1';
      b.setAttribute('aria-pressed', String(p.sentiment === s));
      b.classList.toggle('gx-off', p.sentiment !== 'All' && p.sentiment !== s);
      b.append(el('span', null, s === 'Negative' ? '▼ ' + s : '▲ ' + s));
      if (row) b.append(mark(el('span', null, fmt(row.n)), 'sentiment', 'n', 'sentiment=' + s));
      b.addEventListener('click', () => store.setParams({ sentiment: p.sentiment === s ? 'All' : s }));
      return b;
    }));

    // Slot 1: sentiment
    const sentSlot = voice.querySelector('.gv-slot[data-menu="sent"]');
    const sentPill = sentSlot.querySelector('.gv-pill');
    sentPill.querySelector('.gv-pill-label').textContent = p.sentiment === 'All' ? 'all' : p.sentiment.toLowerCase();
    sentPill.classList.toggle('gv-set', p.sentiment !== 'All');
    sentPill.style.setProperty('--gv-c', p.sentiment === 'Negative' ? 'var(--color-bad)' : p.sentiment === 'Positive' ? 'var(--color-ok)' : 'var(--color-fg)');
    $('#opts-sent').replaceChildren(
      opt('All', p.sentiment === 'All', () => pick({ sentiment: 'All' })),
      ...['Positive', 'Negative'].map((s) => {
        const row = sents.find((d) => d.sentiment === s);
        const n = row ? mark(el('span', 'gv-opt-n', fmt(row.n)), 'sentiment', 'n', 'sentiment=' + s) : null;
        return opt(s, p.sentiment === s, () => pick({ sentiment: s }), { n });
      }));

    // Slot 2: theme
    const themeSlot = voice.querySelector('.gv-slot[data-menu="theme"]');
    const themePill = themeSlot.querySelector('.gv-pill');
    themePill.querySelector('.gv-pill-label').textContent = p.theme || 'any theme';
    themePill.classList.toggle('gv-set', !!p.theme);
    themePill.style.setProperty('--gv-c', p.theme ? colorOf(p.theme) : 'var(--color-fg)');
    const pillDot = themePill.querySelector('.gv-dot-slot');
    pillDot.replaceChildren();
    if (p.theme) { const d = el('span', 'gx-dot'); d.style.background = colorOf(p.theme); pillDot.append(d); }
    $('#opts-theme').replaceChildren(
      opt('Any theme', !p.theme, () => pick({ theme: null })),
      ...rowsT.map((t) => {
        const dot = el('span', 'gx-dot');
        dot.style.background = colorOf(t.theme);
        const cv = CV.status === 'ok' ? CV.data.find((r) => r.theme === t.theme) : null;
        const n = cv ? mark(el('span', 'gv-opt-n', fmt(cv.quotes)), 'coverage', 'quotes', 'theme=' + enc(t.theme)) : null;
        return opt(t.theme, p.theme === t.theme, () => pick({ theme: t.theme }), { dot, n });
      }));

    // Slot 3: search or keyword
    const termPill = voice.querySelector('.gv-slot[data-menu="term"] .gv-pill');
    termPill.querySelector('.gv-pill-label').textContent = p.kw ? p.kw : p.q ? '“' + p.q + '”' : 'anything';
    termPill.classList.toggle('gv-set', !!(p.kw || p.q));
    termPill.style.setProperty('--gv-c', 'var(--color-warn)');
    $('#opts-kw').replaceChildren(...kws.map((k) => {
      const b = el('button', 'gx-chip');
      b.type = 'button';
      b.setAttribute('aria-pressed', String(p.kw === k.word));
      b.append(k.word, mark(el('span', 'gx-cnt', fmt(k.n)), 'keywords', 'n', 'word=' + enc(k.word)));
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        search.value = '';
        pick(p.kw === k.word ? { kw: null } : { kw: k.word, q: null });
      });
      return b;
    }));

    $('#voice-reset').hidden = !(p.theme || p.sentiment !== 'All' || p.kw || p.q);

    // Quotes
    const box = $('#quotes');
    if (Q.status === 'error') { fail(box); $('#showing').textContent = ''; return; }
    if (Q.status !== 'ok') { box.replaceChildren(); return; }
    box.classList.remove('is-loading');
    const kwRow = kws.find((k) => k.word === p.kw);
    const stem = kwRow ? kwRow.stem : null;
    const ql = p.q.toLowerCase();
    const rows = Q.data.filter((q) => {
      const t = q.quote.toLowerCase();
      return (!p.theme || q.theme === p.theme) && (p.sentiment === 'All' || q.sentiment === p.sentiment)
        && (!stem || t.includes(stem)) && (!ql || t.includes(ql));
    });
    $('#showing').textContent = rows.length + ' of ' + Q.data.length + ' quotes';
    if (!rows.length) {
      const empty = el('div', 'gx-empty');
      const btn = el('button', 'gx-link', 'Reset the sentence');
      btn.type = 'button';
      btn.addEventListener('click', () => { store.setParams({ theme: null, sentiment: 'All', kw: null, q: null }); search.value = ''; });
      empty.append('No quotes match these filters. ', btn);
      box.replaceChildren(empty);
      return;
    }
    box.replaceChildren(...rows.map((q, i) => {
      const fig = el('figure', 'gx-quote');
      fig.style.setProperty('--qc', colorOf(q.theme));
      fig.style.animationDelay = (i * 70) + 'ms';
      const qmark = el('span', 'gx-qmark', '“');
      qmark.setAttribute('aria-hidden', 'true');
      const txt = mark(el('blockquote', 'gx-quote-text'), 'quotes', 'quote', 'id=' + enc(q.id));
      highlight(txt, q.quote, [p.q, stem]);
      const more = el('button', 'gx-more', 'Read more');
      more.type = 'button';
      more.hidden = true;
      more.addEventListener('click', () => {
        const open = fig.classList.toggle('gx-open');
        more.textContent = open ? 'Show less' : 'Read more';
      });
      if (p.q || stem) fig.classList.add('gx-open');
      const foot = el('div', 'gx-quote-foot');
      const th = el('span', 'gx-quote-theme');
      const dot = el('span', 'gx-dot');
      dot.style.background = colorOf(q.theme);
      th.append(dot, mark(el('span', 'gx-quote-theme-name', q.theme), 'quotes', 'theme', 'id=' + enc(q.id)));
      const s = mark(el('span', 'gx-sent ' + (q.sentiment === 'Positive' ? 'pos' : 'neg'), q.sentiment), 'quotes', 'sentiment', 'id=' + enc(q.id));
      foot.append(th, s);
      fig.append(qmark, txt, more, foot);
      requestAnimationFrame(() => {
        if (fig.classList.contains('gx-open')) { more.hidden = txt.scrollHeight < 120; more.textContent = 'Show less'; return; }
        more.hidden = !(txt.scrollHeight > txt.clientHeight + 2);
      });
      return fig;
    }));
  }

  store.onData(() => {
    const T = store.data('themes');
    rowsT = T.status === 'ok' ? T.data.slice().sort((a, b) => Number(a.rank) - Number(b.rank)) : [];
    drawKpis();
    drawTake();
    drawActive();
    drawDonut();
    drawLegend();
    drawMap();
    drawGaps();
    drawVoice();
    applyFocus();
    const root = $('#pulse');
    if (!introDone && rowsT.length && store.data('quotes').status === 'ok') {
      introDone = true;
      if (!reduce) { root.classList.add('gx-in'); setTimeout(() => root.classList.remove('gx-in'), 1400); }
    }
  });
})();

loadData();
