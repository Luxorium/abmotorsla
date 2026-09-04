/* A&B Motors — theme behaviour. Vanilla JS, no dependencies, no build step. */
(function () {
  'use strict';

  /* ---------------------------------------------------------------- utils */
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  function on(el, evt, fn) { if (el) el.addEventListener(evt, fn); }

  function money(cents) {
    var fmt = window.ABM && window.ABM.moneyFormat;
    var value = (cents / 100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    if (!fmt) return '$' + value;
    return fmt.replace(/\{\{\s*amount\s*\}\}/, value).replace(/\{\{\s*amount_no_decimals\s*\}\}/, value);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Mirrors Shopify's own tag handleizing, which is what tag URLs are keyed on.
  // Both vehicle pickers build /collections/all/<tag> paths with it.
  function handleize(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  }

  var CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
  var PHOTO = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8.5" cy="10" r="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="m4 17 5-4.5 4 3.2 3-2.4 4 3.4" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

  /* ------------------------------------------- dialog focus management */
  /* `role="dialog" aria-modal="true"` is a promise: focus enters the dialog, stays
     inside while it is open, and returns to whatever opened it. Neither drawer kept
     it. Tab walked straight out into the page behind the scrim — and because the
     background paints *under* the overlay, the focus ring went invisible, so a
     keyboard user was left activating controls they could not see. Closing then
     dropped focus to <body>, restarting the next Tab at the top of the document. */
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  var openDialog = null;     // panel currently holding focus
  var dialogTrigger = null;  // control to hand focus back to on close

  function visibleFocusable(root) {
    return $$(FOCUSABLE, root).filter(function (el) {
      return el.offsetWidth || el.offsetHeight || el.getClientRects().length;
    });
  }

  // Only regions that can never contain a drawer, so this cannot inert a live dialog.
  function backgroundRegions(panel) {
    return $$('#MainContent, .footer').filter(function (el) { return !el.contains(panel); });
  }

  function trapFocus(panel, trigger) {
    if (!panel) return;
    dialogTrigger = trigger || dialogTrigger || document.activeElement;
    openDialog = panel;
    backgroundRegions(panel).forEach(function (el) {
      el.inert = true;
      el.setAttribute('aria-hidden', 'true');
    });
  }

  function releaseFocus() {
    if (!openDialog) return;
    backgroundRegions(openDialog).forEach(function (el) {
      el.inert = false;
      el.removeAttribute('aria-hidden');
    });
    openDialog = null;
    var back = dialogTrigger;
    dialogTrigger = null;
    if (back && document.contains(back)) back.focus();
  }

  on(document, 'keydown', function (e) {
    if (e.key !== 'Tab' || !openDialog) return;
    var items = visibleFocusable(openDialog);
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (!openDialog.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  /* --------------------------------------------------------- menu drawer */
  var drawer = $('[data-drawer]');
  function closeDrawer() {
    // Guarded on the open state so a stray Escape cannot release another dialog's trap.
    if (!drawer || !drawer.hasAttribute('open')) return;
    drawer.removeAttribute('open');
    document.body.style.overflow = '';
    releaseFocus();
  }
  $$('[data-drawer-open]').forEach(function (b) {
    on(b, 'click', function () {
      if (!drawer) return;
      drawer.setAttribute('open', '');
      document.body.style.overflow = 'hidden';
      var panel = drawer.querySelector('.drawer__panel') || drawer;
      trapFocus(panel, b);
      var first = visibleFocusable(panel)[0];
      if (first) first.focus();
    });
  });
  $$('[data-drawer-close]').forEach(function (b) { on(b, 'click', closeDrawer); });

  on(document, 'keydown', function (e) {
    if (e.key !== 'Escape') return;
    closeDrawer();
    closeCart();
    $$('[data-predictive-results]').forEach(hidePredictive);
    $$('details[data-nav-item][open]').forEach(function (d) { d.removeAttribute('open'); });
  });

  on(document, 'click', function (e) {
    $$('details[data-nav-item][open]').forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute('open');
    });
    $$('[data-predictive]').forEach(function (wrap) {
      if (!wrap.contains(e.target)) hidePredictive($('[data-predictive-results]', wrap));
    });
  });

  /* ---------------------------------------------------- predictive search */
  // With ~9,000 one-off parts, finding the part IS the funnel. Query SKU as
  // well as title so a stock number off the part's tag lands straight on it.
  // aria-selected is what the existing .predictive__item[aria-selected="true"] rule
  // styles, and what tells a screen reader which suggestion is current.
  function markSelected(panel, el) {
    $$('.predictive__item', panel).forEach(function (item) {
      item.setAttribute('aria-selected', item === el ? 'true' : 'false');
    });
  }

  function announce(wrap, count, term) {
    var status = $('[data-predictive-status]', wrap);
    if (!status) return;
    status.textContent = count
      ? count + (count === 1 ? ' part matches ' : ' parts match ') + '"' + term + '"'
      : 'No parts matched "' + term + '"';
  }

  function hidePredictive(panel) {
    if (!panel) return;
    var input = $('[data-predictive-input]', panel.parentNode);
    // Arrow keys move real focus onto result links. Hiding the panel while focus is
    // still inside it strands focus on an unrenderable node, so hand it back first.
    if (input && panel.contains(document.activeElement)) input.focus();
    panel.hidden = true;
    if (input) input.setAttribute('aria-expanded', 'false');
  }

  function renderPredictive(data, term) {
    var products = (data.resources && data.resources.results && data.resources.results.products) || [];
    if (!products.length) {
      return '<div class="predictive__empty">' +
        '<b>No parts matched "' + esc(term) + '"</b>' +
        'Not everything on the lot is listed yet — call the yard and we\'ll walk the racks for you.' +
        '</div>';
    }
    var rows = products.map(function (p) {
      var v = (p.variants && p.variants[0]) || {};
      var price = parseFloat(p.price);
      var sku = v.sku ? '<b>' + esc(v.sku) + '</b>' : '';
      var type = p.type ? esc(p.type) : '';
      var meta = [type, sku].filter(Boolean).join(' &middot; ');
      var thumb = p.featured_image && p.featured_image.url
        ? '<img src="' + esc(p.featured_image.url) + '" alt="" loading="lazy">'
        : PHOTO;
      return '<a class="predictive__item" role="option" aria-selected="false" href="' + esc(p.url) + '">' +
        '<span class="predictive__thumb">' + thumb + '</span>' +
        '<span><span class="predictive__title">' + esc(p.title) + '</span>' +
        (meta ? '<span class="predictive__meta">' + meta + '</span>' : '') + '</span>' +
        '<span class="predictive__price">' + esc(isNaN(price) ? '' : money(Math.round(price * 100))) + '</span>' +
        '</a>';
    }).join('');

    // The listbox wraps only the options: the heading and the "see all" link are not
    // options, and a listbox containing non-option children is an invalid structure.
    return '<div class="predictive__head">Parts</div>' +
      '<div class="predictive__list" role="listbox" aria-label="Part suggestions">' + rows + '</div>' +
      '<a class="predictive__all" href="' + (window.ABM.routes.search || '/search') +
      '?q=' + encodeURIComponent(term) + '">See all results for "' + esc(term) + '" &rarr;</a>';
  }

  $$('[data-predictive]').forEach(function (wrap) {
    var input = $('[data-predictive-input]', wrap);
    var panel = $('[data-predictive-results]', wrap);
    var body = $('[data-predictive-body]', wrap);
    if (!input || !panel || !window.fetch) return;

    var timer = null;
    var lastTerm = '';

    function lookup() {
      var term = input.value.trim();
      if (term.length < 2) { hidePredictive(panel); return; }
      if (term === lastTerm) return;
      lastTerm = term;

      var url = (window.ABM.routes.predictive || '/search/suggest') + '.json' +
        '?q=' + encodeURIComponent(term) +
        '&resources[type]=product&resources[limit]=8' +
        '&resources[options][unavailable_products]=last' +
        '&resources[options][fields]=title,product_type,variants.sku,vendor,tag';

      fetch(url, { headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (input.value.trim() !== term) return; // a newer keystroke won
          body.innerHTML = renderPredictive(data, term);
          announce(wrap, $$('.predictive__item', body).length, term);
          panel.hidden = false;
          input.setAttribute('aria-expanded', 'true');
        })
        .catch(function () { hidePredictive(panel); });
    }

    on(input, 'input', function () {
      clearTimeout(timer);
      timer = setTimeout(lookup, 180);
    });
    on(input, 'focus', function () { if (input.value.trim().length >= 2) lookup(); });

    on(input, 'keydown', function (e) {
      var items = $$('.predictive__item, .predictive__all', panel);
      if (!items.length || panel.hidden) return;
      var current = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[current + 1] || items[0]).focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); (items[current - 1] || input).focus(); }
    });
    on(panel, 'focusin', function (e) {
      markSelected(panel, e.target.classList.contains('predictive__item') ? e.target : null);
    });
    on(panel, 'keydown', function (e) {
      var items = $$('.predictive__item, .predictive__all', panel);
      var current = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[current + 1] || items[0]).focus(); }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (current <= 0) input.focus(); else items[current - 1].focus();
      }
    });
  });


  /* ------------------------------------------------- shop by vehicle (YMM) */
  // Data is a static theme asset built from the catalog's own year/make/model
  // tags, so the picker can never offer a vehicle we have no parts for.
  //
  // Submitting navigates to the exact tag filter — /collections/all/2014-subaru-legacy —
  // the same route the collection sidebar facet builds. It used to hand the three values
  // to /search as one keyword string, which is an OR match: "2014 Subaru Legacy" returned
  // 1,000 results padded out with Dodge, Honda and Ford parts, against 102 that actually
  // fit. The section promises "only the parts we actually have for it", so it has to be
  // the filter and not the search index.
  $$('[data-ymm]').forEach(function (form) {
    var makeSel = $('[data-ymm-make]', form);
    var modelSel = $('[data-ymm-model]', form);
    var yearSel = $('[data-ymm-year]', form);
    var submit = $('[data-ymm-submit]', form);
    var hint = $('[data-ymm-hint]', form);
    if (!makeSel || !window.fetch) return;

    var base = form.getAttribute('data-base') || '/collections/all';

    var data = null;
    var makeOnly = {};          // makes the catalogue can filter on without a model
    var defaultHint = hint ? hint.textContent : '';

    function fill(sel, values, placeholder) {
      sel.innerHTML = '<option value="">' + placeholder + '</option>' +
        values.map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + '</option>'; }).join('');
    }

    fetch(form.getAttribute('data-src'), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        data = json.makes || {};
        (json.make_only || Object.keys(data)).forEach(function (m) { makeOnly[m] = true; });
        fill(makeSel, Object.keys(data), 'Choose a make');
      })
      .catch(function () {
        if (hint) hint.textContent = 'Vehicle list unavailable — use the search box above, or call the yard.';
      });

    on(makeSel, 'change', function () {
      var make = makeSel.value;
      modelSel.disabled = !make;
      yearSel.disabled = true;
      yearSel.innerHTML = '<option value="">Any year</option>';
      if (!make || !data[make]) {
        modelSel.innerHTML = '<option value="">Choose a model</option>';
      } else {
        fill(modelSel, Object.keys(data[make]), 'Choose a model');
      }
      sync();
    });

    on(modelSel, 'change', function () {
      var years = (data[makeSel.value] || {})[modelSel.value] || [];
      yearSel.disabled = !years.length;
      fill(yearSel, years.slice().reverse(), 'Any year');
      sync();
    });

    on(yearSel, 'change', sync);

    // One tag, exactly as CoreYard writes it onto the product: "2014 Subaru Legacy",
    // "Subaru Legacy" or "Subaru". A year without a model has no tag of its own —
    // /collections/all/2014-subaru redirects away — so the year only counts once a
    // model is chosen, which is also the order the selects unlock in.
    function chosen() {
      if (makeSel.value && modelSel.value && yearSel.value) {
        return yearSel.value + ' ' + makeSel.value + ' ' + modelSel.value;
      }
      if (makeSel.value && modelSel.value) return makeSel.value + ' ' + modelSel.value;
      return makeSel.value || '';
    }

    on(form, 'submit', function (e) {
      var tag = chosen();
      if (!tag) return;
      e.preventDefault();
      submit.disabled = true;
      window.location.href = base + '/' + handleize(tag);
    });

    function sync() {
      // Year alone is useless; require at least a make. A handful of makes are tagged
      // only alongside a model — "Mercedes C-Class" exists, plain "Mercedes" does not —
      // and vehicles.json says which, so the button waits for a model rather than
      // sending the shopper to a filter the catalogue would drop.
      var needsModel = makeSel.value && !makeOnly[makeSel.value];
      submit.disabled = !makeSel.value || (needsModel && !modelSel.value);
      if (hint) hint.textContent = (needsModel && !modelSel.value)
        ? 'Choose a model to see ' + makeSel.value + ' parts.'
        : defaultHint;
    }
  });

  /* ------------------------------------------- vehicle-aware search (H-01) */
  // Shopify search is an OR match: "Subaru Legacy" returns 515 results whose tail is other
  // makes entirely, against 102 parts that actually fit. The tag filter is the honest
  // answer, and vehicles.json already says which vehicles have one.
  //
  // A query that is ONLY a vehicle is replaced by that vehicle's filter (location.replace,
  // so Back still returns to wherever the shopper came from). A query that names a vehicle
  // plus other words keeps its results and gets a link above them — those other words are
  // the part they came for, and the filter alone would throw them away.
  $$('[data-vehicle-route]').forEach(function (mount) {
    var terms = (mount.getAttribute('data-terms') || '').trim();
    var base = mount.getAttribute('data-base') || '/collections/all';
    if (!terms || !window.fetch) return;

    // "Search all parts by keyword instead" on the filtered page links back here. Without
    // this the two would bounce off each other forever, because the query that sent the
    // shopper to the filter is the same query they just asked to see keyword results for.
    var askedForKeywords =
      new URLSearchParams(window.location.search || '').get('from') === 'keyword';

    fetch(mount.getAttribute('data-src'), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        var data = json.makes || {};
        var makeOnly = {};
        (json.make_only || Object.keys(data)).forEach(function (m) { makeOnly[m] = true; });

        var words = terms.split(/\s+/).filter(Boolean);
        var used = [];

        // Pull out a 4-digit year that looks like a model year, wherever it sits.
        var year = '', yearAt = -1;
        for (var i = 0; i < words.length; i++) {
          if (/^(19|20)\d{2}$/.test(words[i])) { year = words[i]; yearAt = i; used.push(i); break; }
        }

        // Longest make first, so "Land Rover" is not read as a make called "Land".
        function matchAt(list, from) {
          var best = null;
          list.forEach(function (name) {
            var parts = name.toLowerCase().split(' ');
            if (parts.length > words.length - from) return;
            for (var k = 0; k < parts.length; k++) {
              if (used.indexOf(from + k) !== -1) return;
              if (words[from + k].toLowerCase().replace(/[^a-z0-9]/g, '') !==
                  parts[k].replace(/[^a-z0-9]/g, '')) return;
            }
            if (!best || parts.length > best.span) best = { name: name, span: parts.length };
          });
          return best;
        }

        var makes = Object.keys(data).sort(function (a, b) { return b.length - a.length; });
        var make = null, makeAt = -1;
        for (var w = 0; w < words.length && !make; w++) {
          if (used.indexOf(w) !== -1) continue;
          var hit = matchAt(makes, w);
          if (hit) { make = hit; makeAt = w; }
        }

        // People type the model on its own — the hero placeholder suggests "2012 Silverado
        // alternator". A model is only a vehicle when exactly one make has it: "Silverado"
        // is a Chevrolet and nothing else, while "1500" is a Chevrolet, a GMC and a Dodge,
        // and guessing between them would be the same wrong-vehicle answer we are fixing.
        var soleMake = null;
        if (!make) {
          var owners = {};
          Object.keys(data).forEach(function (mk) {
            Object.keys(data[mk]).forEach(function (md) {
              var key = md.toLowerCase();
              (owners[key] = owners[key] || []).push(mk);
            });
          });
          var names = Object.keys(owners).filter(function (k) { return owners[k].length === 1; })
            .sort(function (a, b) { return b.length - a.length; });
          for (var u = 0; u < words.length && !soleMake; u++) {
            if (used.indexOf(u) !== -1) continue;
            var mfound = matchAt(names, u);
            if (!mfound) continue;
            var owner = owners[mfound.name.toLowerCase()][0];
            // Keep the catalogue's own casing rather than the shopper's.
            var real = Object.keys(data[owner]).filter(function (md) {
              return md.toLowerCase() === mfound.name.toLowerCase();
            })[0];
            soleMake = { make: owner, model: real, span: mfound.span, at: u };
          }
          if (!soleMake) return;
          make = { name: soleMake.make, span: 0 };
          makeAt = soleMake.at;
        }
        for (var m = 0; m < make.span; m++) used.push(makeAt + m);

        var model = null, modelAt = -1;
        if (soleMake) {
          model = { name: soleMake.model, span: soleMake.span };
          modelAt = soleMake.at;
        } else {
          var models = Object.keys(data[make.name] || {}).sort(function (a, b) { return b.length - a.length; });
          for (var v = 0; v < words.length && !model; v++) {
            if (used.indexOf(v) !== -1) continue;
            var mhit = matchAt(models, v);
            if (mhit) { model = mhit; modelAt = v; }
          }
        }
        if (model) { for (var n = 0; n < model.span; n++) used.push(modelAt + n); }

        // A year only narrows a model, and only one the catalogue actually tagged. A year we
        // do not stock stays in the query as a leftover word, so the shopper gets the link
        // and their own results rather than being moved somewhere that quietly ignored it.
        function dropYear() {
          year = '';
          used = used.filter(function (ix) { return ix !== yearAt; });
        }
        if (year && model) {
          var yrs = (data[make.name] || {})[model.name] || [];
          if (yrs.indexOf(parseInt(year, 10)) === -1) dropYear();
        } else if (year) {
          dropYear();
        }

        var tag = model
          ? (year ? year + ' ' + make.name + ' ' + model.name : make.name + ' ' + model.name)
          : (makeOnly[make.name] ? make.name : '');
        if (!tag) return;

        var url = base + '/' + handleize(tag);
        var leftover = words.filter(function (_, ix) { return used.indexOf(ix) === -1; });

        if (!leftover.length && !askedForKeywords) {
          // The query was the vehicle and nothing else — the filter IS the answer.
          window.location.replace(url + '?from=search&q=' + encodeURIComponent(terms));
          return;
        }

        mount.className = 'vehicle-route';
        mount.innerHTML =
          '<p><b>Looking for ' + esc(tag) + ' parts?</b> These keyword results include parts ' +
          'for other vehicles. <a href="' + esc(url) + '">See only parts that fit the ' +
          esc(tag) + '</a>, then narrow by type.</p>';
      })
      .catch(function () { /* keyword results stand on their own */ });
  });

  /* Arrival note for a search that was answered with a vehicle filter, so the shopper is
     told what happened instead of silently finding themselves somewhere else. */
  (function () {
    var params = new URLSearchParams(window.location.search || '');
    if (params.get('from') !== 'search') return;
    var main = $('#MainContent') || document.body;
    var host = $('.page-width', main) || main;
    var said = document.createElement('div');
    said.className = 'vehicle-route';
    var asked = params.get('q') || '';
    said.innerHTML = '<p>Showing parts that fit this vehicle' +
      (asked ? ', for your search &ldquo;' + esc(asked) + '&rdquo;' : '') +
      '. <a href="/search?q=' + encodeURIComponent(asked) + '&amp;options%5Bprefix%5D=last' +
      '&amp;from=keyword">Search all parts by keyword instead</a>.</p>';
    host.insertBefore(said, host.firstChild);
  })();

  /* ------------------------------------- collection vehicle facet (YMM) */
  // Same vehicles.json the home-page picker uses, driving Shopify's native tag
  // filtering — /collections/all/ford+ford-ranger — so narrowing needs no filter
  // app. Make/model/year cascade client-side; only "Find parts" navigates, so
  // picking a vehicle costs one page load instead of three.
  $$('[data-ymm-facet]').forEach(function (root) {
    var makeSel = $('[data-ymmf-make]', root);
    var modelSel = $('[data-ymmf-model]', root);
    var yearSel = $('[data-ymmf-year]', root);
    var applyBtn = $('[data-ymmf-apply]', root);
    var clearWrap = $('[data-ymmf-clear]', root);
    var clearLink = $('[data-ymmf-clear-link]', root);
    var note = $('[data-ymmf-note]', root);
    if (!makeSel || !modelSel || !yearSel || !applyBtn || !window.fetch) return;

    var base = root.getAttribute('data-base') || '';
    var sort = root.getAttribute('data-facet-sort') || '';
    var current = (root.getAttribute('data-tags') || '').split('~').filter(Boolean);
    var makes = [];
    var data = null;
    var hasActive = false;
    var busy = false;

    function fill(sel, values, placeholder, selected) {
      sel.innerHTML = '<option value="">' + placeholder + '</option>' +
        values.map(function (v) {
          return '<option value="' + esc(v) + '"' + (v === selected ? ' selected' : '') +
            '>' + esc(v) + '</option>';
        }).join('');
    }

    // "1994 Ford Ranger" / "Ford Ranger" / "Ford" -> parts, if we stock that vehicle.
    // Longest make first, so "Land Rover" is not mistaken for a make called "Land".
    function parseVehicle(tag) {
      if (!data) return null;
      var year = '';
      var rest = tag;
      var m = /^(\d{4})\s+(.+)$/.exec(tag);
      if (m) { year = m[1]; rest = m[2]; }
      for (var i = 0; i < makes.length; i++) {
        var make = makes[i];
        if (rest === make) return year ? null : { make: make, model: '', year: '' };
        if (rest.indexOf(make + ' ') === 0) {
          var model = rest.slice(make.length + 1);
          if (data[make][model]) return { make: make, model: model, year: year };
        }
      }
      return null;
    }

    // Keep every non-vehicle tag already on the URL, swap in the chosen vehicle.
    function urlFor(vehicleTag) {
      var keep = current.filter(function (t) { return !parseVehicle(t); });
      var tags = keep.concat(vehicleTag ? [vehicleTag] : []).map(handleize);
      return base + (tags.length ? '/' + tags.join('+') : '') +
        (sort ? '?sort_by=' + encodeURIComponent(sort) : '');
    }

    function chosen() {
      if (makeSel.value && modelSel.value && yearSel.value) {
        return yearSel.value + ' ' + makeSel.value + ' ' + modelSel.value;
      }
      if (makeSel.value && modelSel.value) return makeSel.value + ' ' + modelSel.value;
      if (makeSel.value) return makeSel.value;
      return '';
    }

    function sync() { applyBtn.disabled = !(makeSel.value || hasActive); }

    function models(make) { return Object.keys(data[make]).sort(); }
    function years(make, model) {
      return ((data[make] || {})[model] || []).slice().reverse().map(String);
    }

    on(makeSel, 'change', function () {
      var make = makeSel.value;
      yearSel.innerHTML = '<option value="">Any year</option>';
      yearSel.disabled = true;
      if (!make || !data || !data[make]) {
        modelSel.innerHTML = '<option value="">Any model</option>';
        modelSel.disabled = true;
      } else {
        fill(modelSel, models(make), 'Any model', '');
        modelSel.disabled = false;
      }
      sync();
    });

    on(modelSel, 'change', function () {
      var list = (data && modelSel.value) ? years(makeSel.value, modelSel.value) : [];
      fill(yearSel, list, 'Any year', '');
      yearSel.disabled = !list.length;
      sync();
    });

    on(yearSel, 'change', sync);

    on(applyBtn, 'click', function () {
      if (busy) return;
      busy = true;
      applyBtn.disabled = true;
      window.location.href = urlFor(chosen());
    });

    fetch(root.getAttribute('data-src'), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        data = json.makes || {};
        // Longest first for prefix matching; the <select> itself stays alphabetical.
        makes = Object.keys(data).sort(function (a, b) { return b.length - a.length; });

        var active = null;
        for (var i = 0; i < current.length && !active; i++) active = parseVehicle(current[i]);
        hasActive = Boolean(active);

        fill(makeSel, Object.keys(data).sort(), 'Any make', active ? active.make : '');
        if (active) {
          fill(modelSel, models(active.make), 'Any model', active.model);
          modelSel.disabled = false;
          if (active.model) {
            fill(yearSel, years(active.make, active.model), 'Any year', active.year);
            yearSel.disabled = false;
          }
          if (clearWrap && clearLink) {
            clearLink.href = urlFor('');
            clearWrap.hidden = false;
          }
        }
        sync();
      })
      .catch(function () {
        if (note) {
          note.hidden = false;
          note.textContent = 'Vehicle list unavailable — use the search box, or call the yard.';
        }
      });
  });

  /* --------------------------------------------------- related parts */
  // The page ships with the part's own category collection already rendered.
  // If the part names a vehicle, upgrade that to parts which actually fit it,
  // fetched from the tag-filtered cards view. Any failure leaves the baseline.
  $$('[data-related]').forEach(function (root) {
    var url = root.getAttribute('data-related-url');
    var heading = root.getAttribute('data-related-heading');
    var exclude = root.getAttribute('data-related-exclude');
    var grid = $('[data-related-grid]', root);
    var title = $('[data-related-title]', root);
    if (!url || !grid || !window.fetch) return;

    fetch(url, { headers: { Accept: 'text/html' } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) {
        var holder = document.createElement('div');
        holder.innerHTML = html;
        var cards = $$('.card', holder).filter(function (c) {
          return c.getAttribute('data-product-id') !== exclude;
        }).slice(0, 4);

        // One lonely card is worse than the category row we already have.
        if (cards.length < 2) return;

        grid.innerHTML = '';
        cards.forEach(function (c) { grid.appendChild(c); });
        if (title && heading) title.textContent = heading;
      })
      .catch(function () { /* keep the server-rendered category row */ });
  });

  /* ------------------------------------------------------------ gallery */
  $$('[data-gallery]').forEach(function (gallery) {
    var main = $('[data-gallery-main]', gallery);
    var thumbs = $$('[data-gallery-thumb]', gallery);
    if (!main || !thumbs.length) return;

    function select(btn) {
      var src = btn.getAttribute('data-full');
      if (src) { main.src = src; main.alt = btn.getAttribute('data-alt') || ''; }
      thumbs.forEach(function (t) { t.setAttribute('aria-current', String(t === btn)); });
    }
    thumbs.forEach(function (t) {
      on(t, 'click', function () { select(t); });
      on(t, 'keydown', function (e) {
        var i = thumbs.indexOf(t);
        if (e.key === 'ArrowRight' && thumbs[i + 1]) { thumbs[i + 1].focus(); select(thumbs[i + 1]); }
        if (e.key === 'ArrowLeft' && thumbs[i - 1]) { thumbs[i - 1].focus(); select(thumbs[i - 1]); }
      });
    });
  });

  /* ---------------------------------------------------------- quantity */
  $$('[data-qty]').forEach(function (wrap) {
    var input = $('input', wrap);
    $$('button', wrap).forEach(function (btn) {
      on(btn, 'click', function () {
        var step = btn.getAttribute('data-qty-step') === 'down' ? -1 : 1;
        var min = parseInt(input.getAttribute('min') || '1', 10);
        var max = parseInt(input.getAttribute('max') || '999', 10);
        input.value = Math.min(max, Math.max(min, (parseInt(input.value, 10) || min) + step));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
  });

  /* ------------------------------------------------------ facet filters */
  var facetForm = $('[data-facet-form]');
  if (facetForm) {
    on(facetForm, 'change', function () {
      if (facetForm.dataset.autoSubmit === 'false') return;
      facetForm.submit();
    });
  }
  $$('[data-sort]').forEach(function (sel) {
    // Only the <select> itself. A change event from any control inside a container
    // that also carried data-sort would bubble up and read .value off the container.
    if (sel.tagName !== 'SELECT') return;
    on(sel, 'change', function () {
      var url = new URL(window.location.href);
      url.searchParams.set('sort_by', sel.value);
      url.searchParams.delete('page');
      window.location.href = url.toString();
    });
  });

  /* -------------------------------------------------------- cart drawer */
  var cartEl = null;

  function closeCart() {
    if (!cartEl || cartEl.hidden) return;
    cartEl.hidden = true;
    document.body.style.overflow = '';
    releaseFocus();
  }


  /* The hidden _ship line property (set by the product form from the product's
     ship:* tag) is what lets the drawer warn about a mixed cart without a second
     request for tags, which /cart.js does not return. */

  /* Wording comes from snippets/shipping-config.liquid, which is rendered from the same
     contract the Liquid notices use. The literals below are only a fallback for a page
     that somehow rendered without it. */
  var SHIP_FALLBACK = {
    pickup: 'Pickup only',
    freight299: '$299.99 freight',
    freight199: '$199.99 freight',
    ground34: '$34.99 shipping',
    ground24: '$24.99 shipping',
    free: 'Free shipping'
  };

  /* Which groups are freight — a truck, a commercial address, a forklift. NOT simply the
     ones that cost something: a paid parcel group costs something too, and warning its
     buyer about a forklift loses the sale. */
  var FREIGHT_FALLBACK = { freight299: true, freight199: true };

  function shipText(group) {
    var config = (window.ABM_SHIP && window.ABM_SHIP.labels) || SHIP_FALLBACK;
    return config[group] || config.free || SHIP_FALLBACK.free;
  }

  function shipKind(group) {
    if (group === 'pickup') { return 'pickup'; }
    if (isFreight(group)) { return 'freight'; }
    return group === 'free' ? 'free' : 'ground';
  }

  function shipLabel(group) {
    return '<span class="ship-line ship-line--' + shipKind(group) + '">' +
      shipText(group) + '</span>';
  }

  function shipOf(line) {
    return (line.properties && line.properties._ship) || 'free';
  }

  function isFreight(group) {
    var rates = (window.ABM_SHIP && window.ABM_SHIP.freight && window.ABM_SHIP.freight.rates)
      || FREIGHT_FALLBACK;
    return Object.prototype.hasOwnProperty.call(rates, group);
  }

  function cartAlert(cart) {
    var groups = cart.items.map(shipOf);
    var pickup = groups.filter(function (g) { return g === 'pickup'; }).length;
    var shipped = groups.length - pickup;
    if (pickup && shipped) {
      return '<div class="cart-alert">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h11v10H3zM14 9h3.6l2.4 3v4h-6" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/></svg>' +
        '<div><b>This cart mixes a pickup-only part with parts we ship.</b>' +
        '<p>Checkout can\'t combine them. Place two separate orders, or call the yard and we\'ll handle it.</p></div></div>';
    }
    if (pickup) {
      return '<div class="cart-alert">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z" fill="none" stroke="currentColor" stroke-width="1.9"/></svg>' +
        '<div><b>Pickup only — Amite, Louisiana</b>' +
        '<p>Choose <b>Pickup</b> at checkout. Ready at the counter within one business day.</p></div></div>';
    }
    if (groups.filter(isFreight).length) {
      return '<div class="cart-alert">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h11v10H3zM14 9h3.6l2.4 3v4h-6" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/></svg>' +
        '<div><b>Freight order — commercial address required</b>' +
        '<p>Must ship to a business address with a forklift, or pick up at a freight terminal. We\'ll call before anything ships.</p></div></div>';
    }
    return '';
  }

  function cartMarkup(cart) {
    if (!cart.item_count) {
      return '<div class="empty-state" style="margin:24px 0;border:0;">' +
        '<h2 class="h3">Nothing in the cart yet</h2>' +
        '<p class="muted">Find your part by number, or start with the part type.</p></div>';
    }
    return cart.items.map(function (line, i) {
      var img = line.image
        ? '<img src="' + esc(line.image) + '" alt="" loading="lazy">'
        : '';
      return '<div class="cart-item">' +
        '<a class="cart-item__media" href="' + esc(line.url) + '">' + img + '</a>' +
        '<div><a href="' + esc(line.url) + '" style="font-weight:650;font-size:14px;line-height:1.35;display:block;">' +
        esc(line.product_title) + '</a>' +
        '<div class="card__meta" style="margin-top:6px;">' +
        (line.sku ? '<span>Stock <b>' + esc(line.sku) + '</b></span>' : '') +
        '<span>Qty ' + line.quantity + '</span></div>' +
        '<div class="card__meta" style="margin-top:4px;">' + shipLabel(shipOf(line)) + '</div>' +
        '<button type="button" class="small muted" data-cart-remove="' + (i + 1) +
        '" style="border:0;background:none;padding:0;margin-top:8px;text-decoration:underline;cursor:pointer;">Remove</button>' +
        '</div>' +
        '<div class="cart-item__price" style="text-align:right;"><span class="price" style="font-size:16px;">' +
        money(line.final_line_price) + '</span></div>' +
        '</div>';
    }).join('');
  }

  function openCart(cart) {
    if (!cartEl) {
      cartEl = document.createElement('div');
      cartEl.className = 'cart-drawer';
      cartEl.hidden = true;
      document.body.appendChild(cartEl);
      on(cartEl, 'click', function (e) {
        if (e.target.hasAttribute('data-cart-close') || e.target.classList.contains('cart-drawer__scrim')) {
          closeCart();
        }
        var rm = e.target.getAttribute('data-cart-remove');
        if (rm) {
          fetch('/cart/change.js', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ line: Number(rm), quantity: 0 })
          }).then(function (r) { return r.json(); }).then(function (c) {
            openCart(c);
            syncCount(c);
          });
        }
      });
    }

    cartEl.innerHTML =
      '<button class="cart-drawer__scrim" data-cart-close aria-label="Close cart" tabindex="-1"></button>' +
      '<div class="cart-drawer__panel" role="dialog" aria-modal="true" aria-label="Cart">' +
        '<div class="cart-drawer__head"><h2>Your cart (' + cart.item_count + ')</h2>' +
        '<button class="icon-btn" data-cart-close aria-label="Close">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>' +
        '</button></div>' +
        '<div class="cart-drawer__items">' + cartAlert(cart) + cartMarkup(cart) + '</div>' +
        (cart.item_count
          ? '<div class="cart-drawer__foot">' +
            '<div class="cart-drawer__row"><span>Subtotal</span><b>' + money(cart.total_price) + '</b></div>' +
            '<p class="small muted" style="margin:0 0 12px;">Shipping calculated at checkout. Most parts ship free.</p>' +
            '<form action="/cart" method="post"><button type="submit" name="checkout" class="btn btn--lg btn--full">Check out</button></form>' +
            '<a href="/cart" class="btn btn--outline btn--full" style="margin-top:8px;">View cart</a>' +
            '</div>'
          : '') +
      '</div>';

    cartEl.hidden = false;
    document.body.style.overflow = 'hidden';
    // Removing a line re-renders this panel, so re-point the trap at the new node but
    // keep the original trigger — the control to return to is still the one that opened it.
    trapFocus(cartEl.querySelector('.cart-drawer__panel'), openDialog ? dialogTrigger : document.activeElement);
    var closeBtn = cartEl.querySelector('.icon-btn[data-cart-close]');
    if (closeBtn) closeBtn.focus();
  }

  function toast(msg) {
    var el = document.createElement('div');
    el.className = 'toast';
    el.setAttribute('role', 'status');
    el.innerHTML = CHECK + '<span>' + esc(msg) + '</span>';
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 2600);
  }

  function syncCount(cart) {
    $$('[data-cart-count]').forEach(function (el) {
      el.textContent = cart.item_count;
      el.hidden = cart.item_count === 0;
    });
    $$('[data-cart-total]').forEach(function (el) { el.textContent = money(cart.total_price); });
  }

  /* ------------------------------------------------------- add to cart */
  $$('[data-product-form]').forEach(function (form) {
    on(form, 'submit', function (e) {
      if (!window.fetch) return;
      e.preventDefault();
      var btn = $('[data-add-button]', form);
      var label = btn ? btn.textContent : '';
      var ship = $('[name="properties[_ship]"]', form);
      var item = {
        id: Number($('[name="id"]', form).value),
        quantity: Number(($('[name="quantity"]', form) || { value: 1 }).value)
      };
      if (ship) item.properties = { _ship: ship.value };
      if (btn) { btn.setAttribute('aria-disabled', 'true'); btn.textContent = 'Adding…'; }

      fetch('/cart/add.js', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          items: [item]
        })
      })
        .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.body.description || res.body.message || 'Could not add to cart');
          if (btn) { btn.textContent = label; btn.removeAttribute('aria-disabled'); }
          return fetch('/cart.js', { headers: { Accept: 'application/json' } }).then(function (r) { return r.json(); });
        })
        .then(function (cart) {
          syncCount(cart);
          if (window.ABM.cartType === 'page') { window.location.href = '/cart'; return; }
          openCart(cart);
          toast('Added to cart');
        })
        .catch(function (err) {
          if (btn) { btn.textContent = label; btn.removeAttribute('aria-disabled'); }
          var note = $('[data-cart-error]', form);
          if (note) { note.textContent = err.message; note.hidden = false; }
          else window.alert(err.message);
        });
    });
  });

  // Header cart icon opens the drawer instead of navigating.
  $$('[data-cart-link]').forEach(function (link) {
    on(link, 'click', function (e) {
      if (!window.fetch || window.ABM.cartType === 'page') return;
      e.preventDefault();
      fetch('/cart.js', { headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(openCart);
    });
  });

  /* ------------------------------------------------------- cart page qty */
  $$('[data-cart-qty]').forEach(function (input) {
    on(input, 'change', function () {
      fetch('/cart/change.js', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ line: Number(input.getAttribute('data-line')), quantity: Number(input.value) })
      }).then(function () { window.location.reload(); });
    });
  });

  /* ----------------------------------------------- recently viewed parts */
  try {
    var pid = document.body.getAttribute('data-product-id');
    if (pid) {
      var key = 'abm:recent';
      var list = JSON.parse(localStorage.getItem(key) || '[]').filter(function (id) { return id !== pid; });
      list.unshift(pid);
      localStorage.setItem(key, JSON.stringify(list.slice(0, 12)));
    }
  } catch (e) { /* storage disabled — not important */ }

  /* --------------------------------------------------- sticky buy reveal */
  var sticky = $('[data-sticky-buy]');
  var buybox = $('[data-buybox]');
  if (sticky && buybox && 'IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      sticky.style.visibility = entries[0].isIntersecting ? 'hidden' : 'visible';
    }, { rootMargin: '0px 0px -60% 0px' }).observe(buybox);
  }

  if (window.fetch) {
    fetch('/cart.js', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(syncCount)
      .catch(function () {});
  }
})();
