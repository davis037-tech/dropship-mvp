import { h, $ } from './dom.js';
import { cartCount } from '../services/cart.js';
import { getConfig, getPolicies } from '../services/api.js';
import { getCustomer, clearSession, isLoggedIn } from '../services/customer.js';

const LOGO = '<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="12" r="5" fill="#e9b44c"/><circle cx="16" cy="12" r="2" fill="#fff"/><path d="M16 17v11M16 23c-5 0-7-3-7-6 4 0 7 2 7 6zM16 21c4 0 6-2 6-5-4 0-6 2-6 5z" fill="#4d7550"/></svg>';

export async function renderLayout() {
  const cfg = await getConfig().catch(() => ({ store_name: 'Store' }));
  const header = $('#site-header');
  if (header) {
    const count = h('span', { class: 'cart-count', 'aria-hidden': 'true' }, String(cartCount()));
    const link = h('a', { class: 'cart-link', href: '/cart', 'aria-label': 'Cart' }, 'Cart ', count);
    const customer = getCustomer();
    const account = isLoggedIn()
      ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => { clearSession(); location.reload(); } }, customer && customer.name ? `Log out (${customer.name})` : 'Log out')
      : h('a', { class: 'btn btn-ghost btn-sm', href: '/login' }, 'Log in');
    const nav = h('nav', { class: 'nav', id: 'nav', 'aria-label': 'Main' },
      h('a', { href: '/#shop' }, 'Shop'), h('a', { href: '/#how-it-works' }, 'How It Works'),
      h('a', { href: '/#benefits' }, 'Benefits'), h('a', { href: '/#faq' }, 'FAQ'));
    const brand = h('a', { class: 'brand', href: '/' }, cfg.store_name);
    brand.insertAdjacentHTML('afterbegin', LOGO);
    const btn = h('button', { class: 'menu-btn', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'nav',
      onclick: () => { const open = nav.classList.toggle('open'); btn.setAttribute('aria-expanded', String(open)); } }, 'Menu');
    nav.addEventListener('click', (e) => { if (e.target.tagName === 'A') nav.classList.remove('open'); });
    header.className = 'site-header';
    header.append(h('div', { class: 'container bar' }, brand, nav, link, account,
      h('a', { class: 'btn btn-primary btn-sm header-cta', href: '/#shop' }, 'Shop Now'), btn));
    document.addEventListener('cart:change', () => { count.textContent = String(cartCount()); });
  }
  const footer = $('#site-footer');
  if (footer) {
    footer.className = 'site-footer';
    const ret = h('p', { class: 'small', id: 'footer-returns' }, 'Returns & refunds: loading…');
    footer.append(h('div', { class: 'container grid grid-3' },
      h('div', {}, h('h3', {}, cfg.store_name), h('p', { class: 'small' }, 'Enrichment feeding accessories for dogs.')),
      h('div', {}, h('h3', {}, 'Help'), h('p', { class: 'small' }, h('a', { href: '/order' }, 'Track your order'), h('br'), h('a', { href: '/#faq' }, 'FAQ')), ret),
      h('div', {}, h('h3', {}, 'Please note'),
        h('p', { class: 'small' }, 'Always supervise your dog while they use any feeding or enrichment product. Product information is for general guidance and is not veterinary advice.'))));
    getPolicies().then((p) => { ret.textContent = 'Returns & refunds: ' + p.returns.policy_text; }).catch(() => { ret.textContent = ''; });
    if (cfg.payment_test_mode) footer.append(h('div', { class: 'container' }, h('p', { class: 'small' }, h('span', { class: 'badge-test' }, 'TEST MODE'), ' Payments are simulated - no real money is taken.')));
  }
}
