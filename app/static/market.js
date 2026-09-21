(() => {
'use strict';
const $ = id => document.getElementById(id);
const LABEL = {hausse: 'HAUSSE', stable: 'STABLE', baisse: 'BAISSE', attendre: 'ATTENDRE'};
let S = null, currentSettings = null, center = null, received = 0, focus = true;

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
function eur(x) { return (x ?? 0).toLocaleString('fr-FR', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' €'; }
function pct(x) { return x == null ? '—' : (x * 100).toFixed(1) + ' %'; }
function metric(parent, label, value) {
  const d = document.createElement('div'); d.className = 'metric';
  const b = document.createElement('b'); b.textContent = value;
  d.append(b, document.createTextNode(label)); parent.append(d);
}
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

// ---------- Decision strip ----------
function renderDecisionStrip(last) {
  $('brain-preference').textContent = last ? LABEL[last.brain_preferred_action] : 'En attente de la première fenêtre';
  const box = $('econ-decision'); const val = $('decision-value'); const reason = $('decision-reason');
  if (!last) { box.className = ''; val.textContent = '—'; reason.textContent = ''; return; }
  if (last.economic_action === 'attendre') {
    box.className = 'wait';
    val.textContent = 'ATTENDRE';
    reason.textContent = 'Raison : ' + (last.economic_reason || '—');
  } else {
    box.className = 'trade';
    val.textContent = 'TRADE ' + eur(last.economic_stake);
    reason.textContent = 'Case verrouillée : ' + LABEL[last.economic_action] + ' · × ' + (last.multiplier ?? '—');
  }
}

// ---------- Stats (trades only, never mixed with waits) ----------
function renderStats(stat) {
  $('capital').textContent = eur(stat.capital);
  $('pnl').textContent = (stat.net >= 0 ? '+' : '') + eur(stat.net);
  $('drawdown').textContent = 'Baisse max : ' + eur(stat.max_drawdown);
  $('trades').textContent = String(stat.trades);
  $('coverage').textContent = pct(stat.coverage) + ' de couverture';
  $('wl').textContent = stat.wins + ' / ' + stat.losses;
  $('winrate').textContent = 'Win rate ' + pct(stat.hit_rate);
  const box = $('secondary-stats'); box.innerHTML = '';
  metric(box, 'total misé', eur(stat.staked));
  metric(box, 'PnL moyen / trade', stat.trades ? eur(stat.net / stat.trades) : '—');
  metric(box, 'choix hausse (trades)', String((stat.chosen || [0, 0, 0])[0]));
  metric(box, 'choix stable (trades)', String((stat.chosen || [0, 0, 0])[1]));
  metric(box, 'choix baisse (trades)', String((stat.chosen || [0, 0, 0])[2]));
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
    const tdTime = document.createElement('td'); tdTime.textContent = fmtTime(r.created_at);
    const tdBrain = document.createElement('td'); tdBrain.textContent = LABEL[r.brain_preferred_action] || '—';
    const tdEcon = document.createElement('td'); tdEcon.textContent = r.economic_action === 'attendre' ? 'ATTENDRE' : 'TRADE ' + LABEL[r.economic_action];
    const tdStake = document.createElement('td'); tdStake.textContent = r.economic_action === 'attendre' ? '—' : eur(r.economic_stake);
    const tdResult = document.createElement('td'); tdResult.append(resultBadge(r));
    tr.append(tdTime, tdBrain, tdEcon, tdStake, tdResult);
    tbody.append(tr);
  }
}

// ---------- Wait diagnostics (collapsed) ----------
async function loadWaitStats(stat) {
  const box = $('wait-stats'); box.innerHTML = '';
  metric(box, 'opportunités', String(stat.opportunities));
  metric(box, 'trades', String(stat.trades));
  metric(box, 'attentes', String(stat.waits));
  metric(box, 'couverture', pct(stat.coverage));
  try {
    const reasons = await api('/api/wait-reasons');
    const tbody = document.querySelector('#reason-table tbody'); tbody.innerHTML = '';
    for (const [reason, count] of Object.entries(reasons)) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${reason}</td><td>${count}</td>`;
      tbody.append(tr);
    }
  } catch (e) { /* diagnostic only */ }
}

// ---------- Technical details ----------
function renderDetails(state) {
  const box = $('tech-metrics'); box.innerHTML = '';
  metric(box, 'brain_id complet', state.brain_id);
  metric(box, 'weight_fingerprint complet', state.weight_fingerprint);
  metric(box, 'calibration n', state.calibration ? state.calibration.n : '—');
  metric(box, 'déploiement', state.deployment ? state.deployment.at : 'aucun');
}

// ---------- Risk / stake controls (reuses Rules06 / /api/settings as-is) ----------
function fillRiskForm(s) {
  const f = $('risk-form');
  f.elements.stake_mode.value = s.stake_mode;
  f.elements.fixed_stake.value = s.fixed_stake;
  f.elements.max_stake.value = s.max_stake;
  f.elements.max_fraction_pct.value = (s.max_fraction * 100).toFixed(1);
  f.elements.drawdown_limit.value = s.drawdown_limit;
  f.elements.min_edge.value = s.min_edge;
  const q = $('quote-form');
  q.elements.quote_mode.value = s.quote_mode;
  q.elements.m0.value = s.multipliers[0];
  q.elements.m1.value = s.multipliers[1];
  q.elements.m2.value = s.multipliers[2];
}

// Activity presets touch ONLY min_edge (the acceptance criterion). Risk limits
// (max_fraction, max_stake, drawdown_limit, fixed_stake) are the user's own,
// explicit choice below and a preset must never raise them.
const PRESETS = {
  prudent: {min_edge: .08},
  normal: {min_edge: .02},
  actif: {min_edge: 0},
};
function markPreset(name) {
  for (const p of ['prudent', 'normal', 'actif']) $('preset-' + p).classList.toggle('active', p === name);
}
async function applyPreset(name) {
  if (!currentSettings) return;
  const overrides = PRESETS[name];
  const merged = Object.assign({}, currentSettings, overrides);
  try {
    await api('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(merged)});
    currentSettings = merged; fillRiskForm(merged); markPreset(name);
    toast('Préréglage ' + name.toUpperCase() + ' appliqué.');
  } catch (e) { showError(e.message); }
}

// ---------- Chart animation (reuses grid.js as-is) ----------
function animate() {
  if (S) {
    const m = S.market;
    if (center === null) center = m.price;
    if (focus && m.price != null) center += (m.price - center) * .18;
    window.FlyGrid.draw($('chart'), {
      now: (m.exchange_time || 0) + (m.connected ? Math.min(.5, (performance.now() - received) / 1000) : 0),
      price: m.price, center, points: m.chart || [], preview: S.preview, trade: S.active_trade,
    }, {center});
  }
  requestAnimationFrame(animate);
}

// ---------- Refresh loop ----------
async function refresh() {
  let state;
  try { state = await api('/api/state'); received = performance.now(); }
  catch (e) { showError(e.message); return; }
  S = state;
  $('connection').textContent = state.market.connected ? 'Connecté' : 'Hors ligne';
  $('feed').textContent = state.market.price != null ? (state.market.price.toFixed(2) + ' USD') : 'En attente du flux';
  $('modes').textContent = (state.collect ? 'Collecte active' : 'Collecte arrêtée') +
    (state.policy_enabled ? ' · politique active' : ' · politique en pause');
  await loadDeployedHeader(state);
  renderDecisionStrip(state.last);
  renderStats(state.stats);
  renderHistory(state.recent || []);
  renderDetails(state);
  if (!currentSettings) { currentSettings = state.settings; fillRiskForm(state.settings); }
  if (document.querySelector('details:has(#wait-stats)').open) await loadWaitStats(state.stats);
}

function wire() {
  $('focus').addEventListener('click', () => { focus = !focus; $('focus').textContent = 'Autofocus ' + (focus ? 'activé' : 'figé'); });
  for (const [id, body] of [['collect-on', {collect: true}], ['collect-off', {collect: false}],
                             ['policy-on', {policy: true}], ['policy-off', {policy: false}]]) {
    $(id).addEventListener('click', async () => {
      try { await api('/api/controls', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); await refresh(); }
      catch (e) { showError(e.message); }
    });
  }
  for (const name of ['prudent', 'normal', 'actif']) $('preset-' + name).addEventListener('click', () => applyPreset(name));

  $('risk-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const d = new FormData(ev.target);
    const merged = Object.assign({}, currentSettings, {
      stake_mode: d.get('stake_mode'), fixed_stake: Number(d.get('fixed_stake')), max_stake: Number(d.get('max_stake')),
      max_fraction: Number(d.get('max_fraction_pct')) / 100, drawdown_limit: Number(d.get('drawdown_limit')),
      min_edge: Number(d.get('min_edge')),
    });
    try {
      await api('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(merged)});
      currentSettings = merged; markPreset(''); toast('Réglages de risque appliqués.');
    } catch (e) { showError(e.message); }
  });

  $('quote-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const d = new FormData(ev.target);
    const merged = Object.assign({}, currentSettings, {
      quote_mode: d.get('quote_mode'), multipliers: [Number(d.get('m0')), Number(d.get('m1')), Number(d.get('m2'))],
    });
    try {
      await api('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(merged)});
      currentSettings = merged; toast('Cotations de simulation appliquées.');
    } catch (e) { showError(e.message); }
  });
}

function init() {
  wire();
  refresh();
  setInterval(refresh, 500);
  animate();
}
init();
})();
