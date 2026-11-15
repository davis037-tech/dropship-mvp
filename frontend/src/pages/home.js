import { h, mount, $ } from '../components/dom.js';
import { renderLayout } from '../components/layout.js';
import { productImage } from '../components/media.js';
import { getConfig, getProducts, getCountries, getPolicies } from '../services/api.js';
import { getCountry, setCountry } from '../services/cart.js';
import { countryName, deliveryText, money } from '../services/format.js';

const BENEFITS = [
  ['Natural sniffing', 'Dogs explore the world with their noses. Hiding food in the mat invites them to do just that.'],
  ['Interactive feeding', 'Mealtime becomes something to do, not just a bowl to empty.'],
  ['Everyday enrichment', 'A simple way to give your dog something to investigate and figure out.'],
  ['Searching', 'Scatter kibble or treats and let your dog hunt for each piece.'],
  ['Exploration', 'New textures and hiding spots to discover, at your dog’s own pace.'],
  ['Indoor activity', 'An easy activity for rainy days, small spaces or quiet evenings.'],
];
const STEPS = [
  ['Scatter', 'Spread a little kibble or a few treats across the mat.'],
  ['Sniff & search', 'Let your dog find the food using their nose.'],
  ['Supervise', 'Stay with your dog while they play and put the mat away afterwards.'],
];

async function main() {
  renderLayout();
  const app = $('#app');
  let cfg, products, countries;
  try { [cfg, products, countries] = await Promise.all([getConfig(), getProducts(), getCountries()]); }
  catch (e) { mount(app, h('div', { class: 'container section-pad' }, h('p', { class: 'notice notice-error' }, e.message))); return; }

  const featured = products[0];
  const fmt = (v) => money(v, featured ? featured.currency : cfg.currency);
  const shipEl = h('p', { class: 'ship-note', id: 'ship-note', 'aria-live': 'polite' });
  const select = h('select', { id: 'country', 'aria-label': 'Shipping destination' }, h('option', { value: '' }, 'Select country'),
    countries.map((c) => h('option', { value: c.country }, countryName(c.country))));
  const refresh = () => {
    const z = countries.find((c) => c.country === select.value);
    shipEl.textContent = !countries.length ? 'Shipping information is not available yet.'
      : z ? `Shipping to ${countryName(z.country)}: ${z.shipping_cost === 0 ? 'free' : fmt(z.shipping_cost)} · ${deliveryText(z)}`
        : 'Choose your country to see shipping details.';
  };
  select.value = countries.some((c) => c.country === getCountry()) ? getCountry() : '';
  select.addEventListener('change', () => { setCountry(select.value); refresh(); });
  refresh();

  const hero = h('section', { class: 'hero' }, h('div', { class: 'container hero-grid' },
    h('div', {},
      h('p', { class: 'eyebrow' }, 'Enrichment for dogs'),
      h('h1', {}, 'Turn Mealtime Into an Adventure.'),
      h('p', { class: 'muted' }, 'A simple way to turn everyday feeding into an engaging sniff-and-search activity for dogs.'),
      h('a', { class: 'btn btn-primary', href: featured ? `/product/${featured.slug}` : '#shop' }, 'Shop Now')),
    featured ? h('div', { class: 'hero-card' }, productImage(featured, 0, { eager: true }),
      h('h2', { class: 'small-title' }, featured.name),
      h('p', { class: 'price' }, fmt(featured.price)),
      !featured.available && h('p', { class: 'notice' }, 'Currently unavailable.'),
      h('div', { class: 'field-inline' }, h('label', { for: 'country', class: 'small' }, 'Ship to'), select),
      shipEl,
      h('p', {}, h('a', { class: 'btn btn-green btn-block', href: `/product/${featured.slug}` }, 'View details')))
      : h('div', { class: 'hero-card' }, h('p', {}, 'Our first product is coming soon.'))));

  const shop = h('section', { id: 'shop' }, h('div', { class: 'container' },
    h('h2', {}, 'Shop'),
    products.length ? h('div', { class: 'grid grid-3' }, products.map((p) => h('a', { class: 'card product-card', href: `/product/${p.slug}` },
      productImage(p), h('h3', {}, p.name), h('span', { class: 'price' }, fmt(p.price)),
      !p.available && h('span', { class: 'badge badge-warn' }, 'Currently unavailable'))))
      : h('p', { class: 'muted' }, 'No products available right now.')));

  const how = h('section', { id: 'how-it-works', class: 'section-alt' }, h('div', { class: 'container' },
    h('h2', {}, 'How it works'),
    h('div', { class: 'grid grid-3' }, STEPS.map(([t, d], i) => h('div', { class: 'card' }, h('span', { class: 'step-num' }, String(i + 1)), h('h3', {}, t), h('p', { class: 'muted' }, d))))));

  const benefits = h('section', { id: 'benefits' }, h('div', { class: 'container' },
    h('h2', {}, 'Why dogs (and their people) enjoy it'),
    h('div', { class: 'grid grid-3' }, BENEFITS.map(([t, d]) => h('div', { class: 'card' }, h('h3', {}, t), h('p', { class: 'muted' }, d)))),
    h('p', { class: 'notice' }, h('strong', {}, 'Supervision required. '), 'Always watch your dog while they use the mat. Remove it if your dog chews or tries to swallow pieces. This product is not a substitute for veterinary or training advice.')));

  const faqShip = h('p', {}, 'Loading…');
  const faqReturns = h('p', {}, 'Loading…');
  const faq = h('section', { id: 'faq', class: 'section-alt' }, h('div', { class: 'container narrow' },
    h('h2', {}, 'Questions'),
    h('details', {}, h('summary', {}, 'What is a snuffle mat?'), h('p', {}, 'A feeding mat you hide kibble or treats in, so your dog uses their nose to find them.')),
    h('details', {}, h('summary', {}, 'How do I use it?'), h('p', {}, 'Scatter a small amount of food on or in the mat and let your dog search. Start simple and supervise throughout.')),
    h('details', {}, h('summary', {}, 'Is it safe to leave with my dog?'), h('p', {}, 'No. Use it only while you are watching, and take it away when finished.')),
    h('details', {}, h('summary', {}, 'Where do you ship and how long does it take?'), faqShip),
    h('details', {}, h('summary', {}, 'How do I track my order?'), h('p', {}, 'Use the link shown after checkout, or enter your order number and email on the ', h('a', { href: '/order' }, 'order status page'), '. Tracking details appear there once your order ships (where tracking is available).')),
    h('details', {}, h('summary', {}, 'What is your returns policy?'), faqReturns)));

  mount(app, hero, shop, how, benefits, faq);
  faqShip.textContent = countries.length
    ? 'We currently ship to: ' + countries.map((c) => countryName(c.country)).join(', ') + '. Choose your country at the top of the page to see the cost and delivery details.'
    : 'Shipping destinations are not available yet.';
  getPolicies().then((p) => { faqReturns.textContent = p.returns.policy_text; }).catch(() => { faqReturns.textContent = ''; });
}
main();
