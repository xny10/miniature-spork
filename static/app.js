const $ = id => document.getElementById(id);
const state = { job: null, sessions: [] };

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2200);
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    let message = await response.text();
    try { message = JSON.parse(message).detail || message; } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

const presets = {
  instant: [0, 0],
  fast: [500, 1500],
  normal: [2000, 6000],
  safe: [5000, 12000],
  slow: [10000, 20000],
};

$('delayMode').onchange = () => {
  const preset = presets[$('delayMode').value];
  if (preset) {
    $('delayMin').value = preset[0];
    $('delayMax').value = preset[1];
  }
};

$('importFile').onchange = async event => {
  const file = event.target.files[0];
  if (file) $('shopeeInput').value = await file.text();
  event.target.value = '';
};

async function action(name, body) {
  try {
    const result = await api(`/api/shopee/${name}`, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (result?.job) {
      state.job = result.job;
      renderJob();
    }
    return result;
  } catch (error) {
    toast(error.message);
    return null;
  }
}

$('sStart').onclick = () => action('start', {
  links: $('shopeeInput').value,
  config: {
    concurrency: +$('concurrency').value,
    batchDelayMin: +$('delayMin').value,
    batchDelayMax: +$('delayMax').value,
    maxRetries: +$('maxRetry').value,
  },
});
$('sPause').onclick = () => action('pause');
$('sResume').onclick = () => action('resume');
$('sStop').onclick = () => action('stop');
$('sRetry').onclick = () => action('retry');

async function loadJob() {
  try {
    const data = await api('/api/shopee/job');
    state.job = data.job;
    renderJob();
  } catch {}
}

async function loadSessions() {
  try {
    const data = await api('/api/shopee/sessions');
    state.sessions = data.sessions || [];
    renderSessions();
  } catch {}
}

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' });
}

function renderSessions() {
  const box = $('savedSessions');
  if (!box) return;
  if (!state.sessions.length) {
    box.innerHTML = '<div class="empty-card">Belum ada data tersimpan. Jalankan job lalu klik + Simpan.</div>';
    return;
  }
  const activeJobId = state.job?.jobId;
  box.innerHTML = state.sessions.map(session => `
    <article class="session-card ${session.jobId === activeJobId ? 'active' : ''}" data-id="${esc(session.id)}">
      <button class="delete-session" data-delete="${esc(session.id)}" title="Hapus card">×</button>
      <div class="session-top">
        <strong>${esc(session.name || 'Session')}</strong>
        <span class="badge ${esc(session.status)}">${esc(session.status || 'saved')}</span>
      </div>
      <div class="session-meta">
        <span>${session.total || 0} URL</span>
        <span>${session.success || 0} sukses</span>
        <span>${session.failed || 0} gagal</span>
      </div>
      <small>Disimpan ${formatDate(session.savedAt)}</small>
    </article>
  `).join('');
  box.querySelectorAll('.session-card').forEach(card => {
    card.onclick = async event => {
      if (event.target.closest('.delete-session')) return;
      try {
        const data = await api(`/api/shopee/sessions/${card.dataset.id}/load`, { method: 'POST' });
        state.job = data.job;
        renderJob();
        renderSessions();
        toast('Data card dibuka');
      } catch (error) {
        toast(error.message);
      }
    };
  });
  box.querySelectorAll('.delete-session').forEach(button => {
    button.onclick = async event => {
      event.stopPropagation();
      if (!confirm('Hapus card data tersimpan ini?')) return;
      try {
        const data = await api(`/api/shopee/sessions/${button.dataset.delete}`, { method: 'DELETE' });
        state.sessions = data.sessions || [];
        renderSessions();
        toast('Card dihapus');
      } catch (error) {
        toast(error.message);
      }
    };
  });
}

function counts(items) {
  const active = new Map();
  const products = new Map();
  items.forEach(item => {
    if (item.status === 'success' && item.adLibraryUrl) {
      if (item.shopId) active.set(item.shopId, (active.get(item.shopId) || 0) + 1);
      if (item.productId) products.set(item.productId, (products.get(item.productId) || 0) + 1);
    }
  });
  return [active, products];
}

function visible(items, active, products) {
  const query = $('sSearch').value.toLowerCase().trim();
  const result = items.filter(item => !query || `${item.shortUrl || ''} ${item.shopId || ''} ${item.productId || ''} ${item.adLibraryUrl || ''} ${item.status || ''} ${item.error || ''}`.toLowerCase().includes(query));
  const mode = $('sSort').value;
  const activeCount = item => item.activeAdCount ?? (active.get(item.shopId) || 0);
  const productCount = item => item.productAdCount ?? (products.get(item.productId) || 0);
  if (mode === 'activeDesc') result.sort((a, b) => activeCount(b) - activeCount(a));
  if (mode === 'activeAsc') result.sort((a, b) => activeCount(a) - activeCount(b));
  if (mode === 'productDesc') result.sort((a, b) => productCount(b) - productCount(a));
  if (mode === 'productAsc') result.sort((a, b) => productCount(a) - productCount(b));
  return result;
}

function renderJob() {
  const job = state.job;
  if (!job) {
    ['sTotal', 'sProcessed', 'sSuccess', 'sFailed', 'sQueued'].forEach(id => $(id).textContent = '0');
    $('sSpeed').textContent = '0/mnt';
    $('sStatus').textContent = 'Idle';
    $('sStatus').className = 'status idle';
    $('sRows').innerHTML = '<tr><td colspan="11">Belum ada job.</td></tr>';
    $('sMessage').textContent = 'Session job disimpan di SQLite.';
    return;
  }

  const items = job.items || [];
  $('sTotal').textContent = job.total || items.length;
  $('sProcessed').textContent = job.processed || 0;
  $('sSuccess').textContent = job.success || 0;
  $('sFailed').textContent = job.failed || 0;
  $('sQueued').textContent = items.filter(item => item.status === 'queued').length;
  const minutes = Math.max((Date.now() - (job.startedAt || job.createdAt)) / 60000, 0.01);
  $('sSpeed').textContent = `${Math.round((job.processed || 0) / minutes)}/mnt`;
  $('sStatus').textContent = job.status;
  $('sStatus').className = `status ${job.status}`;

  if (job.status === 'waiting' && job.waitingUntil) {
    const seconds = Math.max(0, Math.ceil((job.waitingUntil - Date.now()) / 1000));
    $('sMessage').textContent = `Menunggu batch berikutnya sekitar ${seconds} detik...`;
  } else if (job.status === 'processing') {
    $('sMessage').textContent = 'Job sedang diproses...';
  } else {
    $('sMessage').textContent = `Status job: ${job.status}.`;
  }

  const [active, products] = counts(items);
  const rows = visible(items, active, products);
  $('sRows').innerHTML = rows.length ? rows.map((item, index) => {
    const brandOffer = item.shopId ? `https://affiliate.shopee.co.id/offer/brand_offer/${item.shopId}` : '';
    const productOffer = item.productId ? `https://affiliate.shopee.co.id/offer/product_offer/${item.productId}` : '';
    return `<tr>
      <td>${index + 1}</td>
      <td><a href="${esc(item.shortUrl)}" target="_blank" rel="noopener">Buka</a></td>
      <td>${esc(item.shopId || '—')}</td>
      <td>${esc(item.productId || '—')}</td>
      <td>${item.activeAdCount ?? (active.get(item.shopId) || '—')}</td>
      <td>${item.productAdCount ?? (products.get(item.productId) || '—')}</td>
      <td>${brandOffer ? `<a href="${brandOffer}" target="_blank" rel="noopener">Offer</a>` : '—'}${item.brandCategory ? `<br><small>${esc(item.brandCategory)}</small>` : ''}</td>
      <td>${productOffer ? `<a href="${productOffer}" target="_blank" rel="noopener">Produk</a>` : '—'}</td>
      <td>${item.adLibraryUrl ? `<a href="${esc(item.adLibraryUrl)}" target="_blank" rel="noopener">Iklan</a>` : '—'}</td>
      <td><span class="badge ${esc(item.status)}">${esc(item.status)}</span></td>
      <td title="${esc(item.brandOfferError || item.error || '')}">${esc((item.brandOfferError || item.error || '').slice(0, 80))}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="11">Tidak ada hasil.</td></tr>';
}

function markClickedLink(link) {
  if (!link || link.classList.contains('clicked-link')) return;
  link.classList.add('clicked-link');
  link.dataset.clicked = 'true';
  if (!link.querySelector('.click-check')) {
    link.insertAdjacentHTML('beforeend', ' <span class="click-check" aria-hidden="true">✓</span>');
  }
}

$('sRows').addEventListener('click', event => {
  const link = event.target.closest('a');
  if (!link) return;
  markClickedLink(link);
  toast(`Dibuka: ${link.textContent.replace('✓', '').trim()}`);
});

$('sSearch').oninput = renderJob;
$('sSort').onchange = renderJob;

function copyProducts(unique) {
  let ids = (state.job?.items || []).map(item => item.productId).filter(Boolean);
  if (unique) ids = [...new Set(ids)];
  navigator.clipboard.writeText(ids.join('\n')).then(() => toast(`${ids.length} Product ID disalin`));
}
$('copyUnique').onclick = () => copyProducts(true);
$('copyAllProducts').onclick = () => copyProducts(false);

$('copyBrand').onclick = () => {
  const links = [...new Set((state.job?.items || []).map(item => item.shopId).filter(Boolean))]
    .map(id => `https://affiliate.shopee.co.id/offer/brand_offer/${id}`);
  navigator.clipboard.writeText(links.join('\n')).then(() => toast(`${links.length} Brand Offer disalin`));
};

$('copyProductOffer').onclick = () => {
  const links = (state.job?.items || []).map(item => item.productId).filter(Boolean)
    .map(id => `https://affiliate.shopee.co.id/offer/product_offer/${id}`);
  navigator.clipboard.writeText(links.join('\n')).then(() => toast(`${links.length} Product Offer disalin`));
};

$('dedupe').onclick = async () => {
  if (!confirm('Hapus Product ID duplikat?')) return;
  const result = await action('dedupe');
  if (result) toast(`${result.removed || 0} duplikat dihapus`);
};

$('fetchBrand').onclick = async () => {
  toast('Mengambil kategori...');
  const result = await action('brand-categories', { cookie: $('affiliateCookie').value.trim() });
  if (result) toast('Selesai');
};

$('saveCard').onclick = async () => {
  const currentName = $('sessionName').value.trim();
  const name = currentName || prompt('Nama card/session apa?', 'Campaign Shopee')?.trim();
  if (!name) {
    toast('Isi nama card dulu');
    return;
  }
  try {
    const data = await api('/api/shopee/sessions', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    state.sessions = data.sessions || [];
    $('sessionName').value = '';
    renderSessions();
    toast(`Data disimpan: ${name}`);
  } catch (error) {
    toast(error.message);
  }
};

$('clearJob').onclick = async () => {
  if (!confirm('Hapus job Shopee tersimpan?')) return;
  try {
    await api('/api/shopee/job', { method: 'DELETE' });
    state.job = null;
    renderJob();
    toast('Job dihapus');
  } catch (error) {
    toast(error.message);
  }
};

$('loadSession').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch('/api/shopee/session', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Gagal load session');
    state.job = data.job;
    renderJob();
    toast('Session diload');
  } catch (error) {
    toast(error.message);
  }
  event.target.value = '';
};

async function pollJob() {
  await loadJob();
  setTimeout(pollJob, 1500);
}

renderJob();
loadSessions();
pollJob();
