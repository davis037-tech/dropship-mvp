import { h, mount, $ } from '../components/dom.js';
import { renderLayout } from '../components/layout.js';
import { api } from '../services/api.js';
import { dateText, money } from '../services/format.js';

const LABEL = { awaiting_payment: 'Awaiting payment', preparing: 'Preparing your order', shipped: 'Shipped', delivered: 'Delivered', cancelled: 'Cancelled', refunded: 'Refunded' };

async function main() {
  renderLayout();
  const app = $('#app');
  const q = new URLSearchParams(location.search);
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem('last.order') || '{}'); } catch { /* ignore */ }
  const result = h('div', { 'aria-live': 'polite' });
  const ref = h('input', { id: 'ref', name: 'ref', required: true, value: q.get('ref') || saved.ref || '', autocomplete: 'off', maxlength: 30 });
  const email = h('input', { id: 'email', name: 'email', type: 'email', value: saved.email || '', autocomplete: 'email' });
  const token = q.get('t') || '';

  async function lookup(useToken) {
    const params = new URLSearchParams(useToken ? { token } : { email: email.value.trim() });
    try {
      const o = await api(`/api/orders/${encodeURIComponent(ref.value.trim())}/status?${params}`);
      mount(result, h('div', { class: 'card' },
        h('h2', { class: 'small-title' }, `Order ${o.reference}`),
        h('p', {}, h('span', { class: `badge ${o.status === 'cancelled' || o.status === 'refunded' ? 'badge-bad' : ''}` }, LABEL[o.status] || o.status), ' ', h('span', { class: 'muted small' }, dateText(o.created_at))),
        o.status === 'awaiting_payment' && h('p', { class: 'notice' }, 'We have not received your payment confirmation yet. This page updates once the payment provider confirms it - try refreshing in a moment.'),
        h('ol', { class: 'timeline' }, o.timeline.map((s) => h('li', { class: s.done ? 'done' : '' }, s.label))),
        o.tracking ? h('div', {}, h('h3', {}, 'Tracking information'), h('p', {}, o.tracking.carrier ? `${o.tracking.carrier}: ` : '', o.tracking.url ? h('a', { href: o.tracking.url, rel: 'noopener noreferrer' }, o.tracking.number) : o.tracking.number))
          : h('p', { class: 'muted small' }, 'Tracking information will appear here once your order ships (where available).'),
        o.items.map((i) => h('div', { class: 'row' }, h('span', {}, `${i.name} × ${i.quantity}`), h('span', {}, money(i.unit_price * i.quantity, o.currency)))),
        h('div', { class: 'row total' }, h('span', {}, 'Total'), h('span', {}, money(o.total, o.currency)))));
    } catch (e) { mount(result, h('p', { class: 'notice notice-error', role: 'alert' }, e.message)); }
  }

  mount(app, h('div', { class: 'container narrow' }, h('h1', {}, 'Order status'),
    h('form', { onsubmit: (ev) => { ev.preventDefault(); lookup(false); } },
      h('label', { for: 'ref' }, 'Order number'), ref, h('label', { for: 'email' }, 'Email used at checkout'), email,
      h('p', {}, h('button', { class: 'btn btn-green', type: 'submit' }, 'Check status'))), result));
  if (token && ref.value) lookup(true);
}
main();
