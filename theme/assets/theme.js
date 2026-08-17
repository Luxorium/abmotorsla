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

  var CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
  var PHOTO = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8.5" cy="10" r="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="m4 17 5-4.5 4 3.2 3-2.4 4 3.4" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

  /* --------------------------------------------------------- menu drawer */
  var drawer = $('[data-drawer]');
  function closeDrawer() {
    if (!drawer) return;
    drawer.removeAttribute('open');
    document.body.style.overflow = '';
  }
  $$('[data-drawer-open]').forEach(function (b) {
    on(b, 'click', function () {
      if (!drawer) return;
      drawer.setAttribute('open', '');
      document.body.style.overflow = 'hidden';
      var first = drawer.querySelector('a, button');
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
  function hidePredictive(panel) {
    if (!panel) return;
    panel.hidden = true;
    var input = $('[data-predictive-input]', panel.parentNode);
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
      var sku = v.sku ? '<b>' + esc(v.sku) + '</b>' : '';
      var type = p.type ? esc(p.type) : '';
      var meta = [type, sku].filter(Boolean).join(' &middot; ');
      var thumb = p.featured_image && p.featured_image.url
        ? '<img src="' + esc(p.featured_image.url) + '" alt="" loading="lazy">'
        : PHOTO;
      return '<a class="predictive__item" href="' + esc(p.url) + '">' +
        '<span class="predictive__thumb">' + thumb + '</span>' +
        '<span><span class="predictive__title">' + esc(p.title) + '</span>' +
        (meta ? '<span class="predictive__meta">' + meta + '</span>' : '') + '</span>' +
        '<span class="predictive__price">' + esc(p.price ? money(p.price) : '') + '</span>' +
        '</a>';
    }).join('');

    return '<div class="predictive__head">Parts</div>' + rows +
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
  $$('[data-ymm]').forEach(function (form) {
    var makeSel = $('[data-ymm-make]', form);
    var modelSel = $('[data-ymm-model]', form);
    var yearSel = $('[data-ymm-year]', form);
    var submit = $('[data-ymm-submit]', form);
    var qField = $('[data-ymm-q]', form);
    var hint = $('[data-ymm-hint]', form);
    if (!makeSel || !window.fetch) return;

    var data = null;

    function fill(sel, values, placeholder) {
      sel.innerHTML = '<option value="">' + placeholder + '</option>' +
        values.map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + '</option>'; }).join('');
    }

    fetch(form.getAttribute('data-src'), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        data = json.makes || {};
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

    function sync() {
      var bits = [yearSel.value, makeSel.value, modelSel.value].filter(Boolean);
      // Year alone is useless as a search; require at least a make.
      var ok = Boolean(makeSel.value);
      submit.disabled = !ok;
      qField.value = bits.join(' ');
    }
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
    if (cartEl) { cartEl.hidden = true; document.body.style.overflow = ''; }
  }


  /* The hidden _ship line property (set by the product form from the product's
     ship:* tag) is what lets the drawer warn about a mixed cart without a second
     request for tags, which /cart.js does not return. */

  function shipLabel(group) {
    if (group === 'pickup') return '<span class="ship-line ship-line--pickup">Pickup only</span>';
    if (group === 'freight299') return '<span class="ship-line ship-line--freight">$299.99 freight</span>';
    if (group === 'freight199') return '<span class="ship-line ship-line--freight">$199.99 freight</span>';
    return '<span class="ship-line ship-line--free">Free shipping</span>';
  }

  function shipOf(line) {
    return (line.properties && line.properties._ship) || 'free';
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
    if (groups.indexOf('freight299') > -1 || groups.indexOf('freight199') > -1) {
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
      if (btn) { btn.setAttribute('aria-disabled', 'true'); btn.textContent = 'Adding…'; }

      fetch('/cart/add.js', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          items: [{
            id: Number($('[name="id"]', form).value),
            quantity: Number(($('[name="quantity"]', form) || { value: 1 }).value)
          }]
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
