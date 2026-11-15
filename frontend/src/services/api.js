import { API_BASE } from '../config/config.js';

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status; this.code = code; this.details = details;
  }
}

export async function api(path, { method = 'GET', body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(API_BASE + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, 'network', 'Network problem. Please check your connection and try again.');
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const e = (data && data.error) || {};
    throw new ApiError(res.status, e.code || 'error', e.message || 'Something went wrong.', e.details);
  }
  return data;
}

let configCache;
export const getConfig = () => (configCache ||= api('/api/config'));
export const getProducts = () => api('/api/products').then((d) => d.products);
export const getProduct = (slug) => api(`/api/products/${encodeURIComponent(slug)}`);
export const getCountries = () => api('/api/shipping/countries').then((d) => d.countries);
export const getPolicies = () => api('/api/policies');
