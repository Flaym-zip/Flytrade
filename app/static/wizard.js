(() => {
'use strict';
const $ = id => document.getElementById(id);
const STATE_LABEL = {neuf:'Neuf', entrainement:'Entraînement', entraine:'Entraîné',
  teste:'Testé', deploye:'Déployé', archive:'Archivé'};
const S = {workshopId: null, datasetId: null, brains: [], datasets: [], session: null};

function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 4000);
}
function showError(msg) {
  const e = $('error'); e.textContent = msg; e.hidden = false;
  clearTimeout(showError._t); showError._t = setTimeout(() => { e.hidden = true; }, 7000);
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || body.message || ('Erreur ' + res.status));
  return body;
}
function confirmDialog(title, html) {
  return new Promise((resolve) => {
    $('confirm-title').textContent = title;
    $('confirm-body').innerHTML = html;
    const dlg = $('confirm-dialog');
    dlg.showModal();
    const onClose = () => {
      dlg.removeEventListener('close', onClose);
      resolve(dlg.returnValue === 'confirm');
    };
    dlg.addEventListener('close', onClose);
  });
}

function setStepClass(id, cls) {
  const el = $(id);
  el.classList.remove('active', 'done');
  if (cls) el.classList.add(cls);
}

function pct(x) { return x == null ? '—' : (x * 100).toFixed(1) + ' %'; }

// ---------- Step 1: brain ----------
async function loadBrains() {
  S.brains = (await api('/api/brains')).filter(b => !b.archived);
  const box = $('brain-pick'); box.innerHTML = '';
  for (const b of S.brains) {
    const label = document.createElement('label');
    label.innerHTML = `<input name="brain" type="radio" value="${b.workshop_id}"/> <b>${b.name}</b>` +
      `<dl><dt>État</dt><dd>${STATE_LABEL[b.state] || b.state}</dd><dt>KC</dt><dd>${b.n_kc}</dd></dl>`;
    const input = label.querySelector('input');
    input.checked = b.workshop_id === S.workshopId;
    input.addEventListener('change', () => selectBrain(b.workshop_id));
    box.append(label);
  }
  if (!S.workshopId && S.brains.length === 1) selectBrain(S.brains[0].workshop_id);
}

async function selectBrain(workshopId) {
  S.workshopId = workshopId;
  const history = window.history;
  const url = new URL(location.href); url.searchParams.set('workshop', workshopId);
  history.replaceState(null, '', url);
  setStepClass('step-brain', 'done');
  setStepClass('step-dataset', 'active');
  $('selected-header').hidden = false;
  await refreshHeader();
  await loadDatasets();
  await refreshSession();
}

async function refreshHeader() {
  const b = await api('/api/brains/' + S.workshopId);
  $('sel-brain-name').textContent = b.name;
  $('sel-brain-state').textContent = STATE_LABEL[b.state] || b.state;
  $('sel-brain-id').textContent = 'brain_id ' + b.brain_id_short;
  return b;
}

// ---------- Step 2: dataset ----------
async function loadDatasets() {
  S.datasets = await api('/api/datasets');
  const box = $('dataset-pick'); box.innerHTML = '';
  for (const d of S.datasets) {
    const label = document.createElement('label');
    const s = d.stats;
    label.innerHTML = `<input name="dataset" type="radio" value="${d.dataset_id}"/> <b>${d.name}</b>` +
      `<dl><dt>Source</dt><dd>${d.source}</dd><dt>Fenêtres</dt><dd>${d.windows}</dd>` +
      `<dt>H exclusif</dt><dd>${s.hausse}</dd><dt>S exclusif</dt><dd>${s.stable}</dd><dt>B exclusif</dt><dd>${s.baisse}</dd>` +
      `<dt>Multiple</dt><dd>${s.multiple}</dd><dt>Aucune</dt><dd>${s.aucune}</dd></dl>`;
    const input = label.querySelector('input');
    input.checked = d.dataset_id === S.datasetId;
    input.addEventListener('change', () => selectDataset(d.dataset_id, d.name));
    box.append(label);
  }
}
function selectDataset(id, name) {
  S.datasetId = id;
  $('sel-dataset-name').textContent = name;
  setStepClass('step-dataset', 'done');
  setStepClass('step-protocol', 'active');
}

// ---------- Step 3: protocol ----------
async function prepareProtocol() {
  const cfg = {
    seed: 42, n_kc: 2048, sparsity: .05, use_liquidity: true, // overridden server-side by the brain itself
    mode: $('p-mode').value, epochs: Number($('p-epochs').value),
    shuffle_train: $('p-shuffle').checked, signal: $('p-signal').value,
    learning_rate: Number($('p-lr').value), epsilon: Number($('p-epsilon').value),
    economic_head: false, dataset_id: S.datasetId, max_windows: 10000, batch: 10,
  };
  try {
    const snap = await api('/api/brains/' + S.workshopId + '/train/prepare', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cfg),
    });
    S.session = snap;
    setStepClass('step-protocol', 'done');
    setStepClass('step-train', 'active');
    updateTrainDisplay(snap);
    toast('Protocole préparé.');
  } catch (e) { showError(e.message); }
}

// ---------- Step 4: train ----------
function updateTrainDisplay(snap) {
  $('train-progress').max = snap.total || 1;
  $('train-progress').value = snap.position || 0;
  $('train-label').textContent = 'TRAIN — ' + snap.position + ' / ' + snap.total;
  const phase = snap.last ? snap.last.phase : null;
  const m = phase && snap.metrics ? snap.metrics[phase] : null;
  const tbody = document.querySelector('#live-metrics tbody'); tbody.innerHTML = '';
  if (m) {
    const rows = [
      ['Accuracy', pct(m.accuracy)], ['Balanced accuracy', pct(m.balanced_accuracy)],
      ['Recall H', pct(m.recall && m.recall[0])], ['Recall S', pct(m.recall && m.recall[1])],
      ['Recall B', pct(m.recall && m.recall[2])],
    ];
    for (const [k, v] of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${k}</td><td>${v}</td>`;
      tbody.append(tr);
    }
  }
}

async function runTraining() {
  $('train-btn').disabled = true;
  try {
    let snap = S.session;
    while (snap.position < snap.total) {
      snap = await api('/api/brains/' + S.workshopId + '/train/step', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch: 30}),
      });
      S.session = snap;
      updateTrainDisplay(snap);
    }
    setStepClass('step-train', 'done');
    setStepClass('step-validation', 'active');
    await loadValidation();
    setStepClass('step-test', 'active');
    await refreshHeader();
  } catch (e) { showError(e.message); }
  finally { $('train-btn').disabled = false; }
}

// ---------- Step 5: validation ----------
function metricsBlock(m, title) {
  const div = document.createElement('div');
  const conf = m.confusion_matrix;
  let confHtml = '';
  if (conf) {
    confHtml = '<table class="confusion"><thead><tr><th>Réel \\ Prévu</th><th>H</th><th>S</th><th>B</th></tr></thead><tbody>' +
      ['H', 'S', 'B'].map((lab, i) => '<tr><th>' + lab + '</th>' +
        conf[i].map((v, j) => `<td class="${i === j ? 'diag' : ''}">${v}</td>`).join('') + '</tr>').join('') +
      '</tbody></table>';
  }
  div.innerHTML = `<h3>${title}</h3>
    <div class="metrics">
      <div class="metric">Accuracy<b>${pct(m.accuracy)}</b></div>
      <div class="metric">Balanced accuracy<b>${pct(m.balanced_accuracy)}</b></div>
      <div class="metric">Recall H<b>${pct(m.recall[0])}</b></div>
      <div class="metric">Recall S<b>${pct(m.recall[1])}</b></div>
      <div class="metric">Recall B<b>${pct(m.recall[2])}</b></div>
    </div>${confHtml}`;
  if (m.baselines) {
    const b = m.baselines;
    const bt = document.createElement('table'); bt.className = 'baseline-table';
    bt.innerHTML = '<thead><tr><th>Référence</th><th>Accuracy</th><th>Balanced accuracy</th></tr></thead><tbody>' +
      [['Toujours Hausse', b.toujours_hausse], ['Toujours Stable', b.toujours_stable],
       ['Toujours Baisse', b.toujours_baisse], ['Hasard (théorique)', b.hasard]]
        .map(([lab, s]) => `<tr><td>${lab}</td><td>${pct(s.accuracy)}</td><td>${pct(s.balanced_accuracy)}</td></tr>`).join('') +
      '</tbody></table>';
    div.append(bt);
    const baselineNote = document.createElement('p');
    baselineNote.className = 'muted small';
    baselineNote.textContent = 'Hasard = espérance théorique d’un tirage uniforme à trois issues (1/3), pas un tirage aléatoire mesuré.';
    div.append(baselineNote);
  }
  const note = document.createElement('p');
  note.className = 'muted small';
  note.textContent = 'Résultats affichés tels quels, sans jugement de qualité ni de rentabilité.';
  div.append(note);
  return div;
}

async function loadValidation() {
  const m = await api('/api/brains/' + S.workshopId + '/metrics');
  const box = $('validation-body'); box.innerHTML = '';
  if (m.validation) box.append(metricsBlock(m.validation, 'Validation (poids gelés)'));
  else box.textContent = 'Pas encore de validation disponible.';
  renderTestState(m);
}

// ---------- Step 6: test ----------
function renderTestState(m) {
  const resultsBox = $('test-results');
  if (m.test_opened && m.test) {
    $('test-body').hidden = true;
    resultsBox.hidden = false;
    resultsBox.innerHTML = '';
    const banner = document.createElement('div');
    banner.id = 'test-banner-exposed';
    banner.innerHTML = '<b>TEST EXPOSÉ</b> — ces exemples ne redeviendront jamais inconnus.';
    resultsBox.append(banner, metricsBlock(m.test, 'Test final'));
    setStepClass('step-test', 'done');
    setStepClass('step-deploy', 'active');
  } else {
    $('test-body').hidden = false;
    resultsBox.hidden = true;
  }
}

async function openTestFinal() {
  const ok = await confirmDialog('Ouvrir le test final ?',
    '<p>Cette partition ne pourra plus jamais être considérée comme inconnue, même après un nouvel entraînement.</p>');
  if (!ok) return;
  try {
    let snap = await api('/api/brains/' + S.workshopId + '/test/open', {method: 'POST'});
    S.session = snap;
    while (snap.position < snap.total) {
      snap = await api('/api/brains/' + S.workshopId + '/train/step', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch: 30}),
      });
      S.session = snap;
    }
    await loadValidation();
    await refreshHeader();
    toast('Test final ouvert et calculé.');
  } catch (e) { showError(e.message); }
}

// ---------- Step 7: deploy ----------
async function deployBrain() {
  const ok = await confirmDialog('Déployer sur le marché ?',
    '<p>Copie les poids et la calibration gelés ; nouveau portefeuille fictif de 20 €, politique en pause.</p>');
  if (!ok) return;
  try {
    await api('/api/brains/' + S.workshopId + '/deploy', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirm: true}),
    });
    toast('Cerveau déployé sur le marché.');
    await refreshHeader();
  } catch (e) { showError(e.message); }
}

// ---------- Resume existing session on load ----------
async function refreshSession() {
  try {
    const snap = await api('/api/brains/' + S.workshopId + '/session');
    S.session = snap;
    if (snap.session) {
      S.datasetId = snap.config.dataset_id;
      const ds = S.datasets.find(d => d.dataset_id === S.datasetId);
      if (ds) { $('sel-dataset-name').textContent = ds.name; document.querySelector(`input[name=dataset][value="${S.datasetId}"]`)?.click(); }
      setStepClass('step-dataset', 'done');
      setStepClass('step-protocol', 'done');
      setStepClass('step-train', 'active');
      updateTrainDisplay(snap);
      if (snap.position >= snap.total && snap.total > 0) {
        setStepClass('step-train', 'done');
        setStepClass('step-validation', 'active');
        await loadValidation();
        setStepClass('step-test', 'active');
      }
    }
  } catch (e) { /* no session yet: normal for a fresh brain */ }
}

function wire() {
  $('prepare-btn').addEventListener('click', prepareProtocol);
  $('train-btn').addEventListener('click', runTraining);
  $('open-test-btn').addEventListener('click', openTestFinal);
  $('deploy-btn').addEventListener('click', deployBrain);
}

async function init() {
  wire();
  setStepClass('step-brain', 'active');
  const params = new URLSearchParams(location.search);
  const preselect = params.get('workshop');
  await loadBrains();
  if (preselect && S.brains.some(b => b.workshop_id === preselect)) {
    document.querySelector(`input[name=brain][value="${preselect}"]`).checked = true;
    await selectBrain(preselect);
  }
}
init();
})();
