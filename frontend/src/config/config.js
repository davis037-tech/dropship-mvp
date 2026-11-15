// Public, non-secret frontend configuration. No keys or credentials ever belong here.
// The API is same-origin by default; set <meta name="api-base" content="https://api.example.com"> to override.
const meta = document.querySelector('meta[name="api-base"]');
export const API_BASE = (meta && meta.content) || '';
export const CART_KEY = 'cart.v1';
export const COUNTRY_KEY = 'ship.country';
