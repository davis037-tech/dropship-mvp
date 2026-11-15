import { h } from './dom.js';

// Static, constant markup (no data interpolated) - safe to assign as innerHTML.
const FLOWERS = '<svg viewBox="0 0 96 96" aria-hidden="true" fill="none"><g fill="#e9b44c"><circle cx="30" cy="32" r="9"/><circle cx="66" cy="28" r="8"/><circle cx="48" cy="58" r="10"/></g><g fill="#fff"><circle cx="30" cy="32" r="3.5"/><circle cx="66" cy="28" r="3"/><circle cx="48" cy="58" r="4"/></g><g stroke="#4d7550" stroke-width="3" stroke-linecap="round"><path d="M30 41v34M66 36v39M48 68v14"/></g><path d="M12 82h72" stroke="#4d7550" stroke-width="3" stroke-linecap="round"/></svg>';

export function placeholder() {
  const el = h('div', { class: 'ph', role: 'img', 'aria-label': 'Product photo placeholder' });
  el.innerHTML = FLOWERS;
  el.append(h('div', {}, h('small', {}, 'Product photo placeholder'), h('span', { class: 'small' }, 'Real photos appear here once added')));
  return el;
}

export function productImage(product, index = 0, { eager = false } = {}) {
  const img = product.images && product.images[index];
  const box = h('div', { class: 'media' });
  if (!img || !img.url) { box.append(placeholder()); return box; }
  box.append(h('img', { src: img.url, alt: img.alt || product.name, width: 800, height: 800,
    loading: eager ? 'eager' : 'lazy', decoding: 'async', fetchpriority: eager ? 'high' : null }));
  return box;
}
