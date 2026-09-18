async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function card(label, value) {
  return `<article><span>${label}</span><strong>${value}</strong></article>`;
}

async function refresh() {
  const [orders, vehicles] = await Promise.all([request('/api/orders'), request('/api/vehicles')]);
  document.querySelector('#metrics').innerHTML = [
    card('Pending orders', orders.filter((order) => order.status === 'pending').length),
    card('Available vehicles', vehicles.filter((vehicle) => vehicle.status === 'available').length),
    card('Queued weight', `${orders.reduce((sum, order) => sum + order.weight_kg, 0).toLocaleString()} kg`),
  ].join('');
  document.querySelector('#orders').innerHTML = orders.length
    ? orders.map((order) => `<article class="order"><strong>${order.reference}</strong><span>${order.origin} → ${order.destination}</span><small>${order.weight_kg.toLocaleString()} kg · ${order.status}</small></article>`).join('')
    : '<p class="empty">No orders yet. Create one through <code>POST /api/orders</code>.</p>';
}

document.querySelector('#plan').addEventListener('click', async () => {
  const plan = await request('/api/dispatch/plan', { method: 'POST' });
  document.querySelector('#plan-json').textContent = JSON.stringify(plan, null, 2);
  document.querySelector('#plan-output').hidden = false;
});

refresh().catch((error) => {
  document.querySelector('#orders').textContent = error.message;
});

