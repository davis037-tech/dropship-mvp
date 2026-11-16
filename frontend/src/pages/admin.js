import { h, mount, $ } from '../components/dom.js';
import { api } from '../services/api.js';
import { countryName } from '../services/format.js';

const TOKEN_KEY = 'admin.token';
const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
const setToken = (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } };
const app = $('#app');
const logoutBtn = $('#logout');
const money = (v, c = '') => (v == null ? '—' : `${Number(v).toFixed(2)} ${c}`.trim());

async function call(path, opts = {}) {
  try { return await api(path, { ...opts, token: getToken() }); }
  catch (e) { if (e.status === 401 && path !== '/api/admin/login') { setToken(''); location.hash = '#/login'; } throw e; }
}
const notice = (kind, text) => h('p', { class: `notice notice-${kind}`, role: 'alert' }, text);
const nav = (active) => h('nav', { class: 'admin-nav', 'aria-label': 'Admin' },
  [['#/', 'Dashboard'], ['#/products', 'Products'], ['#/orders', 'Orders'], ['#/settings', 'Shipping & policies']]
    .map(([href, label]) => h('a', { href, 'aria-current': active === href ? 'page' : null }, label)));
const badge = (v, bad = []) => h('span', { class: `badge ${bad.includes(v) ? 'badge-bad' : ''}` }, String(v).replaceAll('_', ' '));
const table = (heads, rows) => h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, h('thead', {}, h('tr', {}, heads.map((x) => h('th', { scope: 'col' }, x)))), h('tbody', {}, rows)));

// ---------------------------------------------------------------- login
function login() {
  logoutBtn.classList.add('hidden');
  const err = h('div', {});
  const email = h('input', { type: 'email', id: 'email', autocomplete: 'username', required: true });
  const pw = h('input', { type: 'password', id: 'pw', autocomplete: 'current-password', required: true });
  mount(app, h('div', { class: 'narrow' }, h('h1', {}, 'Admin login'), h('form', { onsubmit: async (e) => {
    e.preventDefault();
    try { const r = await api('/api/admin/login', { method: 'POST', body: { email: email.value, password: pw.value } }); setToken(r.token); location.hash = '#/'; }
    catch (x) { mount(err, notice('error', x.message)); }
  } }, h('label', { for: 'email' }, 'Email'), email, h('label', { for: 'pw' }, 'Password'), pw, err,
  h('p', {}, h('button', { class: 'btn btn-green', type: 'submit' }, 'Log in')))));
}

// ---------------------------------------------------------------- dashboard
async function dashboard() {
  const d = await call('/api/admin/dashboard');
  const stat = (label, v) => h('div', { class: 'stat' }, h('b', {}, String(v)), h('span', { class: 'small muted' }, label));
  mount(app, h('h1', {}, 'Dashboard'), nav('#/'), h('div', { class: 'stats' },
    stat('Total orders', d.total_orders), stat('Paid orders', d.paid_orders), stat('Awaiting supplier approval', d.awaiting_supplier_approval),
    stat('Shipped', d.shipped), stat('Delivered', d.delivered), stat(`Revenue (${d.currency})`, money(d.revenue)),
    stat(`Estimated profit (${d.currency})`, money(d.estimated_profit)),
    stat('Needing review (orders / products)', `${d.orders_needing_review} / ${d.products_needing_review}`)),
  h('p', { class: 'small muted' }, 'Profit is an estimate from cost snapshots taken at order time (fees, refund reserve and ad allowance included). It is not accounting data.'));
}

// ---------------------------------------------------------------- products
async function products() {
  const { products: rows } = await call('/api/admin/products');
  const out = h('div', {});
  const keyword = h('input', { placeholder: 'Search CJ products (e.g. pet mat)', id: 'cj-keyword' });
  const cjOut = h('div', {});
  const fetchCJ = async () => {
    try {
      const r = await call('/api/admin/cj/products?keyword=' + encodeURIComponent(keyword.value.trim()) + '&size=5');
      mount(cjOut, r.products.length ? table(['CJ product', 'Cost', 'Stock', ''], r.products.map((p) => h('tr', {},
        h('td', {}, h('div', {}, p.name), h('div', { class: 'small muted' }, p.supplier_product_id)),
        h('td', {}, money(p.supplier_cost, p.supplier_currency)),
        h('td', {}, String(p.stock)),
        h('td', {}, h('button', { class: 'btn btn-green btn-sm', type: 'button', onclick: async () => {
          try {
            // Import exactly the rows already returned by CJ instead of asking CJ
            // for the same catalog page again.
            const imported = await call('/api/admin/products/import-cj', { method: 'POST', body: { products: r.products } });
            mount(cjOut, imported.errors && imported.errors.length ? notice('error', `Imported ${imported.count} products. ${imported.errors.join(' | ')}`) : notice('ok', `Imported ${imported.count} CJ products. The storefront/database now uses the saved product records.`));
            setTimeout(products, 1000);
          } catch (e) { mount(cjOut, notice('error', e.message)); }
        } }, 'Import results'))))) : h('p', { class: 'muted' }, 'No CJ products found.'));
    } catch (e) { mount(cjOut, notice('error', e.message)); }
  };
  mount(app, h('h1', {}, 'Products'), nav('#/products'), out,
    h('div', { class: 'actions' }, h('a', { class: 'btn btn-green btn-sm', href: '#/products/new' }, 'New product'),
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: async () => {
        try { const r = await call('/api/admin/products/sync-all', { method: 'POST' }); mount(out, notice('ok', `Sync finished: ${r.results.map((x) => (x.skipped ? 'skipped' : x.error ? 'error: ' + x.error : x.flagged ? 'flagged' : 'ok')).join(', ') || 'nothing to sync'}`)); setTimeout(products, 1200); }
        catch (e) { mount(out, notice('error', e.message)); } } }, 'Sync all from supplier')),
    h('div', { class: 'card' }, h('h2', { class: 'small-title' }, 'Find products in CJ'),
      h('div', { class: 'actions' }, keyword, h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: fetchCJ }, 'Fetch from CJ')),
      h('p', { class: 'small muted' }, 'CJ results are enriched with live variant stock and price during import. Import only 3–5 products at a time.'),
      cjOut),
    table(['Product', 'Retail price', 'Supplier cost', 'Landed', 'Margin', 'Stock', 'Status', 'Last sync', ''], rows.map((p) => h('tr', {},
      h('td', {}, p.name), h('td', {}, money(p.retail_price, p.currency)), h('td', {}, money(p.supplier_cost, p.supplier_currency)),
      h('td', {}, money(p.landed_cost, p.currency)), h('td', {}, p.economics ? `${p.economics.margin_percent}% (${p.economics.landed_multiple}× landed)` : '—'), h('td', {}, String(p.stock)),
      h('td', {}, badge(p.status, ['needs_review', 'unavailable']), p.review_reason ? h('div', { class: 'small muted' }, p.review_reason) : null),
      h('td', {}, p.last_synced_at ? new Date(p.last_synced_at).toLocaleString() : 'never'),
      h('td', {}, h('a', { href: `#/products/${p.id}` }, 'Edit'))))));
}

const specsToText = (s) => (s || []).map((x) => `${x.label}: ${x.value}`).join('\n');
const textToSpecs = (t) => t.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.indexOf(':'); return { label: l.slice(0, i).trim(), value: l.slice(i + 1).trim() }; });
const imgsToText = (s) => (s || []).map((x) => `${x.url} | ${x.alt || ''}`).join('\n');
const textToImgs = (t) => t.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => { const [url, ...alt] = l.split('|'); return { url: url.trim(), alt: alt.join('|').trim() }; });

async function productForm(id) {
  const p = id === 'new' ? { name: '', slug: '', description: '', supplier_product_id: '', supplier_platform: '1688', supplier_currency: 'CNY', supplier_cost: 0, estimated_shipping_cost: 0, agent_fee: 0, other_supplier_costs: 0, retail_price: 0, market_price: '', stock: 0, status: 'draft', images: [], specifications: [] }
    : await call(`/api/admin/products/${id}`);
  const msg = h('div', {});
  const f = {};
  const input = (key, label, attrs = {}) => { f[key] = h('input', { id: key, value: p[key] ?? '', ...attrs }); return h('div', {}, h('label', { for: key }, label), f[key]); };
  f.description = h('textarea', { id: 'description', rows: 6 }, p.description);
  f.images = h('textarea', { id: 'images', rows: 3, placeholder: 'https://… | alt text (one per line)' }, imgsToText(p.images));
  f.specs = h('textarea', { id: 'specs', rows: 4, placeholder: 'Label: value (one per line)' }, specsToText(p.specifications));
  f.status = h('select', { id: 'status' }, ['draft', 'active', 'unavailable', 'archived'].map((s) => h('option', { value: s, selected: s === p.status }, s)));
  f.rec = h('input', { type: 'checkbox', id: 'rec' });
  const body = () => ({
    name: f.name.value, slug: f.slug.value, description: f.description.value, supplier_product_id: f.supplier_product_id.value || null,
    supplier_platform: f.supplier_platform.value, supplier_currency: f.supplier_currency.value, supplier_cost: f.supplier_cost.value,
    estimated_shipping_cost: f.estimated_shipping_cost.value, agent_fee: f.agent_fee.value, other_supplier_costs: f.other_supplier_costs.value,
    retail_price: f.retail_price.value, market_price: f.market_price.value || null, use_recommended_price: f.rec.checked, stock: parseInt(f.stock.value || '0', 10),
    status: ['needs_review'].includes(f.status.value) ? undefined : f.status.value, images: textToImgs(f.images.value), specifications: textToSpecs(f.specs.value),
  });
  const save = async (e) => {
    e.preventDefault();
    try {
      const r = await call(id === 'new' ? '/api/admin/products' : `/api/admin/products/${id}`, { method: id === 'new' ? 'POST' : 'PUT', body: body() });
      mount(msg, notice('ok', 'Saved.')); if (id === 'new') location.hash = `#/products/${r.id}`;
    } catch (x) { mount(msg, notice('error', x.message + (x.details && x.details.margin_percent !== undefined ? ` (margin would be ${x.details.margin_percent}%)` : ''))); }
  };
  const extra = id === 'new' ? null : h('div', { class: 'card' },
    h('h3', {}, 'Pricing'), h('p', { class: 'small' }, `Landed cost ${money(p.landed_cost, p.currency)} · recommended price ${money(p.recommended_price, p.currency)}`,
      p.economics ? ` · current margin ${p.economics.margin_percent}% · ${p.economics.landed_multiple}× landed` : ''),
    p.status === 'needs_review' && notice('error', `Flagged for review: ${p.review_reason || ''}`),
    h('div', { class: 'actions' },
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: async () => { try { const r = await call(`/api/admin/products/${id}/sync`, { method: 'POST' }); mount(msg, notice('ok', r.result.skipped ? 'Sync skipped (no live supplier API in this mode).' : r.result.error ? `Supplier error: ${r.result.error}` : `Synced. ${r.result.changes.join('; ') || 'No changes.'}`)); } catch (x) { mount(msg, notice('error', x.message)); } } }, 'Sync now'),
      p.status === 'needs_review' && h('button', { class: 'btn btn-green btn-sm', type: 'button', onclick: async () => { try { await call(`/api/admin/products/${id}/approve-review`, { method: 'POST', body: {} }); productForm(id); } catch (x) { mount(msg, notice('error', x.message)); } } }, 'Approve current price'),
      p.status === 'needs_review' && h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: async () => { try { await call(`/api/admin/products/${id}/approve-review`, { method: 'POST', body: { apply_recommended_price: true } }); productForm(id); } catch (x) { mount(msg, notice('error', x.message)); } } }, 'Approve & use recommended price')));
  mount(app, h('h1', {}, id === 'new' ? 'New product' : p.name), nav('#/products'), extra, msg,
    h('form', { onsubmit: save },
      input('name', 'Name', { required: true }), input('slug', 'URL slug', { required: true, pattern: '[a-z0-9]+(-[a-z0-9]+)*' }),
      h('label', { for: 'description' }, 'Description (public)'), f.description,
      h('h2', { class: 'small-title' }, 'Supplier & costs (internal)'),
      h('div', { class: 'cols-2' }, input('supplier_platform', 'Supplier platform'), input('supplier_product_id', 'Supplier product ID')),
      h('div', { class: 'cols-2' }, input('supplier_currency', 'Supplier currency', { maxlength: 3 }), input('supplier_cost', 'Supplier unit cost', { type: 'number', step: '0.01', min: '0' })),
      h('div', { class: 'cols-2' }, input('estimated_shipping_cost', 'Est. shipping cost (store currency)', { type: 'number', step: '0.01', min: '0' }), input('agent_fee', 'Agent fee (store currency)', { type: 'number', step: '0.01', min: '0' })),
      input('other_supplier_costs', 'Other supplier costs (store currency)', { type: 'number', step: '0.01', min: '0' }),
      h('h2', { class: 'small-title' }, 'Storefront'),
      h('div', { class: 'cols-2' }, input('retail_price', 'Retail price', { type: 'number', step: '0.01', min: '0' }), input('market_price', 'Market reference price', { type: 'number', step: '0.01', min: '0' })), h('p', { class: 'small muted' }, 'If the market reference is above the profitable minimum, it becomes the retail price. If it is below the minimum, the profitable minimum wins.'), h('div', { class: 'cols-2' }, input('stock', 'Stock', { type: 'number', min: '0' }), h('div', {})),
      h('label', {}, f.rec, ' Use the recommended price from the pricing engine (overrides retail price)'),
      h('label', { for: 'status' }, 'Status'), f.status,
      h('label', { for: 'images' }, 'Images (https URL | alt text)'), f.images,
      h('label', { for: 'specs' }, 'Specifications'), f.specs,
      h('p', {}, h('button', { class: 'btn btn-green', type: 'submit' }, 'Save')),
      id !== 'new' && h('p', {}, h('button', { class: 'btn btn-danger btn-sm', type: 'button', onclick: async () => { if (confirm('Archive this product? It will disappear from the store.')) { await call(`/api/admin/products/${id}`, { method: 'DELETE' }); location.hash = '#/products'; } } }, 'Archive product'))));
}

// ---------------------------------------------------------------- orders
async function orders() {
  const { orders: rows } = await call('/api/admin/orders');
  mount(app, h('h1', {}, 'Orders'), nav('#/orders'), rows.length ? table(
    ['Order #', 'Customer', 'Product', 'Qty', 'Paid', 'Supplier cost', 'Est. profit', 'Payment', 'Order status', 'Supplier', 'Shipping', 'Tracking', 'Date'],
    rows.map((o) => h('tr', {}, h('td', {}, h('a', { href: `#/orders/${o.id}` }, o.reference), o.needs_review ? h('span', { class: 'badge badge-warn' }, 'review') : null),
      h('td', {}, o.customer), h('td', {}, o.product), h('td', {}, String(o.quantity)), h('td', {}, money(o.total, o.currency)),
      h('td', {}, money(o.supplier_cost)), h('td', {}, money(o.estimated_profit)), h('td', {}, badge(o.payment_status, ['failed', 'review'])), h('td', {}, badge(o.status, ['cancelled', 'refunded'])),
      h('td', {}, badge(o.supplier_status)), h('td', {}, badge(o.shipping_status)), h('td', {}, o.tracking_number || '—'), h('td', {}, new Date(o.created_at).toLocaleDateString()))))
    : h('p', { class: 'muted' }, 'No orders yet.'));
}

async function orderDetail(id) {
  const o = await call(`/api/admin/orders/${id}`);
  const msg = h('div', {});
  const act = (label, cls, fn) => h('button', { class: `btn btn-sm ${cls}`, type: 'button', onclick: async () => {
    try { await fn(); await orderDetail(id); } catch (x) {
      const w = x.details && x.details.warnings;
      mount(msg, notice('error', x.message + (w ? ' ' + w.join(' | ') : '')));
      if (w && confirm(`${w.join('\n')}\n\nApprove anyway?`)) { await call(`/api/admin/orders/${id}/status`, { method: 'PUT', body: { status: 'supplier_approved', override: true } }); orderDetail(id); }
    } } }, label);
  const put = (body) => () => call(`/api/admin/orders/${id}/status`, { method: 'PUT', body });
  const supId = h('input', { id: 'sid', placeholder: '1688 order number', maxlength: 64 });
  const trk = h('input', { id: 'trk', placeholder: 'Tracking number', maxlength: 64, value: o.tracking_number || '' });
  const car = h('input', { id: 'car', placeholder: 'Carrier (optional)', value: o.tracking_carrier || '' });
  const turl = h('input', { id: 'turl', placeholder: 'https:// tracking link (optional)', value: o.tracking_url || '' });
  const s = o.status;
  const kv = (k, v) => [h('dt', {}, k), h('dd', {}, v == null || v === '' ? '—' : v)];
  const a = o.ship_to.address;
  mount(app, h('h1', {}, `Order ${o.reference}`), nav('#/orders'), msg,
    o.needs_review && notice('error', `Needs review: ${o.review_note || ''}`),
    h('div', { class: 'card' }, h('div', { class: 'actions' },
      s === 'awaiting_supplier_approval' && act('Approve supplier order', 'btn-green', put({ status: 'supplier_approved' })),
      s === 'supplier_approved' && [supId, act('Mark supplier order placed', 'btn-green', () => put({ status: 'supplier_ordered', supplier_order_id: supId.value.trim() })())],
      (s === 'supplier_ordered' || s === 'shipped') && [trk, car, turl, act('Save tracking', 'btn-ghost', () => call(`/api/admin/orders/${id}/tracking`, { method: 'PUT', body: { tracking_number: trk.value.trim(), carrier: car.value.trim() || null, url: turl.value.trim() || null } }))],
      s === 'supplier_ordered' && act('Mark shipped', 'btn-green', put({ status: 'shipped' })),
      s === 'shipped' && act('Mark delivered', 'btn-green', put({ status: 'delivered' })),
      ['pending_payment', 'awaiting_supplier_approval', 'supplier_approved', 'supplier_ordered'].includes(s) && act('Cancel' + (o.payment_status === 'paid' ? ' & refund' : ''), 'btn-danger', () => {
        if (!confirm('Cancel this order' + (o.payment_status === 'paid' ? ' and refund the customer' : '') + '?')) return Promise.reject(new Error('Cancelled by you'));
        const body = { status: 'cancelled', refund: o.payment_status === 'paid' };
        if (s === 'supplier_ordered') { if (!confirm('A 1688 order exists. Have you cancelled it on 1688?')) return Promise.reject(new Error('Cancel the 1688 order first')); body.acknowledge_supplier_order = true; }
        return put(body)();
      }),
      o.payment_status === 'paid' && ['shipped', 'delivered'].includes(s) && act('Refund', 'btn-danger', async () => { if (!confirm('Refund the full amount?')) throw new Error('Not refunded'); await call(`/api/admin/orders/${id}/refund`, { method: 'POST', body: {} }); }))),
    h('div', { class: 'grid grid-2' },
      h('div', { class: 'card' }, h('h3', {}, 'Money (internal)'), h('dl', { class: 'kv' },
        kv('Customer paid', money(o.total, o.currency)), kv('Subtotal / shipping', `${money(o.subtotal)} / ${money(o.shipping_cost)}`),
        kv('Supplier + landed cost', money(o.supplier_cost)), kv('Fees & reserves', money(o.fees_and_reserves)), kv('Estimated profit', money(o.estimated_profit)),
        kv('Payment', `${o.payment_status} (${o.payment_provider || '—'})`), kv('Supplier status', o.supplier_status), kv('1688 order #', o.supplier_order_id), kv('Tracking', o.tracking_number))),
      h('div', { class: 'card' }, h('h3', {}, 'Ship to'), h('dl', { class: 'kv' },
        kv('Name', o.ship_to.name), kv('Email', o.email), kv('Address', [a.line1, a.line2].filter(Boolean).join(', ')), kv('City / region', [a.city, a.region].filter(Boolean).join(', ')),
        kv('Postal code', a.postal_code), kv('Country', countryName(o.ship_to.country))))),
    h('h2', { class: 'small-title' }, 'Items'), table(['Product', 'Supplier ID', 'Qty', 'Unit price', 'Unit landed cost'],
      o.items.map((i) => h('tr', {}, h('td', {}, i.name), h('td', {}, i.supplier_product_id || '—'), h('td', {}, String(i.quantity)), h('td', {}, money(i.unit_price)), h('td', {}, money(i.unit_landed_cost))))),
    h('h2', { class: 'small-title' }, 'History'), table(['When', 'Event', 'By', 'Detail'], o.events.map((e) => h('tr', {}, h('td', {}, new Date(e.created_at).toLocaleString()), h('td', {}, e.event), h('td', {}, e.actor), h('td', {}, e.detail || '')))));
}

// ---------------------------------------------------------------- settings
async function settings() {
  const [{ zones }, st] = await Promise.all([call('/api/admin/shipping'), call('/api/admin/settings')]);
  const msg = h('div', {});
  const z = { country: h('input', { id: 'zc', maxlength: 2, placeholder: 'US', required: true }), cost: h('input', { id: 'zcost', type: 'number', step: '0.01', min: '0', required: true }),
    min: h('input', { id: 'zmin', type: 'number', min: '1', placeholder: 'leave empty if unknown' }), max: h('input', { id: 'zmax', type: 'number', min: '1', placeholder: 'leave empty if unknown' }),
    trk: h('input', { type: 'checkbox', id: 'ztrk' }), en: h('input', { type: 'checkbox', id: 'zen', checked: true }) };
  const fill = (x) => { z.country.value = x.country; z.cost.value = x.shipping_cost; z.min.value = x.delivery_days_min ?? ''; z.max.value = x.delivery_days_max ?? ''; z.trk.checked = x.tracking_available; z.en.checked = x.enabled; };
  const sel = h('select', { id: 'rshipby' }, [['', 'Not decided'], ['customer', 'Customer'], ['store', 'Store']].map(([v, l]) => h('option', { value: v, selected: v === st['returns.return_shipping_paid_by'] }, l)));
  const r = { en: h('input', { type: 'checkbox', id: 'ren', checked: st['returns.enabled'] === 'true' }), days: h('input', { id: 'rdays', type: 'number', min: '0', value: st['returns.window_days'] }),
    method: h('input', { id: 'rmethod', value: st['returns.refund_method'] }), text: h('textarea', { id: 'rtext', rows: 6 }, st['returns.policy_text']) };
  mount(app, h('h1', {}, 'Shipping & policies'), nav('#/settings'), msg,
    h('h2', { class: 'small-title' }, 'Shipping zones'),
    h('p', { class: 'small muted' }, 'Customers can only order to enabled countries. Leave delivery days empty unless you have a real estimate - the storefront then says "to be confirmed".'),
    zones.length ? table(['Country', 'Customer pays', 'Delivery days', 'Tracking', 'Enabled', ''], zones.map((x) => h('tr', {}, h('td', {}, `${countryName(x.country)} (${x.country})`), h('td', {}, money(x.shipping_cost)),
      h('td', {}, x.delivery_days_min ? `${x.delivery_days_min}–${x.delivery_days_max || x.delivery_days_min}` : 'not set'), h('td', {}, x.tracking_available ? 'yes' : 'no'), h('td', {}, x.enabled ? 'yes' : 'no'),
      h('td', {}, h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => fill(x) }, 'Edit'), ' ', h('button', { class: 'btn btn-danger btn-sm', type: 'button', onclick: async () => { await call(`/api/admin/shipping/${x.country}`, { method: 'DELETE' }); settings(); } }, 'Delete'))))) : h('p', { class: 'muted' }, 'No zones yet.'),
    h('form', { onsubmit: async (e) => { e.preventDefault(); try { await call(`/api/admin/shipping/${z.country.value.trim().toUpperCase()}`, { method: 'PUT', body: { shipping_cost: z.cost.value, delivery_days_min: z.min.value ? +z.min.value : null, delivery_days_max: z.max.value ? +z.max.value : null, tracking_available: z.trk.checked, enabled: z.en.checked } }); settings(); } catch (x) { mount(msg, notice('error', x.message)); } } },
      h('div', { class: 'cols-2' }, h('div', {}, h('label', { for: 'zc' }, 'Country code (ISO-2)'), z.country), h('div', {}, h('label', { for: 'zcost' }, 'Shipping charged to customer'), z.cost)),
      h('div', { class: 'cols-2' }, h('div', {}, h('label', { for: 'zmin' }, 'Min delivery days'), z.min), h('div', {}, h('label', { for: 'zmax' }, 'Max delivery days'), z.max)),
      h('label', {}, z.trk, ' Tracking available'), h('label', {}, z.en, ' Enabled (customers can order to this country)'), h('p', {}, h('button', { class: 'btn btn-green', type: 'submit' }, 'Save zone'))),
    h('h2', { class: 'small-title' }, 'Returns & refunds (placeholders until you decide)'),
    h('form', { onsubmit: async (e) => { e.preventDefault(); try { await call('/api/admin/settings', { method: 'PUT', body: { returns_enabled: r.en.checked, returns_window_days: r.days.value ? +r.days.value : 0, returns_return_shipping_paid_by: sel.value, returns_refund_method: r.method.value, returns_policy_text: r.text.value } }); mount(msg, notice('ok', 'Saved.')); } catch (x) { mount(msg, notice('error', x.message)); } } },
      h('label', {}, r.en, ' Offer returns'), h('label', { for: 'rdays' }, 'Return window (days)'), r.days, h('label', { for: 'rshipby' }, 'Return shipping paid by'), sel,
      h('label', { for: 'rmethod' }, 'Refund method'), r.method, h('label', { for: 'rtext' }, 'Policy text shown to customers (get legal review)'), r.text, h('p', {}, h('button', { class: 'btn btn-green', type: 'submit' }, 'Save policy'))));
}

// ---------------------------------------------------------------- router
async function route() {
  if (!getToken()) { login(); return; }
  logoutBtn.classList.remove('hidden');
  const hash = location.hash || '#/';
  mount(app, h('p', { class: 'muted' }, 'Loading…'));
  try {
    if (hash === '#/login') location.hash = '#/';
    else if (hash === '#/') await dashboard();
    else if (hash === '#/products') await products();
    else if (hash.startsWith('#/products/')) await productForm(hash.split('/')[2]);
    else if (hash === '#/orders') await orders();
    else if (hash.startsWith('#/orders/')) await orderDetail(hash.split('/')[2]);
    else if (hash === '#/settings') await settings();
    else mount(app, h('p', {}, 'Not found'));
  } catch (e) { if (e.status !== 401) mount(app, notice('error', e.message)); }
}
logoutBtn.addEventListener('click', () => { setToken(''); location.hash = '#/login'; route(); });
window.addEventListener('hashchange', route);
route();
