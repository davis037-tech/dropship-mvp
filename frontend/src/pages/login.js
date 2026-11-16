import { h, mount, $ } from '../components/dom.js';
import { api } from '../services/api.js';
import { setSession } from '../services/customer.js';

async function main() {
  const app = $('#app');
  const q = new URLSearchParams(location.search);
  const next = q.get('next') || '/';
  const mode = q.get('mode') === 'register' ? 'register' : 'login';
  const msg = h('div', {});
  const email = h('input', { id: 'email', type: 'email', required: true, autocomplete: 'email' });
  const name = h('input', { id: 'name', type: 'text', required: true, autocomplete: 'name', maxlength: 100 });
  const password = h('input', { id: 'password', type: 'password', required: true, minlength: 8, autocomplete: mode === 'register' ? 'new-password' : 'current-password' });
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault();
    const body = { email: email.value.trim(), password: password.value };
    if (mode === 'register') body.name = name.value.trim();
    try {
      const res = await api(mode === 'register' ? '/api/customer/register' : '/api/customer/login', { method: 'POST', body });
      setSession(res.token, res.customer);
      location.assign(next.startsWith('/') ? next : '/');
    } catch (x) { mount(msg, h('p', { class: 'notice notice-error' }, x.message)); }
  } },
    mode === 'register' ? h('div', {}, h('label', { for: 'name' }, 'Full name'), name) : null,
    h('label', { for: 'email' }, 'Email'), email,
    h('label', { for: 'password' }, 'Password'), password,
    mode === 'register' ? h('p', { class: 'small muted' }, 'Use at least 8 characters.') : null,
    msg, h('p', {}, h('button', { class: 'btn btn-green btn-block', type: 'submit' }, mode === 'register' ? 'Create account' : 'Log in'))
  );
  const loginHref = '/login?next=' + encodeURIComponent(next);
  const registerHref = '/login?mode=register&next=' + encodeURIComponent(next);
  mount(app, h('div', { class: 'container narrow' },
    h('h1', {}, mode === 'register' ? 'Create your account' : 'Log in'),
    h('p', { class: 'muted' }, mode === 'register' ? 'Create an account before adding products to your cart or purchasing.' : 'Log in to add products to your cart and buy.'),
    form,
    h('p', { class: 'small' }, mode === 'register' ? 'Already have an account? ' : 'New here? ',
      h('a', { href: mode === 'register' ? loginHref : registerHref }, mode === 'register' ? 'Log in' : 'Create an account'))
  ));
}
main();
