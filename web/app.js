const $ = (s) => document.querySelector(s);
const esc = (s='') => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timeLabel = (value) => { if(!value)return 'now'; const d=new Date(value); return isNaN(d)?'now':d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); };
let toastTimer;
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),2400)}
async function api(path, data){const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});const result=await res.json();if(!res.ok)throw new Error(result.error||'Request failed');return result}
function render(state){
  $('#stat-events').textContent=state.stats.received;
  $('#stat-reactions').textContent=state.stats.reactions;
  const loreCount=state.lore.filter(x=>x.status==='ACTIVE').length;
  $('#stat-lore').textContent=loreCount;
  $('#stat-pending').textContent=state.pending;
  $('#pending-pill').textContent=state.pending;
  $('#side-pending').textContent=state.pending;
  $('#side-pending').classList.toggle('hide',!state.pending);
  $('#mute-toggle').classList.toggle('on',state.muted);
  $('#mute-caption').textContent=state.muted?'Panic mute is on':'The keeper is ready to speak';
  $('#mute-toggle').setAttribute('aria-label',state.muted?'Unmute mascot':'Mute mascot');
  $('#pipeline-label').textContent=state.stats.replay_running?'REPLAYING':state.twitch_connected?'LIVE':'READY';
  $('#uptime').textContent=state.stats.replay_running?'Moments are flowing through the archive':`${state.event_log==='redpanda'?'Redpanda event log':'Local event log'} · ${state.stats.uptime}s uptime`;
  const conn=$('#connection');const failedSubs=state.twitch_subscriptions.filter(x=>x.status!=='enabled');const missingScopes=failedSubs.filter(x=>String(x.status).startsWith('missing scope:'));conn.innerHTML=state.twitch_eventsub?`<i></i><span>Live · EventSub${failedSubs.length?` · ${state.twitch_subscriptions.length-failedSubs.length}/${state.twitch_subscriptions.length}`:''}</span>`:state.twitch_authenticated?state.twitch_error?'<i style="background:#cf8077"></i><span>Twitch authorized · EventSub needs attention</span>':'<i style="background:#e2bd75"></i><span>Twitch authorized · EventSub connecting…</span>':state.twitch_error?'<i style="background:#cf8077"></i><span>Twitch connection needs attention</span>':'<i></i><span>'+(state.stats.replay_running?'Replay in progress':'Twitch not connected')+'</span>';
  conn.title=[state.twitch_error,...failedSubs.map(x=>x.type+': '+x.status)].filter(Boolean).join('\n');
  $('#channel-status').textContent=state.twitch_eventsub?'EventSub connected':state.twitch_authenticated?state.twitch_error?'EventSub needs retry':'Connecting…':'Connect Twitch';
  const needsScopeReview=missingScopes.length>0;
  $('#twitch-connect').classList.toggle('hide',state.twitch_authenticated&&!needsScopeReview);
  $('#twitch-connect').textContent=state.twitch_authenticated?'Review permissions':'Connect Twitch';
  $('#connect-hero').classList.toggle('hide',state.twitch_authenticated);
  $('#twitch-retry').classList.toggle('hide',!state.twitch_authenticated||state.twitch_eventsub||!state.twitch_error);
  $('#twitch-disconnect').classList.toggle('hide',!state.twitch_authenticated);
  if(state.twitch_login){$('#channel-status').textContent='@'+state.twitch_login;$('.channel strong').textContent=state.twitch_login;}
  $('#lore-empty').classList.toggle('hide',state.lore.length>0);
  const lore=state.lore.slice(0,3);
  $('#lore-list').innerHTML=lore.map(item=>`<article class="lore-card"><div class="lore-meta"><span>✳</span><span class="lore-kind">${esc(item.kind.replace('_',' '))}</span><span>·</span><span>${item.status==='PENDING'?'NEEDS YOUR OK':esc(item.status)}</span></div><div class="lore-summary">${esc(item.summary)}</div><div class="lore-bottom"><span class="lore-source">${item.mentions} signal${item.mentions===1?'':'s'} · ${item.evidence?JSON.parse(item.evidence).length:1} pieces of evidence</span><div class="lore-actions">${item.status==='PENDING'?`<button class="approve" data-action="approve" data-id="${esc(item.id)}">Keep this</button><button data-action="reject" data-id="${esc(item.id)}">Pass</button>`:`<button data-action="retire" data-id="${esc(item.id)}">Retire</button>`}</div></div></article>`).join('');
  const events=state.events.slice(0,6);
  $('#event-empty').classList.toggle('hide',events.length>0);
  const icons={'chat.message':'◌','raid.incoming':'↗','follow':'✦','subscription':'♡','gift_sub':'♡','stream.online':'◉','privacy.forget':'⌑'};
  $('#event-list').innerHTML=events.map(e=>`<div class="event-row"><div class="event-icon">${icons[e.type]||'·'}</div><div class="event-copy"><b>${esc(e.viewer||'Viewer')} <span style="color:#758174;font-weight:400">${esc(e.type.replace('.',' '))}</span></b><span>${esc(e.text||'')}</span></div><div class="event-time">${timeLabel(e.at)}</div></div>`).join('');
  if(state.moments.length){const m=state.moments[0];$('#moment-strip').innerHTML=`<div class="moment-marker"><span>✳</span><div><b>${esc(m.title)}</b><small>${esc(m.detail)} · ${Math.round(m.score*100)}% signal</small></div></div>`}
  const latest=state.utterances[0];if(latest)$('#preview-line').textContent=latest.text;
}
async function refresh(){try{render(await (await fetch('/api/state',{cache:'no-store'})).json())}catch(e){$('#connection').innerHTML='<i style="background:#cf8077"></i><span>Server reconnecting</span>'}}
refresh();setInterval(refresh,1800);
const stream=new EventSource('/api/stream');stream.onmessage=()=>refresh();
$('#start-replay').addEventListener('click',()=>$('#modal').classList.add('open'));
$('#modal-close').addEventListener('click',()=>$('#modal').classList.remove('open'));
$('#modal').addEventListener('click',e=>{if(e.target.id==='modal')$('#modal').classList.remove('open')});
document.querySelectorAll('[data-profile]').forEach(button=>button.addEventListener('click',async()=>{try{await api('/api/replay',{profile:button.dataset.profile});$('#modal').classList.remove('open');toast('Replay started — watch your community story unfold.')}catch(e){toast(e.message)}}));
$('#recap-button').addEventListener('click',async()=>{try{const r=await api('/api/recap');$('#preview-line').textContent=r.recap.text;toast('“Previously on…” is on the overlay.')}catch(e){toast(e.message)}});
$('#mute-toggle').addEventListener('click',async()=>{const muted=!$('#mute-toggle').classList.contains('on');try{await api('/api/settings/mute',{muted});refresh();toast(muted?'Mascot muted. The stream stays yours.':'Mascot is ready to speak.')}catch(e){toast(e.message)}});
document.addEventListener('click',async e=>{const button=e.target.closest('[data-action]');if(!button)return;try{await api('/api/lore/action',{id:button.dataset.id,action:button.dataset.action});refresh();toast(button.dataset.action==='approve'?'Added to community lore.':'Lore updated.')}catch(err){toast(err.message)}});
document.querySelectorAll('[data-scroll]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.scroll)?.scrollIntoView({behavior:'smooth',block:'start'})));
$('#view-all').addEventListener('click',()=>$('#lore-section').scrollIntoView({behavior:'smooth',block:'center'}));
$('#forget-button').addEventListener('click',async()=>{const viewer=prompt('Delete a viewer’s memory. For replay, enter a demo username such as pixel_pete. For live Twitch, enter the viewer’s Twitch user ID.');if(!viewer?.trim())return;const demo=!$('#connection').textContent.includes('Live · EventSub');try{await api('/api/privacy/forget',{viewer_id:viewer.trim(),viewer:viewer.trim(),demo});refresh();toast('Viewer memory deleted from the archive.')}catch(e){toast(e.message)}});
$('#twitch-disconnect').addEventListener('click',async()=>{try{await api('/api/twitch/disconnect');refresh();toast('Twitch has been disconnected.')}catch(e){toast(e.message)}});
$('#twitch-retry').addEventListener('click',async()=>{try{await api('/api/twitch/reconnect');refresh();toast('Retrying the Twitch EventSub connection.')}catch(e){toast(e.message)}});
