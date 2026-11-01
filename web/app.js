const $ = (selector) => document.querySelector(selector);
const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const timeLabel = value => { if (!value) return 'now'; const date = new Date(value); return isNaN(date) ? 'now' : date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); };
const themeButton = $('#theme-toggle');
let toastTimer;
let latestState = null;

function syncThemeControl() {
  const light = document.documentElement.dataset.theme === 'light';
  themeButton.setAttribute('aria-label', `Switch to ${light ? 'dark' : 'light'} mode`);
  themeButton.title = `Switch to ${light ? 'dark' : 'light'} mode`;
  themeButton.querySelector('.theme-icon').textContent = light ? '☾' : '☼';
  themeButton.querySelector('.theme-label').textContent = light ? 'Dark' : 'Light';
}
syncThemeControl();
themeButton.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('lorekeeper-theme', next); } catch {}
  syncThemeControl();
});

function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 2800);
}

async function api(path, data = {}) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}

function truncateWords(value, max = 210) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  const prefix = text.slice(0, max + 1);
  const boundary = prefix.lastIndexOf(' ');
  return (prefix.slice(0, boundary > 0 ? boundary : max).trimEnd()) + '…';
}

function readableMemory(value, max = 210) {
  let text = String(value || '').replace(/\s+/g, ' ').trim();
  text = text.replace(/^chat keeps returning to\s+remember\s+/i, '')
    .replace(/^chat keeps returning to\s+/i, '')
    .replace(/^chat keeps saying\s+/i, '')
    .replace(/^remember\s+/i, '')
    .replace(/^["“]+|["”]+$/g, '');
  if (text) text = text[0].toLocaleUpperCase() + text.slice(1);
  return truncateWords(text, max);
}

function readableRoundTitle(value, ordinal) {
  const title = String(value || '').replace(/^(?:Field Study|Round)\s+\d+\s*:\s*/i, '').trim();
  return `Round ${ordinal}: ${title || 'Community Hunt'}`;
}

// Pure view-state derivation: no DOM reads or writes, API calls, or mutations.
function currentStep(state) {
  const chapter = state?.chapter;
  const game = state?.game;
  if (chapter?.status === 'VOTING') return 'vote';
  if (game?.status === 'ACTIVE') return 'hunt';
  if (chapter?.status === 'RESOLVED') {
    const matchingGame = game?.chapter_id === chapter.id;
    if (matchingGame && game.status === 'RESOLVED' && (game.mode === 'live' || game.live_completed_for_chapter)) return 'result';
    return 'hunt';
  }
  return 'memories';
}
window.currentStep = currentStep;

function applyTabRoute() {
  const valid = new Set(['stage','memories','history','engineering']);
  let tab = location.hash.replace(/^#/, '');
  if (!valid.has(tab)) {
    tab = 'stage';
    history.replaceState(null, '', `${location.pathname}${location.search}#stage`);
  }
  document.querySelectorAll('[data-panel]').forEach(panel => panel.classList.toggle('hide', panel.dataset.panel !== tab));
  document.querySelectorAll('[data-tab]').forEach(link => {
    const selected = link.dataset.tab === tab;
    link.classList.toggle('active', selected);
    link.setAttribute('aria-selected', String(selected));
    link.tabIndex = selected ? 0 : -1;
  });
}
window.addEventListener('hashchange', applyTabRoute);
applyTabRoute();

function setStep(step) {
  document.querySelectorAll('[data-view]').forEach(panel => panel.classList.toggle('hide', panel.dataset.view !== step));
  const order = ['memories','vote','hunt','result'];
  const index = order.indexOf(step);
  document.querySelectorAll('#round-steps [data-step]').forEach((item, itemIndex) => {
    item.classList.toggle('current', item.dataset.step === step);
    item.classList.toggle('complete', itemIndex < index);
    if (item.dataset.step === step) item.setAttribute('aria-current','step');
    else item.removeAttribute('aria-current');
  });
  const labels = {
    memories:['Memories','Choose the memory that starts the next Round.'],
    vote:['Vote','Chat chooses how the Hunt begins.'],
    hunt:['Hunt','Chat guesses together in Twitch.'],
    result:['Round result','See the answer and who helped find it.']
  };
  $('#stage-heading').textContent = labels[step][0];
  $('#stage-subtitle').textContent = labels[step][1];
  $('#chapter-status').textContent = labels[step][0].toUpperCase();
}

function latestStageMemory(memories) {
  return (memories || []).filter(item => item.status === 'PENDING' || item.status === 'ACTIVE')
    .sort((a,b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0] || null;
}

function renderMemoryCards(memories) {
  $('#lore-empty').classList.toggle('hide', memories.length > 0);
  $('#pending-pill').textContent = `${memories.filter(item => item.status === 'PENDING').length} pending`;
  $('#lore-list').innerHTML = memories.map(item => {
    const pending = item.status === 'PENDING';
    const approved = item.status === 'ACTIVE';
    const status = pending ? 'Needs review' : approved ? 'Approved' : item.status === 'REJECTED' ? 'Skipped' : 'Retired';
    let actions = '';
    if (pending) actions = `<button class="button secondary" data-action="approve" data-id="${esc(item.id)}">Approve</button><button class="button quiet" data-action="reject" data-id="${esc(item.id)}">Skip</button>`;
    else if (approved) actions = `<button class="button quiet" data-action="retire" data-id="${esc(item.id)}">Retire</button>`;
    else actions = `<button class="button secondary" data-action="restore" data-id="${esc(item.id)}">Restore</button><button class="button quiet" data-action="delete" data-id="${esc(item.id)}">Delete</button>`;
    return `<article class="lore-card"><div class="memory-meta lore-meta"><span class="lore-kind">${status}</span></div><p class="lore-summary">${esc(readableMemory(item.summary, 230))}</p><div class="lore-bottom"><span class="lore-source">${Number(item.mentions)||1} mention${Number(item.mentions)===1?'':'s'}</span><div class="lore-actions">${actions}</div></div></article>`;
  }).join('');
}

function renderMemoryStep(state) {
  const memories = state.lore || [];
  const selected = latestStageMemory(memories);
  const approvedCount = Number(state.lore_counts?.active ?? memories.filter(item => item.status === 'ACTIVE').length);
  $('#chapter-empty').classList.toggle('hide', Boolean(selected));
  $('#stage-memory-card').classList.toggle('hide', !selected);
  $('#stage-memory-actions').innerHTML = selected?.status === 'PENDING'
    ? `<button class="button secondary" data-action="approve" data-id="${esc(selected.id)}">Approve</button><button class="button quiet" data-action="reject" data-id="${esc(selected.id)}">Skip</button>`
    : selected ? '<span class="approved-label">Approved</span>' : '';
  if (selected) {
    $('#stage-memory-state').textContent = selected.status === 'PENDING' ? 'NEEDS REVIEW' : 'APPROVED';
    $('#stage-memory-count').textContent = `${Number(selected.mentions)||1} mention${Number(selected.mentions)===1?'':'s'}`;
    $('#stage-memory-summary').textContent = readableMemory(selected.summary, 230);
  }
  $('#chapter-empty strong').textContent = approvedCount ? 'No current memory selected.' : 'No approved memory yet.';
  $('#chapter-empty span').textContent = approvedCount ? 'Choose or approve a memory in the Memories tab.' : 'Review a memory in the Memories tab to begin.';
  const canStartVote = approvedCount > 0 && selected?.status !== 'PENDING';
  $('#start-vote').disabled = !window.lorekeeperLive || !canStartVote;
  $('#start-vote').title = !window.lorekeeperLive ? 'Connect Twitch and wait for Live · EventSub.' : !canStartVote ? 'Approve or skip the memory first.' : '';
}

function renderVote(state) {
  const chapter = state.chapter;
  const voting = chapter?.status === 'VOTING';
  window.currentChapterId = chapter?.id || null;
  $('#chapter-live').classList.toggle('hide', !voting);
  $('#close-chapter').classList.toggle('hide', !voting);
  if (!chapter) return;
  window.currentChapterId = chapter.id;
  $('#chapter-title').textContent = `Round ${chapter.ordinal}`;
  $('#chapter-intro').textContent = truncateWords(chapter.intro || 'Chat chooses how the Hunt begins.', 230);
  $('#chapter-seed').textContent = readableMemory(chapter.seed_lore || '', 180);
  $('#chapter-choice-a').textContent = 'Reveal a hint now';
  $('#chapter-choice-b').textContent = '+20 seconds, hint stays hidden';
  $('#chapter-choice-a-detail').textContent = 'Hint 1 appears when the Hunt starts.';
  $('#chapter-choice-b-detail').textContent = 'The Hunt starts without hint 1.';
  const votes = chapter.votes || {};
  const a = Number(votes.A || 0), b = Number(votes.B || 0), total = a+b;
  $('#chapter-votes-a').textContent = `${a} vote${a===1?'':'s'}`;
  $('#chapter-votes-b').textContent = `${b} vote${b===1?'':'s'}`;
  $('#chapter-total').textContent = `${total} vote${total===1?'':'s'}`;
  $('#chapter-bar-a').style.width = `${total ? Math.round(a*100/total) : 0}%`;
  $('#chapter-bar-b').style.width = `${total ? Math.round(b*100/total) : 0}%`;
  $('#chapter-clock').textContent = voting ? `Closes in ${formatClock(Math.max(0,Math.ceil((new Date(chapter.closes_at)-Date.now())/1000)))}` : 'Vote complete';
  $('#chapter-result').classList.toggle('hide', voting);
  $('#chapter-result').textContent = `${total} votes`;
  $('#chapter-result-copy').classList.add('hide');
  $('#close-chapter').disabled = !window.lorekeeperLive;
}

function formatClock(seconds) {
  return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
}

function renderHunt(state) {
  const game = state.game || {status:'IDLE'};
  const chapter = state.chapter;
  const hasGame = Boolean(game.id);
  const active = game.status === 'ACTIVE';
  const canStartLive = chapter?.status === 'RESOLVED' && !(game.chapter_id === chapter.id && game.mode === 'live') && !active;
  $('#boss-status').textContent = active ? (game.mode === 'simulation' ? 'SYNTHETIC' : 'LIVE') : hasGame ? 'COMPLETE' : 'READY';
  $('#boss-idle').classList.toggle('hide', hasGame);
  $('#boss-live').classList.toggle('hide', !hasGame);
  $('#start-boss').classList.toggle('hide', !canStartLive);
  $('#start-boss').disabled = !window.lorekeeperLive;
  $('#boss-replay').disabled = !chapter || chapter.status !== 'RESOLVED' || active;
  if (!hasGame) {
    $('#boss-idle strong').textContent = chapter?.status === 'RESOLVED' ? 'The Hunt is ready.' : 'Approve a memory to begin.';
    $('#boss-idle span').textContent = chapter?.status === 'RESOLVED' ? 'Start the Hunt; chat then guesses with !guess WORD.' : 'Start with a memory, then hold a vote.';
    return;
  }
  $('#boss-name').textContent = `${game.boss_name || 'Hunt'}${game.relic_name ? ` · ${game.relic_name}` : ''}`;
  $('#boss-epithet').textContent = game.boss_epithet || '';
  $('#boss-clock').textContent = active ? formatClock(game.seconds_left || 0) : 'Round complete';
  $('#boss-clue').textContent = game.clue || '';
  $('#boss-hint').textContent = game.status === 'RESOLVED' && game.target_word ? `Answer: ${game.target_word.toUpperCase()}` : game.hint ? `Hint ${game.hint_stage}: ${game.hint}` : 'A hint appears as the Hunt progresses.';
  const heat = Number(game.best_heat || 0);
  $('#boss-meters').textContent = game.best_guess ? `${heat}%${game.best_band ? ` · ${game.best_band.toLowerCase()}` : ''}` : 'Waiting for guesses';
  $('#boss-hp').textContent = `${heat}% best heat`;
  $('#boss-hp-bar').style.width = `${Math.max(0,Math.min(100,heat))}%`;
  $('#boss-mode').textContent = `${game.embedding_mode === 'offline_curated' ? 'Offline practice' : 'Meaning score'} · ${game.unique_guesses || 0} guesses${game.mode === 'simulation' ? ' · synthetic activity' : ''}${game.guess_latency_p95_ms == null ? '' : ` · p95 ${game.guess_latency_p95_ms} ms`}`;
  $('#boss-scoring-warning').textContent = game.scoring_error || '';
  $('#boss-scoring-warning').classList.toggle('hide', !game.scoring_error);
  $('#boss-input-notice').textContent = game.input_notice || '';
  $('#boss-input-notice').classList.toggle('hide', !game.input_notice);
  $('#boss-offline-vocab').classList.toggle('hide', !game.offline_words?.length);
  $('#boss-offline-words').textContent = (game.offline_words || []).join(', ');
  $('#boss-result').textContent = game.result || '';
  $('#boss-result').classList.toggle('hide', !game.result);
  $('#boss-closest-guesses').innerHTML = (game.closest_guesses || []).map(item => `<article class="guess-item"><b>#${Number(item.rank)||'—'} closest · ${esc(item.viewer)}</b><span>${esc(item.guess)} · ${Number(item.heat)||0}% heat</span></article>`).join('') || '<p class="muted-line">No guesses yet.</p>';
  $('#boss-feed').innerHTML = (game.feed || []).map(item => `<article class="guess-item"><b>${esc(item.viewer)}</b><span>${esc(item.guess)} · ${Number(item.heat)||0}% heat · #${Number(item.rank)||'—'} closest</span></article>`).join('') || '<p class="muted-line">No guesses yet.</p>';
  $('#boss-leaderboard').innerHTML = (game.round_leaderboard || []).slice(0,3).map(item => `<article class="contributor-row"><b>${esc(item.viewer)}</b><span>${item.best_heat}% heat · ${item.assists} assist${item.assists===1?'':'s'}${item.xp_awarded?` · +${item.xp_awarded} XP`:''}</span></article>`).join('');
}

function renderResult(state) {
  const game = state.game || {};
  const chapter = state.chapter || {};
  $('#result-word').textContent = game.target_word ? game.target_word.toUpperCase() : 'Hunt complete';
  $('#result-story').textContent = game.result || 'The Hunt is complete.';
  $('#result-memory').textContent = readableMemory(chapter.seed_lore || '', 200);
  const contributors = (game.round_leaderboard || []).slice(0,3);
  $('#result-contributors').innerHTML = contributors.map((item,index) => `<article class="contributor-row"><b>${index+1}. ${esc(item.viewer)}${item.is_closest?' · closest':''}</b><span>${item.best_heat}% heat · ${item.assists} assist${item.assists===1?'':'s'} · ${item.xp_awarded ? `+${item.xp_awarded} XP` : `${item.xp_projected} XP`}</span></article>`).join('') || '<p class="muted-line">No scored guesses this Round.</p>';
  $('#start-chapter').disabled = !window.lorekeeperLive;
}

function renderRoundHistory(rounds, relics) {
  const entries = [];
  const usedRelics = new Set();
  for (const round of rounds || []) {
    const charm = round.relic;
    if (charm?.id) usedRelics.add(charm.id);
    const votes = round.votes || {};
    const voteCount = Number(votes.A||0)+Number(votes.B||0);
    entries.push(`<article class="history-item"><div class="history-heading"><b>${esc(readableRoundTitle(round.title, round.ordinal))}</b><span>${voteCount} vote${voteCount===1?'':'s'}</span></div><p>${esc(readableMemory(round.seed_lore,180))}</p><p>${esc(truncateWords(round.result || 'Round result saved.',220))}</p>${charm ? `<div class="history-charm"><span aria-hidden="true">✦</span><div><b>${esc(charm.name)}</b><small>Charm</small></div></div>` : ''}</article>`);
  }
  for (const charm of relics || []) {
    if (charm.id && usedRelics.has(charm.id)) continue;
    entries.push(`<article class="history-item history-charm-only"><div class="history-charm"><span aria-hidden="true">✦</span><div><b>${esc(charm.name)}</b><small>Charm</small><p>${esc(readableMemory(charm.source_memory || charm.power_text || '',180))}</p></div></div></article>`);
  }
  $('#history-list').innerHTML = entries.join('');
  $('#history-empty').classList.toggle('hide', entries.length > 0);
  $('#relic-count').textContent = (relics || []).length;
  $('#relic-empty').classList.add('hide');
  $('#relic-grid').innerHTML = '';
}

function renderEngineering(state) {
  $('#stat-events').textContent = state.stats?.received ?? 0;
  $('#stat-lore').textContent = state.lore_counts?.active ?? (state.lore || []).filter(item => item.status === 'ACTIVE').length;
  $('#stat-pending').textContent = state.pending ?? 0;
  $('#stat-chapters').textContent = (state.chapter_history || []).filter(item => item.status === 'RESOLVED').length;
  $('#pipeline-label').textContent = state.stats?.replay_running ? 'REPLAY' : state.twitch_eventsub ? 'LIVE' : 'READY';
  $('#uptime').textContent = `${state.event_log === 'redpanda' ? 'Redpanda' : 'Local'} · ${state.stats?.uptime ?? 0}s`;
  $('#event-log-label').textContent = state.event_log === 'redpanda' ? 'Redpanda' : 'Local';
  const pipeline = state.pipeline || {};
  $('#pipeline-eps').textContent = pipeline.events_per_second ?? 0;
  $('#pipeline-lag').textContent = pipeline.consumer_lag ?? 0;
  $('#pipeline-p95').textContent = pipeline.projection_p95_ms == null ? '-' : `${pipeline.projection_p95_ms} ms`;
  const load = state.load_test || {};
  const replayBusy = Boolean(state.stats?.replay_running || load.running);
  ['#replay-raid','#replay-troll','#load-replay'].forEach(selector => { $(selector).disabled = replayBusy; });
  $('#rehearsal-status').textContent = state.stats?.replay_running ? 'Synthetic replay is running…'
    : load.running ? 'Load run is running…'
      : load.result ? `${load.result.events_sent} synthetic events · ${load.result.measured_events_per_second} events/sec · p95 ${load.result.append_or_process_latency_ms?.p95 ?? '-'} ms · ${load.result.all_events_projected ? 'fully processed' : 'still processing'}`
        : load.error ? `Load run failed: ${load.error}` : 'Replay traffic is synthetic and cannot create memories.';
  const events = state.events || [];
  $('#event-empty').classList.toggle('hide', events.length > 0);
  const icons = {'chat.message':'•','raid.incoming':'↗','follow':'＋','subscription':'♡','gift_sub':'♡','stream.online':'◉','privacy.forget':'⌑'};
  $('#event-list').innerHTML = events.slice(0,7).map(event => `<article class="event-row"><span class="event-icon">${icons[event.type] || '·'}</span><span class="event-copy"><b>${esc(event.viewer || 'Viewer')}</b><small>${esc(event.type?.replace('.',' ') || 'event')}</small><span>${esc(event.text || '')}</span></span><time>${timeLabel(event.at)}</time></article>`).join('');
}

function render(state) {
  latestState = state;
  window.lorekeeperLive = Boolean(state.twitch_eventsub);
  const memories = state.lore || [];
  const step = currentStep(state);
  setStep(step);
  renderMemoryCards(memories);
  renderMemoryStep(state);
  renderVote(state);
  renderHunt(state);
  renderResult(state);
  renderRoundHistory(state.chapter_history || [], state.relics || []);
  renderEngineering(state);

  const connection = $('#connection');
  const failed = (state.twitch_subscriptions || []).filter(item => item.status !== 'enabled');
  const missingScopes = failed.some(item => String(item.status).startsWith('missing scope:'))
    || (state.twitch_authenticated && !state.twitch_whisper_enabled);
  connection.innerHTML = state.twitch_eventsub
    ? `<i></i><span>Live · EventSub · ${esc(state.twitch_login || 'channel')}</span>`
    : state.twitch_authenticated
      ? `<i class="${state.twitch_error ? 'warning' : ''}"></i><span>${state.twitch_error ? 'EventSub needs attention' : 'EventSub connecting…'}</span>`
      : `<i class="${state.twitch_error ? 'warning' : ''}"></i><span>${state.twitch_error ? 'Twitch needs attention' : 'Twitch not connected'}</span>`;
  connection.title = [state.twitch_error, state.twitch_whisper_status,
    ...failed.map(item => item.type + ': ' + item.status)].filter(Boolean).join('\n');
  $('#channel-status').textContent = state.twitch_eventsub ? 'Live' : state.twitch_authenticated ? 'Connecting' : 'Not connected';
  $('.channel strong').textContent = state.twitch_login ? `@${state.twitch_login}` : 'your channel';
  $('#twitch-connect').classList.toggle('hide', state.twitch_authenticated && !missingScopes);
  $('#twitch-retry').classList.toggle('hide', !state.twitch_authenticated || state.twitch_eventsub || !state.twitch_error);
  $('#twitch-disconnect').classList.toggle('hide', !state.twitch_authenticated);
  $('#connect-hero').classList.add('hide');
  $('#mute-toggle').classList.toggle('on', Boolean(state.muted));
  $('#mute-caption').textContent = state.muted ? 'ON' : 'OFF';
  $('#mute-toggle').setAttribute('aria-pressed', String(Boolean(state.muted)));
  $('#mute-toggle').setAttribute('aria-label', `Panic mute ${state.muted ? 'on' : 'off'}. Activate to ${state.muted ? 'unmute' : 'mute'} the overlay.`);

  const activeMemoryCount = Number(state.lore_counts?.active ?? memories.filter(item => item.status === 'ACTIVE').length);
  $('#start-vote').classList.toggle('hide', step !== 'memories');
  $('#close-chapter').classList.toggle('hide', step !== 'vote');
  $('#start-boss').classList.toggle('hide', step !== 'hunt' || state.chapter?.status !== 'RESOLVED' || state.game?.status === 'ACTIVE' || (state.game?.chapter_id === state.chapter?.id && state.game?.mode === 'live'));
  $('#boss-replay').classList.toggle('hide', step !== 'hunt' || !state.chapter || state.chapter.status !== 'RESOLVED');
  $('#start-chapter').classList.toggle('hide', step !== 'result');
  $('#start-chapter').disabled = !window.lorekeeperLive || activeMemoryCount === 0;
  $('#start-chapter').title = !window.lorekeeperLive ? 'Connect Twitch before starting the next Round.' : '';

  const utterance = state.utterances?.[0];
  if (utterance) $('#preview-line').textContent = utterance.text;
}

async function refresh() {
  try { render(await (await fetch('/api/state', {cache:'no-store'})).json()); }
  catch {
    $('#connection').innerHTML = '<i class="warning"></i><span>Reconnecting…</span>';
  }
}
refresh();
setInterval(refresh, 1500);
const stream = new EventSource('/api/stream');
stream.onmessage = () => refresh();

async function startRound() {
  try { await api('/api/chapter/start'); await refresh(); toast('Vote started.'); }
  catch (error) { toast(error.message); }
}
$('#start-vote').addEventListener('click', startRound);
$('#start-chapter').addEventListener('click', startRound);
$('#close-chapter').addEventListener('click', async () => {
  try { await api('/api/chapter/close'); await refresh(); toast('Vote closed.'); }
  catch (error) { toast(error.message); }
});
$('#start-boss').addEventListener('click', async () => {
  try { await api('/api/game/start', {chapter_id:window.currentChapterId}); await refresh(); toast('Hunt started. Chat can guess with !guess WORD.'); }
  catch (error) { toast(error.message); }
});
$('#boss-replay').addEventListener('click', async () => {
  try { await api('/api/game/replay'); await refresh(); toast('Synthetic Hunt preview started.'); }
  catch (error) { toast(error.message); }
});
$('#mute-toggle').addEventListener('click', async () => {
  try { await api('/api/settings/mute', {muted:!latestState?.muted}); await refresh(); }
  catch (error) { toast(error.message); }
});
$('#replay-raid').addEventListener('click', async () => {
  try { await api('/api/replay', {profile:'raid'}); toast('Synthetic raid replay started.'); }
  catch (error) { toast(error.message); }
});
$('#replay-troll').addEventListener('click', async () => {
  try { await api('/api/replay', {profile:'troll'}); toast('Safety replay started.'); }
  catch (error) { toast(error.message); }
});
$('#load-replay').addEventListener('click', async () => {
  try { await api('/api/load-test', {events:1000,rate:100,profile:'raid'}); toast('1,000-event load started.'); }
  catch (error) { toast(error.message); }
});

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  try {
    await api('/api/lore/action', {id:button.dataset.id, action:button.dataset.action});
    await refresh();
    toast(button.dataset.action === 'approve' ? 'Memory approved.' : button.dataset.action === 'reject' ? 'Memory skipped.' : 'Memory updated.');
  } catch (error) { toast(error.message); }
});

$('#forget-button').addEventListener('click', async () => {
  const viewer = prompt('Enter the viewer’s Twitch user ID to delete their saved memory.');
  if (!viewer?.trim()) return;
  try { await api('/api/privacy/forget', {viewer_id:viewer.trim(), viewer:viewer.trim()}); await refresh(); toast('Viewer memory deleted.'); }
  catch (error) { toast(error.message); }
});
$('#twitch-disconnect').addEventListener('click', async () => {
  try { await api('/api/twitch/disconnect'); await refresh(); toast('Twitch disconnected.'); }
  catch (error) { toast(error.message); }
});
$('#twitch-retry').addEventListener('click', async () => {
  try { await api('/api/twitch/reconnect'); await refresh(); toast('Reconnecting to EventSub.'); }
  catch (error) { toast(error.message); }
});
