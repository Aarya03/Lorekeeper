const $ = (selector) => document.querySelector(selector);
const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const timeLabel = value => { if (!value) return 'now'; const date = new Date(value); return isNaN(date) ? 'now' : date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); };
const themeButton = $('#theme-toggle');
function syncThemeControl() {
  const light = document.documentElement.dataset.theme === 'light';
  themeButton.setAttribute('aria-label', `Switch to ${light ? 'dark' : 'light'} mode`);
  themeButton.title = `Switch to ${light ? 'dark' : 'light'} mode`;
  themeButton.querySelector('.theme-icon').textContent = light ? '\u263E' : '\u263C';
  themeButton.querySelector('.theme-label').textContent = light ? 'Dark mode' : 'Light mode';
}
syncThemeControl();
themeButton.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('lorekeeper-theme', next); } catch {}
  syncThemeControl();
});
let toastTimer;
let relicRenderKey = '';
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
function renderChapter(chapter, history, activeLoreCount) {
  window.currentChapterId = chapter?.id || null;
  const active = chapter && chapter.status === 'VOTING';
  const exists = Boolean(chapter);
  $('#chapter-empty').classList.toggle('hide', exists);
  $('#chapter-live').classList.toggle('hide', !exists);
  const hasApprovedLore = activeLoreCount > 0;
  $('#start-chapter').disabled = Boolean(active) || !window.lorekeeperLive || (!exists && !hasApprovedLore);
  $('#start-chapter').textContent = active ? 'Field study in progress' : (!exists && !hasApprovedLore ? 'Approve an observation first' : 'Start a field study');
  $('#start-chapter').title = !window.lorekeeperLive ? 'Connect Twitch and wait for Live · EventSub first' : (!exists && !hasApprovedLore ? 'Approve a community observation in the review panel first' : '');
  if (!exists) {
    $('#chapter-empty strong').textContent = hasApprovedLore ? 'An approved observation is ready to become a field study.' : 'Approve a community observation to seed the first field study.';
    $('#chapter-empty span').textContent = hasApprovedLore ? 'Start the field study, then chat votes how its research charm enters the lore-boss encounter.' : 'Review the Community Observations panel, approve a specific chat moment, then start the field study.';
  }
  $('#start-boss').classList.toggle('hide', !chapter || chapter.status !== 'RESOLVED');
  $('#start-boss').disabled = !window.lorekeeperLive;
  $('#close-chapter').classList.toggle('hide', !active);
  $('#stat-chapters').textContent = history.filter(item => item.status === 'RESOLVED').length;
  if (chapter) {
    $('#chapter-status').textContent = active ? 'VOTING' : 'LAST REVEAL';
    $('#chapter-title').textContent = chapter.title || ('FIELD STUDY ' + chapter.ordinal);
    $('#chapter-intro').textContent = chapter.intro;
    $('#chapter-seed').textContent = chapter.seed_lore;
    $('#chapter-choice-a').textContent = chapter.choices.A;
    $('#chapter-choice-b').textContent = chapter.choices.B;
    $('#chapter-choice-a-detail').textContent = chapter.choice_details?.A || '';
    $('#chapter-choice-b-detail').textContent = chapter.choice_details?.B || '';
    const a = Number(chapter.votes.A || 0), b = Number(chapter.votes.B || 0), total = a + b;
    $('#chapter-votes-a').textContent = a + ' vote' + (a === 1 ? '' : 's');
    $('#chapter-votes-b').textContent = b + ' vote' + (b === 1 ? '' : 's');
    $('#chapter-total').textContent = total + ' community vote' + (total === 1 ? '' : 's') + (active ? ' · use !vote A or !vote B in Twitch chat' : '');
    $('#chapter-bar-a').style.width = (total ? Math.round(a * 100 / total) : 0) + '%';
    $('#chapter-bar-b').style.width = (total ? Math.round(b * 100 / total) : 0) + '%';
    $('#chapter-result').classList.toggle('hide', active);
    $('#chapter-result-copy').classList.toggle('hide', active);
    if (!active) {
      const winner = chapter.winning_choice === 'TIE' ? 'BOTH PATHS' : 'PATH ' + chapter.winning_choice;
      $('#chapter-result').textContent = winner + ' · ' + a + '–' + b;
      $('#chapter-result-copy').textContent = chapter.result;
      $('#chapter-clock').textContent = 'FINDING RECORDED';
    } else {
      const remaining = Math.max(0, Math.ceil((new Date(chapter.closes_at) - Date.now()) / 1000));
      $('#chapter-clock').textContent = 'VOTE CLOSES IN ' + Math.floor(remaining / 60) + ':' + String(remaining % 60).padStart(2, '0');
    }
  } else {
    $('#chapter-status').textContent = 'READY';
    $('#chapter-clock').textContent = '';
  }
  const completed = history.filter(item => item.status === 'RESOLVED');
  $('#history-empty').classList.toggle('hide', completed.length > 0);
  $('#history-list').innerHTML = completed.map(item =>
    '<article class="history-item"><b>Field Study ' + item.ordinal + ': ' + esc(item.winning_choice === 'TIE' ? 'Both paths stayed open' : item.choices[item.winning_choice]) + '</b><span>' + esc(item.result) + ' · ' + (Number(item.votes.A || 0) + Number(item.votes.B || 0)) + ' votes</span></article>'
  ).join('');
}
const relicSilhouettes = {
  reliquary:'M50 7 83 25v34c0 17-13 28-33 35C30 87 17 76 17 59V25z',
  medallion:'M50 8a34 34 0 1 0 0 68 34 34 0 0 0 0-68M38 72l-5 20 17-9 17 9-5-20',
  crystal:'M50 5 83 29 70 76 50 94 30 76 17 29z',
  compass:'M50 8a34 34 0 1 0 0 68 34 34 0 0 0 0-68M58 37 68 27 63 43 42 57 32 67 37 51z',
  keepsake:'M23 18h54a7 7 0 0 1 7 7v43a7 7 0 0 1-7 7H23a7 7 0 0 1-7-7V25a7 7 0 0 1 7-7M31 75l-5 14 24-11 24 11-5-14'
};
const relicGlyphs = {
  crystal:'M50 23 69 42 50 62 31 42z M50 23v39 M31 42h38',
  die:'M35 32h30a5 5 0 0 1 5 5v26a5 5 0 0 1-5 5H35a5 5 0 0 1-5-5V37a5 5 0 0 1 5-5z M40 42h1 M59 57h1 M59 42h1 M40 57h1',
  palette:'M50 27c-15 0-26 10-26 24 0 13 10 23 22 23h7c5 0 8-5 5-9l-3-4c-2-3 0-7 4-7h8c7 0 9-8 5-14-5-8-13-13-22-13z M38 43h1 M48 37h1 M60 43h1',
  soundwave:'M26 51h7l6-16 11 33 9-25 6 8h9',
  circuit:'M32 32h17v17H32z M49 40h18v18H49z M40 49v17h-8 M67 49h8v-9',
  flame:'M50 25c4 12-7 15-5 24 2 5 8 5 9 0 8 9 8 18 2 24-10 11-29 4-29-11 0-10 6-18 13-25-1 9 2 12 6 12 6 0 8-10 4-24z',
  medal:'M50 32a18 18 0 1 0 0 36 18 18 0 0 0 0-36z M39 65 34 82l16-8 16 8-5-17',
  orbit:'M50 50m-9 0a9 9 0 1 0 18 0 9 9 0 1 0-18 0 M50 50c-20-25-35-14-24 0 10 13 37 18 48 5 10-13-6-31-22-31-15 0-27 13-26 26',
  compass:'M50 27 61 50 50 73 39 50z M50 27v46 M27 50h46',
  eye:'M23 50Q50 24 77 50 50 76 23 50z M50 42a8 8 0 1 0 0 16 8 8 0 0 0 0-16z',
  lantern:'M37 35h26l5 34H32z M42 27h16v8H42z M39 76h22 M50 35v34',
  cassette:'M29 35h42v31H29z M36 43h28v10H36z M40 60a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M60 60a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  gear:'M50 29v8m0 26v8m21-21h-8m-26 0h-8m36-15-6 6m-18 18-6 6m30 0-6-6M41 41l-6-6 M50 41a9 9 0 1 0 0 18 9 9 0 0 0 0-18z',
  microscope:'M39 27h19v8H39z M44 35v20l-9 10h30 M52 39l14 14-7 7-14-14z M33 73h39 M65 60a16 16 0 0 0 10-12',
  atom:'M50 50c-23-10-29-24-19-29 11-6 28 11 32 29 5 19-2 32-13 29-12-4-17-25 0-42 13-13 29-14 31-3 2 12-18 24-31 16z M50 50h1',
  flask:'M41 25h18 M46 25v20L31 70h38L54 45V25 M39 61h22',
  molecule:'M35 35l16 15 17-17 M51 50l-7 19 M35 35a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M61 26a7 7 0 1 0 14 0 7 7 0 0 0-14 0z M37 63a7 7 0 1 0 14 0 7 7 0 0 0-14 0z M44 43a7 7 0 1 0 14 0 7 7 0 0 0-14 0z',
  telescope:'M28 36l40-12 8 19-40 12z M40 52l10 25 M65 43l7 17 M32 60h38',
  star:'m50 25 7 17 18 1-14 12 5 18-16-10-16 10 5-18-14-12 18-1z'
};
const relicPatterns = {
  orbit:'<ellipse cx="50" cy="50" rx="39" ry="16" fill="none" stroke="#fff" stroke-opacity=".3" transform="rotate(-28 50 50)"/>',
  stardust:'<circle cx="28" cy="28" r="2" fill="#fff"/><circle cx="72" cy="34" r="1.5" fill="#fff"/><circle cx="32" cy="68" r="1.5" fill="#fff"/><circle cx="72" cy="70" r="2" fill="#fff"/>',
  engraving:'<path d="M27 50h6m34 0h6M50 27v6m0 34v6M34 34l5 5m22 22 5 5m0-32-5 5m-22 22-5 5" stroke="#fff" stroke-opacity=".35" stroke-width="1.5"/>',
  runes:'<path d="m23 35 6-5 6 5-6 5zm36 0 6-5 6 5-6 5zM23 65l6-5 6 5-6 5zm36 0 6-5 6 5-6 5z" fill="none" stroke="#fff" stroke-opacity=".4" stroke-width="1.5"/>',
  ribbons:'<path d="M25 32q25-16 50 0M25 68q25 16 50 0" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="2"/>'
};
function relicSvg(relic) {
  const colors = (relic.palette || []).filter(color => /^#[\da-f]{6}$/i.test(color));
  const first = colors[0] || '#a98cff', second = colors[1] || '#ff9b72';
  const silhouette = relicSilhouettes[relic.visual?.shape] || relicSilhouettes.reliquary;
  const glyph = relicGlyphs[relic.visual?.sigil] || relicGlyphs.star;
  const pattern = relicPatterns[relic.visual?.pattern] || relicPatterns.stardust;
  return `<svg viewBox="0 0 100 100" role="img" aria-label="${esc(relic.name)} artwork"><defs><linearGradient id="rg-${esc(relic.id)}" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${first}"/><stop offset="1" stop-color="${second}"/></linearGradient><radialGradient id="gl-${esc(relic.id)}"><stop stop-color="${second}" stop-opacity=".45"/><stop offset="1" stop-color="${first}" stop-opacity="0"/></radialGradient></defs><circle cx="50" cy="48" r="46" fill="url(#gl-${esc(relic.id)})"/><path d="${silhouette}" fill="url(#rg-${esc(relic.id)})" fill-opacity=".24" stroke="url(#rg-${esc(relic.id)})" stroke-width="2.5" stroke-linejoin="round"/>${pattern}<path d="${glyph}" fill="none" stroke="#fff7ff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><circle cx="17" cy="18" r="2" fill="${second}"/><circle cx="83" cy="77" r="2" fill="${first}"/></svg>`;
}
function renderRelics(relics = []) {
  const signature = JSON.stringify(relics);
  if (signature === relicRenderKey) return;
  relicRenderKey = signature;
  $('#relic-count').textContent = relics.length;
  $('#relic-empty').classList.toggle('hide', relics.length > 0);
  $('#relic-grid').innerHTML = relics.map(relic => {
    const stageStart = [0, 30, 100, 250, 600][Number(relic.stage) || 0];
    const next = relic.next_stage_at;
    const progress = next ? Math.max(0, Math.min(100, (relic.resonance - stageStart) * 100 / (next - stageStart))) : 100;
    const pips = Array.from({length:relic.max_charge}, (_, index) => `<i class="${index < relic.charge ? 'charged' : ''}"></i>`).join('');
    return `<article class="relic-card" data-stage="${Number(relic.stage) || 0}"><div class="relic-artwork">${relicSvg(relic)}<span class="relic-tier">${esc(relic.stage_name)}</span><div class="relic-charge" aria-label="${relic.charge} of ${relic.max_charge} charges">${pips}</div></div><div class="relic-details"><span class="relic-category">${esc(relic.stream_label)}</span><h3>${esc(relic.name)}</h3><p class="relic-memory">${esc(relic.source_memory || 'A founding memory of the community')}</p><p>${esc(relic.power_text)}</p><div class="relic-power">${esc(relic.power_name)}</div><div class="relic-progress"><i style="width:${progress}%"></i></div><div class="relic-meta"><span>${relic.resonance} resonance</span><span>${relic.wards} wards</span></div></div></article>`;
  }).join('');
}
function render(state) {
  window.lorekeeperLive = Boolean(state.twitch_eventsub);
  $('#stat-events').textContent = state.stats.received;
  $('#stat-lore').textContent = state.lore_counts?.active ?? state.lore.filter(item => item.status === 'ACTIVE').length;
  $('#stat-pending').textContent = state.pending;
  $('#narrator-status').textContent = state.narrator_provider || 'Curated templates';
  $('#pending-pill').textContent = state.pending + ' pending';
  $('#side-pending').textContent = state.pending;
  $('#side-pending').classList.toggle('hide', !state.pending);
  $('#mute-toggle').classList.toggle('on', state.muted);
  $('#mute-caption').textContent = state.muted ? 'Panic mute is on' : 'The keeper is ready to speak';
  $('#mute-toggle').setAttribute('aria-label', state.muted ? 'Unmute mascot' : 'Mute mascot');
  $('#pipeline-label').textContent = state.stats.replay_running ? 'REPLAY' : state.twitch_eventsub ? 'LIVE' : 'READY';
  $('#uptime').textContent = (state.event_log === 'redpanda' ? 'Redpanda event log' : 'Local event log') + ' · ' + state.stats.uptime + 's uptime';
  const pipeline = state.pipeline || {};
  $('#pipeline-eps').textContent = pipeline.events_per_second ?? 0;
  $('#pipeline-lag').textContent = pipeline.consumer_lag ?? 0;
  $('#pipeline-p95').textContent = pipeline.projection_p95_ms == null ? 'Warming up' : pipeline.projection_p95_ms + ' ms';
  const load = state.load_test || {};
  const replayBusy = Boolean(state.stats.replay_running || load.running);
  ['#replay-raid','#replay-troll','#load-replay'].forEach(selector => { $(selector).disabled = replayBusy; });
  $('#rehearsal-status').textContent = state.stats.replay_running ? 'Synthetic replay is running…'
    : load.running ? 'Load test is running…'
      : load.result ? `${load.result.events_sent} synthetic events · ${load.result.measured_events_per_second} events/sec · p95 ${load.result.append_or_process_latency_ms.p95} ms · ${load.result.all_events_projected ? 'fully projected' : 'projection still draining'}`
        : load.error ? 'Load test failed: ' + load.error : 'Replay traffic is marked synthetic and cannot create lore.';
  const connection = $('#connection');
  const failed = state.twitch_subscriptions.filter(item => item.status !== 'enabled');
  const missingScopes = failed.some(item => String(item.status).startsWith('missing scope:'));
  connection.innerHTML = state.twitch_eventsub
    ? '<i></i><span>Live · EventSub · ' + esc(state.twitch_login || 'channel') + '</span>'
    : state.twitch_authenticated
      ? '<i style="background:#e2bd75"></i><span>' + (state.twitch_error ? 'EventSub needs attention' : 'EventSub connecting…') + '</span>'
      : '<i></i><span>' + (state.twitch_error ? 'Twitch connection needs attention' : 'Twitch not connected') + '</span>';
  connection.title = [state.twitch_error, ...failed.map(item => item.type + ': ' + item.status)].filter(Boolean).join('\n');
  $('#channel-status').textContent = state.twitch_eventsub ? '@' + state.twitch_login : state.twitch_authenticated ? 'EventSub connecting' : 'Connect Twitch';
  $('.channel strong').textContent = state.twitch_login || 'your channel';
  $('#twitch-connect').classList.toggle('hide', state.twitch_authenticated && !missingScopes);
  $('#connect-hero').classList.toggle('hide', state.twitch_authenticated);
  $('#twitch-retry').classList.toggle('hide', !state.twitch_authenticated || state.twitch_eventsub || !state.twitch_error);
  $('#twitch-disconnect').classList.toggle('hide', !state.twitch_authenticated);
  $('#lore-empty').classList.toggle('hide', state.lore.length > 0);
  $('#lore-list').innerHTML = state.lore.slice(0, 8).map(item => {
    const narratorLabel = item.narrator_provider === 'lm_studio' ? 'LOCAL LM STUDIO' : item.narrator_provider === 'templates' ? 'TEMPLATE STORY' : '';
    const actions = item.status === 'PENDING'
      ? '<button class="approve" data-action="approve" data-id="' + esc(item.id) + '">Approve</button><button data-action="reject" data-id="' + esc(item.id) + '">Pass</button>'
      : item.status === 'ACTIVE'
        ? '<button data-action="retire" data-id="' + esc(item.id) + '">Retire</button>'
        : '<button class="approve" data-action="restore" data-id="' + esc(item.id) + '">Restore for review</button><button data-action="delete" data-id="' + esc(item.id) + '">Delete</button>';
    const statusLabel = item.status === 'PENDING' ? 'NEEDS YOUR OK' : item.status === 'ACTIVE' ? 'APPROVED' : item.status === 'REJECTED' ? 'PASSED' : 'RETIRED';
    return '<article class="lore-card"><div class="lore-meta"><span>✳</span><span class="lore-kind">' + esc(item.kind.replace('_',' ')) + '</span><span>·</span><span>' + statusLabel + '</span>' + (narratorLabel ? '<span>·</span><span>' + narratorLabel + '</span>' : '') + '</div><div class="lore-summary">' + esc(item.summary) + '</div><div class="lore-bottom"><span class="lore-source">' + item.mentions + ' signal' + (item.mentions === 1 ? '' : 's') + '</span><div class="lore-actions">' + actions + '</div></div></article>';
  }).join('');
  const events = state.events.slice(0, 7);
  $('#event-empty').classList.toggle('hide', events.length > 0);
  const icons = {'chat.message':'◌','raid.incoming':'↗','follow':'✦','subscription':'♡','gift_sub':'♡','stream.online':'◉','privacy.forget':'⌑'};
  $('#event-list').innerHTML = events.map(event =>
    '<div class="event-row"><div class="event-icon">' + (icons[event.type] || '·') + '</div><div class="event-copy"><b>' + esc(event.viewer || 'Viewer') + ' <span>' + esc(event.type.replace('.',' ')) + '</span></b><span>' + esc(event.text || '') + '</span></div><div class="event-time">' + timeLabel(event.at) + '</div></div>'
  ).join('');
  const latest = state.utterances[0];
  if (latest) $('#preview-line').textContent = latest.text;
  const activeLoreCount = Number(state.lore_counts?.active ?? state.lore.filter(item => item.status === 'ACTIVE').length);
  renderChapter(state.chapter, state.chapter_history || [], activeLoreCount);
  renderBoss(state.game || {status:'IDLE'});
  const game = state.game || {};
  if (!game.id && state.chapter?.status === 'RESOLVED') {
    $('#boss-idle strong').textContent = 'The field study is ready for its signal hunt.';
    $('#boss-idle span').textContent = 'Start the lore boss encounter, then ask chat to find the hidden word with !guess WORD.';
  }
  $('#boss-replay').disabled = !state.chapter || state.chapter.status !== 'RESOLVED' || game.status === 'ACTIVE';
  const liveRoundForChapter = Boolean(state.chapter && game.chapter_id === state.chapter.id && (game.mode === 'live' || game.live_completed_for_chapter));
  const bossInProgress = game.status === 'ACTIVE';
  const chapterCanAdvance = !state.chapter || (state.chapter.status === 'RESOLVED' && liveRoundForChapter && !bossInProgress && game.status === 'RESOLVED');
  $('#start-chapter').disabled = !window.lorekeeperLive || !chapterCanAdvance || (!state.chapter && activeLoreCount === 0);
  if (bossInProgress) $('#start-chapter').textContent = 'Investigation in progress';
  else if (state.chapter?.status === 'RESOLVED' && !liveRoundForChapter) $('#start-chapter').textContent = 'Investigate the anomaly first';
  else if (state.chapter?.status === 'RESOLVED' && game.status === 'RESOLVED') $('#start-chapter').textContent = 'Start the next field study';
  else if (!state.chapter && activeLoreCount === 0) $('#start-chapter').textContent = 'Approve an observation first';
  $('#start-boss').textContent = 'Start lore boss encounter';
  $('#start-boss').classList.toggle('hide', !state.chapter || state.chapter.status !== 'RESOLVED' || bossInProgress || liveRoundForChapter);
  renderRelics(state.relics || []);
  const streamContext = state.stream_context || {};
  $('#relic-stream-context').textContent = streamContext.category
    ? 'Science-first style · Twitch category: ' + streamContext.category
    : streamContext.source === 'title and tags'
      ? 'Science-first style · uses your live Twitch stream details'
      : 'Science-first style · local narrator personalizes from approved observations';
}
function renderBoss(game) {
  const active = game.status === 'ACTIVE';
  $('#boss-status').textContent = active ? (game.mode === 'simulation' ? 'SIMULATION' : 'LIVE') : game.status || 'WAITING';
  $('#boss-idle').classList.toggle('hide', Boolean(game.id));
  $('#boss-live').classList.toggle('hide', !game.id);
  if (!game.id) return;
  $('#boss-name').textContent = game.boss_name + ' · ' + game.relic_name;
  $('#boss-epithet').textContent = game.boss_epithet;
  $('#boss-clock').textContent = active ? `0:${String(game.seconds_left).padStart(2,'0')} SIGNAL HUNT` : 'ENCOUNTER CLOSED';
  $('#boss-hp').textContent = `SIGNAL DISTANCE ${game.hp} / 100`;
  $('#boss-hp-bar').style.width = `${Math.max(0,Math.min(100,game.hp*100/game.max_hp))}%`;
  $('#boss-meters').textContent = game.best_guess ? `BEST HEAT ${game.best_band} · ${game.best_heat}% · “${game.best_guess}”` : 'NO SIGNALS FOUND YET';
  $('#boss-mode').textContent = game.mode === 'simulation' ? 'SIMULATED CHAT · NOT LIVE TWITCH VIEWERS' : `${game.unique_guesses||0} guesses · one shared hunt`;
  $('#boss-clue').textContent = game.clue || '';
  $('#boss-hint').textContent = game.hint ? `HINT ${game.hint_stage}: ${game.hint}` : 'Hints unlock as the hunt continues.';
  if (game.target_word) $('#boss-hint').textContent = `ANSWER: ${game.target_word.toUpperCase()}`;
  $('#boss-result').textContent = game.result || '';
  $('#boss-feed').innerHTML = (game.feed||[]).map(item => `<article class="history-item"><b>${esc(item.viewer)}</b><span>${esc(item.guess)} · ${esc(item.heat)}% ${esc(gameHeatBand(item.heat))}</span></article>`).join('');
  $('#boss-leaderboard').innerHTML = game.mode === 'simulation' ? '' : (game.leaderboard||[]).map((item,index) => `<article class="history-item"><b>#${index+1} ${esc(item.display_name)} · ${esc(item.title)}</b><span>${item.xp} XP</span></article>`).join('');
}
function gameHeatBand(heat) { return heat >= 88 ? 'BLAZING' : heat >= 70 ? 'HOT' : heat >= 48 ? 'WARM' : heat >= 28 ? 'COOL' : 'COLD'; }
async function refresh() {
  try { render(await (await fetch('/api/state', {cache:'no-store'})).json()); }
  catch { $('#connection').innerHTML = '<i style="background:#cf8077"></i><span>Server reconnecting</span>'; }
}
refresh();
setInterval(refresh, 1500);
const stream = new EventSource('/api/stream');
stream.onmessage = () => refresh();
document.querySelectorAll('.sidebar .nav-item[href^="#"]').forEach(link => link.addEventListener('click', event => {
  const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
  if (!target) return;
  event.preventDefault();
  history.replaceState(null, '', link.hash);
  target.scrollIntoView({behavior:'smooth', block:'start'});
  document.querySelectorAll('.sidebar .nav-item').forEach(item => item.classList.toggle('active', item === link));
}));
$('#start-chapter').addEventListener('click', async () => {
  try { await api('/api/chapter/start'); await refresh(); toast('Field study opened. Chat can vote now.'); }
  catch (error) { toast(error.message); }
});
$('#replay-raid').addEventListener('click', async () => {
  try { await api('/api/replay', {profile:'raid'}); toast('Synthetic raid replay started.'); }
  catch (error) { toast(error.message); }
});
$('#replay-troll').addEventListener('click', async () => {
  try { await api('/api/replay', {profile:'troll'}); toast('Synthetic prompt-injection safety replay started.'); }
  catch (error) { toast(error.message); }
});
$('#load-replay').addEventListener('click', async () => {
  try { await api('/api/load-test', {events:1000,rate:100,profile:'raid'}); toast('1,000-event synthetic load test started.'); }
  catch (error) { toast(error.message); }
});
$('#close-chapter').addEventListener('click', async () => {
  try { await api('/api/chapter/close'); await refresh(); toast('The community finding is revealed.'); }
  catch (error) { toast(error.message); }
});
$('#start-boss').addEventListener('click', async () => {
  try { await api('/api/game/start', {chapter_id:window.currentChapterId}); await refresh(); toast('Signal hunt started. Ask chat to guess with !guess WORD.'); }
  catch (error) { toast(error.message); }
});
$('#boss-replay').addEventListener('click', async () => {
  try { await api('/api/game/replay'); toast('Starting clearly labelled 200-guess simulation.'); }
  catch (error) { toast(error.message); }
});
$('#mute-toggle').addEventListener('click', async () => {
  const muted = !$('#mute-toggle').classList.contains('on');
  try { await api('/api/settings/mute', {muted}); await refresh(); toast(muted ? 'Mascot muted.' : 'Storyteller is ready.'); }
  catch (error) { toast(error.message); }
});
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  try {
    await api('/api/lore/action', {id:button.dataset.id, action:button.dataset.action});
    await refresh();
    toast(button.dataset.action === 'approve' ? 'Memory approved for future chapters.' : 'Memory updated.');
  } catch (error) { toast(error.message); }
});
$('#forget-button').addEventListener('click', async () => {
  const viewer = prompt('Delete a viewer memory. Enter the viewer’s Twitch user ID.');
  if (!viewer?.trim()) return;
  try { await api('/api/privacy/forget', {viewer_id:viewer.trim(), viewer:viewer.trim()}); await refresh(); toast('Viewer memory deleted.'); }
  catch (error) { toast(error.message); }
});
$('#twitch-disconnect').addEventListener('click', async () => {
  try { await api('/api/twitch/disconnect'); await refresh(); toast('Twitch disconnected.'); }
  catch (error) { toast(error.message); }
});
$('#twitch-retry').addEventListener('click', async () => {
  try { await api('/api/twitch/reconnect'); await refresh(); toast('Retrying Twitch EventSub.'); }
  catch (error) { toast(error.message); }
});
