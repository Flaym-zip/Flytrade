(() => {
'use strict';
const $ = id => document.getElementById(id);
const STATE_LABEL = {neuf:'Neuf', entrainement:'Entraînement', entraine:'Entraîné',
  teste:'Testé', deploye:'Déployé', archive:'Archivé'};

function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 4000);
}
function showError(msg) {
  const e = $('error'); e.textContent = msg; e.hidden = false;
  clearTimeout(showError._t); showError._t = setTimeout(() => { e.hidden = true; }, 6000);
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || body.message || ('Erreur ' + res.status));
  return body;
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleString('fr-FR'); } catch { return iso; }
}
function pct(x) { return x == null ? 'Pas encore calculée' : (x * 100).toFixed(1) + ' %'; }

function actionButton(label, key, actions, onClick) {
  const [enabled, reason] = actions[key];
  const b = document.createElement('button');
  b.textContent = label; b.className = 'subtle';
  b.disabled = !enabled;
  if (reason) b.title = reason;
  if (enabled) b.addEventListener('click', onClick);
  return b;
}

function renderCard(b) {
  const card = document.createElement('article'); card.className = 'brain-card';
  const head = document.createElement('div'); head.className = 'row wrap';
  const h3 = document.createElement('h3'); h3.textContent = b.name;
  const badge = document.createElement('span');
  badge.className = 'state-badge state-' + b.state;
  badge.textContent = STATE_LABEL[b.state] || b.state;
  head.append(h3, badge);
  card.append(head);
  if (b.legacy) {
    const tag = document.createElement('span'); tag.className = 'legacy-tag';
    tag.textContent = 'Hérité — cerveau atelier historique migré';
    card.append(tag);
  }
  const dl = document.createElement('dl');
  const rows = [
    ['brain_id', b.brain_id_short],
    ['weight_fingerprint', b.weight_fingerprint_short],
    ['créé le', fmtDate(b.created_at)],
    ['dataset', b.dataset_name || (b.dataset_id ? b.dataset_id.slice(0, 8) : 'Aucun')],
    ['mises à jour', String(b.updates)],
    ['balanced accuracy (validation)', pct(b.balanced_accuracy_validation)],
    ['test ouvert', b.test_opened ? 'Oui' : 'Non'],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.append(dt, dd);
  }
  card.append(dl);
  const actions = document.createElement('div'); actions.className = 'brain-actions';
  actions.append(
    actionButton('Entraîner', 'train', b.actions, () => { location.href = '/entrainement?workshop=' + b.workshop_id; }),
    actionButton('Tester', 'test', b.actions, () => { location.href = '/entrainement?workshop=' + b.workshop_id; }),
    actionButton('Déployer', 'deploy', b.actions, () => { location.href = '/entrainement?workshop=' + b.workshop_id; }),
    actionButton('Dupliquer', 'duplicate', b.actions, () => openDuplicate(b)),
    actionButton('Archiver', 'archive', b.actions, () => openArchive(b)),
  );
  card.append(actions);
  return card;
}

async function refresh() {
  let list;
  try { list = await api('/api/brains'); }
  catch (e) { showError(e.message); return; }
  const grid = $('grid'); grid.innerHTML = '';
  $('empty-note').hidden = list.length > 0;
  for (const b of list) grid.append(renderCard(b));
}

let duplicateTarget = null;
function openDuplicate(b) {
  duplicateTarget = b;
  const form = $('duplicate-form'); form.reset();
  form.elements.name.value = b.name + ' (copie)';
  $('duplicate-dialog').showModal();
}
let archiveTarget = null;
function openArchive(b) {
  archiveTarget = b;
  $('archive-dialog').showModal();
}

function wire() {
  $('open-create').addEventListener('click', () => {
    $('create-form').reset();
    $('create-dialog').showModal();
  });
  $('create-cancel').addEventListener('click', () => $('create-dialog').close());
  $('create-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const d = new FormData($('create-form'));
    try {
      await api('/api/brains', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: d.get('name'), n_kc: Number(d.get('n_kc')), seed: Number(d.get('seed')),
          sparsity: Number(d.get('sparsity')), use_liquidity: d.get('use_liquidity') === 'on',
        }),
      });
      $('create-dialog').close(); toast('Cerveau créé.'); await refresh();
    } catch (e) { showError(e.message); }
  });

  $('duplicate-cancel').addEventListener('click', () => $('duplicate-dialog').close());
  $('duplicate-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const d = new FormData($('duplicate-form'));
    try {
      await api('/api/brains/' + duplicateTarget.workshop_id + '/duplicate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: d.get('name')}),
      });
      $('duplicate-dialog').close(); toast('Cerveau dupliqué.'); await refresh();
    } catch (e) { showError(e.message); }
  });

  $('archive-cancel').addEventListener('click', () => $('archive-dialog').close());
  $('archive-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try {
      await api('/api/brains/' + archiveTarget.workshop_id + '/archive', {method: 'POST'});
      $('archive-dialog').close(); toast('Cerveau archivé (données conservées).'); await refresh();
    } catch (e) { showError(e.message); }
  });
}

wire(); refresh();
})();
