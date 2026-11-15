import { h, mount, $ } from '../components/dom.js';
import { renderLayout } from '../components/layout.js';
import { api, getCountries } from '../services/api.js';
import { getCart, clearCart, getCountry, setCountry } from '../services/cart.js';
import { countryName, deliveryText, money } from '../services/format.js';

const field = (label, name, attrs = {}) => h('div', {}, h('label', { for: name }, label), h('input', { id: name, name, ...attrs }));

async function main() {
  renderLayout();
  const app = $('#app');
  const items = getCart();
  if (!items.length) { mount(app, h('div', { class: 'container narrow' }, h('h1', {}, 'Checkout'), h('p', { class: 'muted' }, 'Your cart is empty.'), h('a', { class: 'btn btn-green', href: '/#shop' }, 'Shop')) ); return; }
  let countries = [];
  try { countries = await getCountries(); } catch (e) { mount(app, h('div', { class: 'container narrow' }, h('p', { class: 'notice notice-error' }, e.message))); return; }
  if (!countries.length) { mount(app, h('div', { class: 'container narrow' }, h('h1', {}, 'Checkout'), h('p', { class: 'notice' }, 'We are not shipping anywhere yet. Please check back soon.'))); return; }

  const summary = h('div', { class: 'card', 'aria-live': 'polite' }, h('p', { class: 'muted' }, 'Select a country to see your total.'));
  const errorBox = h('p', { class: 'notice notice-error hidden', role: 'alert' });
  const country = h('select', { id: 'country', name: 'country', required: true, autocomplete: 'country' }, h('option', { value: '' }, 'Select country'),
    countries.map((c) => h('option', { value: c.country }, countryName(c.country))));
  country.value = countries.some((c) => c.country === getCountry()) ? getCountry() : '';
  const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit', disabled: true }, 'Continue to secure payment');

  const showError = (m) => { errorBox.textContent = m; errorBox.classList.toggle('hidden', !m); if (m) errorBox.scrollIntoView({ block: 'center' }); };

  async function updateQuote() {
    showError('');
    if (!country.value) { submit.disabled = true; return; }
    setCountry(country.value);
    try {
      const q = await api('/api/checkout/quote', { method: 'POST', body: { items, country: country.value } });
      const z = q.shipping;
      mount(summary, h('h2', { class: 'small-title' }, 'Order summary'),
        q.items.map((i) => h('div', { class: 'row' }, h('span', {}, `${i.name} × ${i.quantity}`), h('span', {}, money(i.line_total, q.currency)))),
        h('div', { class: 'row' }, h('span', {}, 'Shipping'), h('span', {}, money(q.shipping_cost, q.currency))),
        h('p', { class: 'small muted' }, deliveryText(z)),
        h('div', { class: 'row total' }, h('span', {}, 'Total'), h('span', {}, money(q.total, q.currency))));
      submit.disabled = false;
    } catch (e) { submit.disabled = true; mount(summary, h('p', { class: 'muted' }, 'Totals unavailable.')); showError(e.message); }
  }
  country.addEventListener('change', updateQuote);

  const form = h('form', { novalidate: false, onsubmit: async (ev) => {
    ev.preventDefault();
    showError(''); submit.disabled = true; submit.textContent = 'Please wait…';
    const f = new FormData(form);
    const val = (k) => (f.get(k) || '').toString().trim();
    try {
      const res = await api('/api/checkout', { method: 'POST', body: {
        items, country: val('country'),
        customer: { name: val('name'), email: val('email') },
        address: { line1: val('line1'), line2: val('line2') || null, city: val('city'), region: val('region') || null, postal_code: val('postal_code') },
      } });
      try { sessionStorage.setItem('last.order', JSON.stringify({ ref: res.order_reference, email: val('email') })); } catch { /* ignore */ }
      clearCart();
      location.assign(res.redirect_url);
    } catch (e) {
      const d = e.details;
      showError(e.code === 'validation_error' && Array.isArray(d) ? 'Please check: ' + d.map((x) => x.field).join(', ') : e.message);
      submit.disabled = false; submit.textContent = 'Continue to secure payment';
    }
  } },
    h('h2', { class: 'small-title' }, 'Contact'),
    field('Full name', 'name', { required: true, maxlength: 100, autocomplete: 'name' }),
    field('Email', 'email', { type: 'email', required: true, maxlength: 254, autocomplete: 'email' }),
    h('h2', { class: 'small-title' }, 'Shipping address'),
    h('div', {}, h('label', { for: 'country' }, 'Country'), country),
    field('Address', 'line1', { required: true, maxlength: 120, autocomplete: 'address-line1' }),
    field('Apartment, suite, etc. (optional)', 'line2', { maxlength: 120, autocomplete: 'address-line2' }),
    h('div', { class: 'cols-2' }, field('City', 'city', { required: true, maxlength: 80, autocomplete: 'address-level2' }), field('State / region (optional)', 'region', { maxlength: 80, autocomplete: 'address-level1' })),
    field('Postal code', 'postal_code', { required: true, maxlength: 20, autocomplete: 'postal-code' }),
    h('p', { class: 'small muted' }, 'You will be redirected to our payment provider to pay securely. We never see or store your card details.'),
    errorBox, summary, h('p', {}, submit));
  mount(app, h('div', { class: 'container narrow' }, h('h1', {}, 'Checkout'), form));
  updateQuote();
}
main();
