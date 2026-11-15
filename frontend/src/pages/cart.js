import { h, mount, $ } from '../components/dom.js';
import { renderLayout } from '../components/layout.js';
import { api } from '../services/api.js';
import { getCart, setQuantity, getCountry } from '../services/cart.js';
import { money } from '../services/format.js';

async function draw() {
  const app = $('#app');
  const items = getCart();
  if (!items.length) {
    mount(app, h('div', { class: 'container narrow' }, h('h1', {}, 'Your cart'), h('p', { class: 'muted' }, 'Your cart is empty.'), h('a', { class: 'btn btn-green', href: '/#shop' }, 'Continue shopping')));
    return;
  }
  const country = getCountry();
  let quote = null, error = null;
  if (country) {
    try { quote = await api('/api/checkout/quote', { method: 'POST', body: { items, country } }); }
    catch (e) { error = e.message; }
  }
  const rows = quote ? quote.items.map((i) => h('div', { class: 'card' },
    h('div', { class: 'row' }, h('strong', {}, i.name), h('span', {}, money(i.line_total, quote.currency))),
    h('div', { class: 'field-inline' }, h('div', { class: 'qty' },
      h('button', { type: 'button', 'aria-label': `Decrease ${i.name}`, onclick: () => { setQuantity(i.slug, i.quantity - 1); draw(); } }, '−'), h('span', {}, String(i.quantity)),
      h('button', { type: 'button', 'aria-label': `Increase ${i.name}`, onclick: () => { setQuantity(i.slug, i.quantity + 1); draw(); } }, '+')),
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => { setQuantity(i.slug, 0); draw(); } }, 'Remove')),
    h('p', { class: 'small muted' }, `${money(i.unit_price, quote.currency)} each`)))
    : items.map((i) => h('div', { class: 'card' }, h('div', { class: 'row' }, h('strong', {}, i.slug), h('span', {}, `× ${i.quantity}`)),
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => { setQuantity(i.slug, 0); draw(); } }, 'Remove')));
  mount(app, h('div', { class: 'container narrow' }, h('h1', {}, 'Your cart'),
    error && h('p', { class: 'notice notice-error', role: 'alert' }, error),
    !country && h('p', { class: 'notice' }, 'Choose your shipping country at checkout to see the total.'),
    h('div', { class: 'grid' }, rows),
    quote && h('div', { class: 'card' },
      h('div', { class: 'row' }, h('span', {}, 'Subtotal'), h('span', {}, money(quote.subtotal, quote.currency))),
      h('div', { class: 'row' }, h('span', {}, 'Shipping'), h('span', {}, money(quote.shipping_cost, quote.currency))),
      h('div', { class: 'row total' }, h('span', {}, 'Total'), h('span', {}, money(quote.total, quote.currency)))),
    h('p', {}, h('a', { class: 'btn btn-primary btn-block', href: '/checkout' }, 'Checkout'))));
}
renderLayout();
draw();
