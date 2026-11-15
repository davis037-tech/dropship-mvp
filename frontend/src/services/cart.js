// The cart stores ONLY product slugs and quantities. Prices always come from the server.
import { CART_KEY, COUNTRY_KEY } from '../config/config.js';

const read = () => {
  try {
    const v = JSON.parse(localStorage.getItem(CART_KEY) || '[]');
    return Array.isArray(v) ? v.filter((i) => typeof i.slug === 'string' && Number.isInteger(i.quantity) && i.quantity > 0) : [];
  } catch { return []; }
};
const write = (items) => {
  try { localStorage.setItem(CART_KEY, JSON.stringify(items)); } catch { /* storage unavailable */ }
  document.dispatchEvent(new CustomEvent('cart:change'));
};

export const getCart = read;
export const cartCount = () => read().reduce((n, i) => n + i.quantity, 0);
export function addToCart(slug, quantity = 1, max = 5) {
  const items = read();
  const found = items.find((i) => i.slug === slug);
  if (found) found.quantity = Math.min(max, found.quantity + quantity); else items.push({ slug, quantity: Math.min(max, quantity) });
  write(items);
}
export function setQuantity(slug, quantity) {
  write(read().map((i) => (i.slug === slug ? { ...i, quantity } : i)).filter((i) => i.quantity > 0));
}
export const clearCart = () => write([]);

export const getCountry = () => { try { return localStorage.getItem(COUNTRY_KEY) || ''; } catch { return ''; } };
export const setCountry = (c) => { try { localStorage.setItem(COUNTRY_KEY, c); } catch { /* ignore */ } };
