export const money = (amount, currency) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);

let names;
export function countryName(code) {
  try { names ||= new Intl.DisplayNames(undefined, { type: 'region' }); return names.of(code) || code; } catch { return code; }
}

export function deliveryText(z) {
  if (!z) return '';
  const parts = [];
  if (z.delivery_days_min && z.delivery_days_max) {
    parts.push(z.delivery_days_min === z.delivery_days_max
      ? `Estimated delivery: ${z.delivery_days_min} days`
      : `Estimated delivery: ${z.delivery_days_min}–${z.delivery_days_max} days`);
  } else parts.push('Delivery estimate: to be confirmed');
  parts.push(z.tracking_available ? 'Tracking available' : 'Tracking not available');
  return parts.join(' · ');
}

export const dateText = (iso) => new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
