import { h, mount, $ } from '../components/dom.js';
import { renderLayout } from '../components/layout.js';
import { productImage } from '../components/media.js';
import { getProduct, getCountries } from '../services/api.js';
import { addToCart, getCountry, setCountry } from '../services/cart.js';
import { countryName, deliveryText, money } from '../services/format.js';
import { isLoggedIn } from '../services/customer.js';

async function main() {
  renderLayout();
  const app = $('#app');
  const slug = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
  let p, countries;
  try { [p, countries] = await Promise.all([getProduct(slug), getCountries()]); }
  catch (e) {
    mount(app, h('div', { class: 'container', }, h('h1', {}, e.status === 404 ? 'Product not found' : 'Something went wrong'),
      h('p', { class: 'muted' }, e.message), h('a', { class: 'btn btn-green', href: '/#shop' }, 'Back to shop')));
    return;
  }
  let qty = 1, current = 0;
  const gallery = h('div', {});
  const drawGallery = () => {
    gallery.replaceChildren(productImage(p, current, { eager: true }),
      p.images.length > 1 && h('div', { class: 'thumbs' }, p.images.map((img, i) => h('button', { type: 'button', 'aria-label': `Show image ${i + 1}`, 'aria-current': String(i === current),
        onclick: () => { current = i; drawGallery(); } }, h('img', { src: img.url, alt: '', loading: 'lazy', width: 64, height: 64 })))));
  };
  drawGallery();

  const qtyEl = h('span', { 'aria-live': 'polite' }, '1');
  const setQty = (n) => { qty = Math.max(1, Math.min(p.max_quantity || 1, n)); qtyEl.textContent = String(qty); };
  const msg = h('p', { 'aria-live': 'polite', class: 'small' });
  const select = h('select', { id: 'country' }, h('option', { value: '' }, 'Select country'), countries.map((c) => h('option', { value: c.country }, countryName(c.country))));
  const shipEl = h('p', { class: 'ship-note', 'aria-live': 'polite' });
  const refresh = () => {
    const z = countries.find((c) => c.country === select.value);
    shipEl.textContent = !countries.length ? 'Shipping information is not available yet.'
      : z ? `Shipping to ${countryName(z.country)}: ${money(z.shipping_cost, p.currency)} · ${deliveryText(z)}` : 'Choose your country to see shipping details.';
  };
  select.value = countries.some((c) => c.country === getCountry()) ? getCountry() : '';
  select.addEventListener('change', () => { setCountry(select.value); refresh(); });
  refresh();

  const loginUrl = '/login?next=' + encodeURIComponent(location.pathname);
  const buy = p.available ? (isLoggedIn() ? h('div', {},
    h('div', { class: 'field-inline' }, h('span', { class: 'small' }, 'Quantity'),
      h('div', { class: 'qty' }, h('button', { type: 'button', 'aria-label': 'Decrease quantity', onclick: () => setQty(qty - 1) }, '−'), qtyEl,
        h('button', { type: 'button', 'aria-label': 'Increase quantity', onclick: () => setQty(qty + 1) }, '+'))),
    h('div', { class: 'actions' },
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => { addToCart(p.slug, qty, p.max_quantity); msg.textContent = 'Added to your cart.'; } }, 'Add to cart'),
      h('button', { class: 'btn btn-green', type: 'button', onclick: () => { addToCart(p.slug, qty, p.max_quantity); location.href = '/checkout'; } }, 'Buy now')), msg)
    : h('div', { class: 'notice' }, 'Please log in before adding this product to your cart or buying it. ', h('a', { href: loginUrl }, 'Log in')))
    : h('p', { class: 'notice' }, 'This item is currently unavailable. Please check back soon.');

  document.title = `${p.name} | ${document.title.split('|').pop().trim()}`;
  mount(app, h('div', { class: 'container product-grid' }, gallery, h('div', {},
    h('h1', {}, p.name), h('p', { class: 'price' }, money(p.price, p.currency)),
    p.description.split(/\n{2,}/).map((para) => h('p', { class: 'muted' }, para)),
    h('div', { class: 'field-inline' }, h('label', { for: 'country', class: 'small' }, 'Ship to'), select), shipEl, buy,
    p.specifications.length > 0 && h('table', { class: 'specs' }, h('caption', { class: 'sr-only' }, 'Specifications'),
      h('tbody', {}, p.specifications.map((s) => h('tr', {}, h('th', { scope: 'row' }, s.label), h('td', {}, s.value))))),
    h('p', { class: 'notice' }, h('strong', {}, 'Supervision required. '), 'Always supervise your dog while they use this product and remove it if they chew or swallow pieces.'))));
}
main();
