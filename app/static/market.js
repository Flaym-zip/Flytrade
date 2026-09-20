(() => {
'use strict';
const $ = id => document.getElementById(id);
const LABEL = {hausse: 'HAUSSE', stable: 'STABLE', baisse: 'BAISSE', attendre: 'ATTENDRE'};

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
function eur(x) { return (x ?? 0).toFixed(2).replace('.', ',') + ' €'; }
function pct(x) { return x == null ? '—' : (x * 100).toFixed(1) + ' %'; }
function num(x, d = 3) { return x == null ? '—' : Number(x).toFixed(d); }
function fmtTime(iso) { try { return new Date(iso).toLocaleTimeString('fr-FR'); } catch { return iso; } }

// ---------- Deployed brain header ----------
async function loadDeployedHeader(state) {
  try {
    const brains = await api('/api/brains');
    const match = brains.find(b => b.brain_id === state.brain_id);
    $('dep-name').textContent = match ? match.name : 'Cerveau non nommé (déploiement direct)';
    $('dep-dataset').textContent = match ? (match.dataset_name || 'Aucun') : '—';
  } catch (e) { $('dep-name').textContent = 'Cerveau non identifié'; }
  $('dep-brain-id').textContent = 'brain_id ' + (state.brain_id || '').slice(0, 8);
  $('dep-fingerprint').textContent = 'weight_fingerprint ' + (state.weight_fingerprint || '').slice(0, 8);
}

// ---------- Bloc 1: brain ----------
function renderBrainBlock(last) {
  const row = $('hsb-row'); row.innerHTML = '';
  const labels = ['HAUSSE', 'STABLE', 'BAISSE'];
  const preferred = last ? last.brain_preferred_action : null;
  for (let a = 0; a < 3; a++) {
    const cell = document.createElement('div'); cell.className = 'hsb-cell';
    const key = ['hausse', 'stable', 'baisse'][a];
    if (preferred === key) cell.classList.add('preferred');
    const score = last ? last.brain_scores[a] : null;
    const prob = last && last.probabilities ? last.probabilities[a] : null;
    cell.innerHTML = `<label>${labels[a]}</label><div class="score">${num(score, 3)}</div>` +
      `<div class="prob">p calibrée ${prob && prob.p != null ? pct(prob.p) : 'non prête'}</div>`;
    row.append(cell);
  }
  $('brain-preference').textContent = preferred ? LABEL[preferred] : 'Aucune décision encore';
  const tie = last && last.brain_tie_break;
  const tieNote = $('tie-note');
  if (tie && tie.used) {
    tieNote.hidden = false;
    tieNote.textContent = 'Égalité entre ' + tie.candidates.map(c => LABEL[c]).join(' / ') +
      ' — la case ' + LABEL[tie.selected] + ' a été retenue par tirage de départage (tie-break), pas par préférence plus forte.';
  } else {
    tieNote.hidden = true;
  }
}

// ---------- Bloc 2: economics ----------
function renderEconBlock(last) {
  const tbody = document.querySelector('#econ-table tbody'); tbody.innerHTML = '';
  const labels = ['Hausse', 'Stable', 'Baisse'];
  if (last && last.policy && last.quotes) {
    for (let a = 0; a < 3; a++) {
      const c = last.policy.candidates[a];
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${labels[a]}</td><td>${c.p != null ? pct(c.p) : 'non prête'}</td>` +
        `<td>${num(last.quotes.effective_gross[a], 3)}</td><td>${num(c.ev, 3)}</td><td>${num(c.ev_lower, 3)}</td>`;
      tbody.append(tr);
    }
  }
  const line = $('decision-line'); const val = $('decision-value'); const detail = $('decision-detail');
  detail.innerHTML = '';
  if (!last) {
    line.className = 'decision-line'; val.textContent = 'Aucune décision encore'; return;
  }
  const action = last.economic_action;
  if (action === 'attendre') {
    line.className = 'decision-line wait';
    val.textContent = 'ATTENDRE';
    const reason = document.createElement('div'); reason.className = 'wait-reason';
    reason.textContent = 'Raison : ' + (last.economic_reason || '—');
    detail.append(reason);
  } else {
    line.className = 'decision-line trade';
    val.textContent = 'TRADE ' + LABEL[action];
    const info = document.createElement('div'); info.className = 'wait-reason';
    info.textContent = 'Mise proposée : ' + eur(last.economic_stake) +
      (last.risk_decision ? ' · risque budget ' + eur(last.risk_decision.risk_budget) : '');
    detail.append(info);
  }
}

// ---------- History ----------
function resultBadge(r) {
  const span = document.createElement('span'); span.className = 'result-pill';
  if (r.status === 'won') { span.classList.add('won'); span.textContent = 'Gagné ' + eur(r.net); }
  else if (r.status === 'lost') { span.classList.add('lost'); span.textContent = 'Perdu ' + eur(r.net); }
  else if (r.status === 'pending') { span.textContent = 'En cours'; }
  else if (r.status === 'void') { span.textContent = 'Annulé'; }
  else { span.textContent = r.status || '—'; }
  return span;
}
function renderHistory(recent) {
  const tbody = document.querySelector('#history-table tbody'); tbody.innerHTML = '';
  for (const r of recent) {
    const tr = document.createElement('tr');
    const reasonOrStake = r.economic_action === 'attendre'
      ? (r.economic_reason || '—')
      : 'Mise ' + eur(r.economic_stake);
    const tdTime = document.createElement('td'); tdTime.textContent = fmtTime(r.created_at);
    const tdBrain = document.createElement('td'); tdBrain.textContent = LABEL[r.brain_preferred_action] || '—';
    const tdEcon = document.createElement('td'); tdEcon.textContent = r.economic_action === 'attendre' ? 'ATTENDRE' : 'TRADE ' + LABEL[r.economic_action];
    const tdReason = document.createElement('td'); tdReason.textContent = reasonOrStake;
    const tdResult = document.createElement('td'); tdResult.append(resultBadge(r));
    tr.append(tdTime, tdBrain, tdEcon, tdReason, tdResult);
    tbody.append(tr);
  }
}

// ---------- Technical details (collapsed) ----------
function renderDetails(state) {
  const box = $('tech-metrics'); box.innerHTML = '';
  const rows = [
    ['brain_id complet', state.brain_id],
    ['weight_fingerprint complet', state.weight_fingerprint],
    ['generation', state.stats ? state.stats.updates : '—'],
    ['calibration n', state.calibration ? state.calibration.n : '—'],
  ];
  for (const [k, v] of rows) {
    const div = document.createElement('div'); div.className = 'metric';
    div.innerHTML = `${k}<b style="font-size:12px;word-break:break-all">${v}</b>`;
    box.append(div);
  }
  $('tech-note').textContent = state.deployment ? ('Déployé : ' + state.deployment.at) : 'Aucun déploiement enregistré.';
}

// ---------- Controls ----------
async function refresh() {
  let state;
  try { state = await api('/api/state'); }
  catch (e) { showError(e.message); return; }
  $('connection').textContent = state.market && state.market.connected ? 'Connecté' : 'Hors ligne';
  $('modes').textContent = (state.collect ? 'Collecte active' : 'Collecte arrêtée') +
    (state.policy_enabled ? ' · politique active' : ' · politique en pause');
  $('capital').textContent = eur(state.stats ? state.stats.capital : 20);
  $('pnl').textContent = (state.stats && state.stats.net >= 0 ? '+' : '') + eur(state.stats ? state.stats.net : 0);
  await loadDeployedHeader(state);
  renderBrainBlock(state.last);
  renderEconBlock(state.last);
  renderHistory(state.recent || []);
  renderDetails(state);
}

function wire() {
  $('collect-on').addEventListener('click', async () => {
    try { await api('/api/controls', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({collect: true})}); await refresh(); }
    catch (e) { showError(e.message); }
  });
  $('collect-off').addEventListener('click', async () => {
    try { await api('/api/controls', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({collect: false})}); await refresh(); }
    catch (e) { showError(e.message); }
  });
  $('policy-on').addEventListener('click', async () => {
    try { await api('/api/controls', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({policy: true})}); await refresh(); }
    catch (e) { showError(e.message); }
  });
  $('policy-off').addEventListener('click', async () => {
    try { await api('/api/controls', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({policy: false})}); await refresh(); }
    catch (e) { showError(e.message); }
  });
}

function init() {
  wire();
  refresh();
  setInterval(refresh, 2000);
}
init();
})();
