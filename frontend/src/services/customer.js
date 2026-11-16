import { api } from './api.js';

const TOKEN_KEY = 'customer.token';
const CUSTOMER_KEY = 'customer.profile';

export const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
export const setSession = (token, customer) => {
  try { sessionStorage.setItem(TOKEN_KEY, token); sessionStorage.setItem(CUSTOMER_KEY, JSON.stringify(customer)); } catch {}
};
export const clearSession = () => {
  try { sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(CUSTOMER_KEY); } catch {}
};
export const getCustomer = () => {
  try { return JSON.parse(sessionStorage.getItem(CUSTOMER_KEY) || 'null'); } catch { return null; }
};
export const isLoggedIn = () => Boolean(getToken());
export const authApi = (path, opts = {}) => api(path, { ...opts, token: getToken() });
