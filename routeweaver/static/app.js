async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) {
      /* keep the status text */
    }
    throw new Error(detail);
  }
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

function showResult(label, payload) {
  document.querySelector('#plan-json').textContent = `// ${label}\n${JSON.stringify(payload, null, 2)}`;
  document.querySelector('#plan-output').hidden = false;
}

async function refreshBatches() {
  const batches = await request('/api/dispatch/batches');
  document.querySelector('#batches').innerHTML = batches.length
    ? batches
        .map(
          (batch) =>
            `<article class="batch" data-batch-id="${batch.batch_id}"><strong>Batch #${batch.batch_id}</strong><span>${batch.assignment_count} assigned · ${batch.unassigned_count} unassigned</span><small>${batch.created_at}</small></article>`,
        )
        .join('')
    : '<p class="empty">No confirmed batches yet. Use “Confirm dispatch” to commit the current plan.</p>';
}

document.querySelector('#plan').addEventListener('click', async () => {
  try {
    const plan = await request('/api/dispatch/plan', { method: 'POST' });
    showResult('read-only preview', plan);
  } catch (error) {
    showResult('plan failed', { error: error.message });
  }
});

document.querySelector('#commit').addEventListener('click', async () => {
  try {
    const result = await request('/api/dispatch/commit', { method: 'POST' });
    showResult('confirmed dispatch', result);
  } catch (error) {
    showResult('commit rejected (409)', { error: error.message });
  }
  await Promise.all([refresh(), refreshBatches()]);
});

document.querySelector('#refresh-batches').addEventListener('click', () => refreshBatches().catch((error) => {
  document.querySelector('#batches').textContent = error.message;
}));

document.querySelector('#batches').addEventListener('click', async (event) => {
  const cardEl = event.target.closest('.batch');
  if (!cardEl) return;
  const batch = await request(`/api/dispatch/batches/${cardEl.dataset.batchId}`);
  showResult(`batch #${batch.batch_id}`, batch);
});

Promise.all([refresh(), refreshBatches()]).catch((error) => {
  document.querySelector('#orders').textContent = error.message;
});
