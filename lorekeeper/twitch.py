from __future__ import annotations
import asyncio, json, os, queue, random, threading, time, urllib.error, urllib.parse, urllib.request
from . import runtime
from .projection import emit_event

twitch_status={"connected":False,"eventsub":False,"channel":"","login":"","error":"","subscriptions":[],"whisper_status":"Waiting for Twitch authorization."}

auth_states: dict[str,float] = {}

token_lock=threading.RLock()

twitch_stop=threading.Event()

twitch_thread=None

twitch_tokens=None

TWITCH_SCOPES=["user:read:chat","user:manage:whispers","moderator:read:followers","channel:read:subscriptions","channel:read:redemptions","bits:read"]

_whisper_queue=queue.Queue(maxsize=256)
_whisper_worker_lock=threading.Lock()
_whisper_worker_thread=None
_whisper_request_lock=threading.Lock()
_whisper_last_request={}

def client_id(): return os.environ.get("TWITCH_CLIENT_ID","").strip()

def client_secret(): return os.environ.get("TWITCH_CLIENT_SECRET","").strip()

def redirect_uri(): return os.environ.get("TWITCH_REDIRECT_URI","http://localhost:8787/auth/callback").strip()

def token_cipher():
    from cryptography.fernet import Fernet
    key=os.environ.get("TOKEN_ENCRYPTION_KEY","").strip().encode()
    if not key: raise RuntimeError("Set TOKEN_ENCRYPTION_KEY before connecting Twitch.")
    return Fernet(key)

def persist_tokens(tokens):
    global twitch_tokens
    encrypted=token_cipher().encrypt(json.dumps(tokens).encode())
    temporary=runtime.TOKEN_FILE.with_suffix(".tmp")
    temporary.write_bytes(encrypted); os.replace(temporary,runtime.TOKEN_FILE)
    try: runtime.TOKEN_FILE.chmod(0o600)
    except OSError: pass
    with token_lock: twitch_tokens=tokens; runtime.twitch_tokens=tokens

def restore_tokens():
    global twitch_tokens
    if not runtime.TOKEN_FILE.exists(): return
    try:
        tokens=json.loads(token_cipher().decrypt(runtime.TOKEN_FILE.read_bytes()))
        with token_lock: twitch_tokens=tokens; runtime.twitch_tokens=tokens
    except Exception as exc:
        twitch_status["error"]=f"Saved Twitch authorization unavailable: {exc}"

def oauth_post(values):
    body=urllib.parse.urlencode(values).encode()
    request=urllib.request.Request("https://id.twitch.tv/oauth2/token",data=body,headers={"Content-Type":"application/x-www-form-urlencoded"},method="POST")
    with urllib.request.urlopen(request,timeout=12) as response: return json.loads(response.read())

def revoke_twitch_token(access_token):
    body=urllib.parse.urlencode({"client_id":client_id(),"token":access_token}).encode()
    request=urllib.request.Request("https://id.twitch.tv/oauth2/revoke",data=body,headers={"Content-Type":"application/x-www-form-urlencoded"},method="POST")
    with urllib.request.urlopen(request,timeout=12): pass

def helix_request(path, token, method="GET", body=None):
    payload=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request("https://api.twitch.tv/helix/"+path,data=payload,headers={"Client-Id":client_id(),"Authorization":"Bearer "+token,"Content-Type":"application/json"},method=method)
    try:
        with urllib.request.urlopen(request,timeout=12) as response:
            payload=response.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode("utf-8","replace")[:500]
        raise RuntimeError(f"Twitch API {exc.code}: {detail}") from exc

def _whisper_worker():
    """Send personal profile replies off the EventSub and projection threads."""
    from datetime import datetime, timezone
    last_send=0.0
    while True:
        viewer_id,message=_whisper_queue.get()
        try:
            with token_lock: tokens=dict(twitch_tokens or {})
            scopes=set(tokens.get("scope",[]))
            if "user:manage:whispers" not in scopes:
                twitch_status["whisper_status"]="Reconnect Twitch and grant user:manage:whispers to enable private replies."
                continue
            if not str(viewer_id).isdigit():
                twitch_status["whisper_status"]="Private reply skipped: Twitch viewer ID is unavailable."
                continue
            utc_day=datetime.now(timezone.utc).date().isoformat()
            with runtime.db() as c:
                already=c.execute("SELECT 1 FROM whisper_recipients WHERE utc_day=? AND viewer_id=?",(utc_day,str(viewer_id))).fetchone()
                unique_today=c.execute("SELECT count(*) n FROM whisper_recipients WHERE utc_day=?",(utc_day,)).fetchone()["n"]
            if not already and unique_today>=40:
                twitch_status["whisper_status"]="Daily Twitch Whisper limit reached (40 unique viewers)."
                continue
            delay=0.4-(time.monotonic()-last_send)
            if delay>0: time.sleep(delay)
            token=current_token()
            query=urllib.parse.urlencode({"from_user_id":tokens["user_id"],"to_user_id":str(viewer_id)})
            helix_request("whispers?"+query,token,"POST",{"message":" ".join(str(message).split())[:450]})
            sent_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
            with runtime.db() as c:
                c.execute("INSERT INTO whisper_recipients(utc_day,viewer_id,sent_at) VALUES(?,?,?) ON CONFLICT(utc_day,viewer_id) DO UPDATE SET sent_at=excluded.sent_at",
                          (utc_day,str(viewer_id),sent_at))
            last_send=time.monotonic()
            twitch_status["whisper_status"]="Private response accepted by Twitch. Delivery may depend on the viewer's Whisper settings."
        except Exception as exc:
            twitch_status["whisper_status"]=f"Private reply failed: {str(exc)[:220]}"
            print(f"Twitch Whisper delivery failed: {str(exc)[:220]}",flush=True)
        finally:
            _whisper_queue.task_done()

def _start_whisper_worker():
    global _whisper_worker_thread
    with _whisper_worker_lock:
        if _whisper_worker_thread and _whisper_worker_thread.is_alive(): return
        _whisper_worker_thread=threading.Thread(target=_whisper_worker,daemon=True,name="twitch-whisper-sender")
        _whisper_worker_thread.start()

def enqueue_whisper(viewer_id,message,force=False):
    """Queue a rate-limited private message without blocking chat ingestion."""
    global _whisper_last_request
    with token_lock: tokens=dict(twitch_tokens or {})
    if not tokens:
        twitch_status["whisper_status"]="Connect Twitch to enable private viewer replies."
        return False
    if "user:manage:whispers" not in set(tokens.get("scope",[])):
        twitch_status["whisper_status"]="Reconnect Twitch and grant user:manage:whispers to enable private replies."
        return False
    viewer_id=str(viewer_id or "")
    if not viewer_id.isdigit(): return False
    now_monotonic=time.monotonic()
    with _whisper_request_lock:
        previous=_whisper_last_request.get(viewer_id,0.0)
        if not force and now_monotonic-previous<15:
            twitch_status["whisper_status"]="Private reply rate limited; wait a few seconds and try again."
            return False
        _whisper_last_request[viewer_id]=now_monotonic
        if len(_whisper_last_request)>2000:
            _whisper_last_request={key:value for key,value in _whisper_last_request.items() if now_monotonic-value<3600}
    try:
        _whisper_queue.put_nowait((viewer_id,str(message)))
    except queue.Full:
        twitch_status["whisper_status"]="Private reply queue is busy; try again shortly."
        return False
    _start_whisper_worker()
    twitch_status["whisper_status"]="Private reply queued for Twitch."
    return True

def current_token():
    global twitch_tokens
    with token_lock: tokens=dict(twitch_tokens or {})
    if not tokens: raise RuntimeError("Twitch is not connected.")
    if tokens.get("expires_at",0) < time.time()+120:
        refreshed=oauth_post({"client_id":client_id(),"client_secret":client_secret(),"grant_type":"refresh_token","refresh_token":tokens["refresh_token"]})
        tokens.update(refreshed); tokens["expires_at"]=time.time()+int(refreshed["expires_in"]); persist_tokens(tokens)
    return tokens["access_token"]

def start_twitch_worker():
    global twitch_stop,twitch_thread
    if twitch_thread and twitch_thread.is_alive():
        twitch_stop.set(); twitch_thread.join(timeout=15)
        if twitch_thread.is_alive():
            twitch_status["error"]="Previous EventSub session is still closing; retry Connect Twitch in a few seconds."
            return False
    twitch_stop=threading.Event()
    twitch_thread=threading.Thread(target=twitch_eventsub_worker,args=(twitch_stop,),daemon=True,name="twitch-eventsub")
    twitch_thread.start()
    return True

def subscribe_eventsub(access,session_id,broadcaster_id,scopes):
    definitions=[
      ("channel.chat.message","1",{"broadcaster_user_id":broadcaster_id,"user_id":broadcaster_id},"user:read:chat"),
      ("channel.update","2",{"broadcaster_user_id":broadcaster_id},None),
      ("stream.online","1",{"broadcaster_user_id":broadcaster_id},None),
      ("stream.offline","1",{"broadcaster_user_id":broadcaster_id},None),
      ("channel.raid","1",{"to_broadcaster_user_id":broadcaster_id},None),
      ("channel.follow","2",{"broadcaster_user_id":broadcaster_id,"moderator_user_id":broadcaster_id},"moderator:read:followers"),
      ("channel.subscribe","1",{"broadcaster_user_id":broadcaster_id},"channel:read:subscriptions"),
      ("channel.subscription.message","1",{"broadcaster_user_id":broadcaster_id},"channel:read:subscriptions"),
      ("channel.subscription.gift","1",{"broadcaster_user_id":broadcaster_id},"channel:read:subscriptions"),
      ("channel.cheer","1",{"broadcaster_user_id":broadcaster_id},"bits:read"),
      ("channel.channel_points_custom_reward_redemption.add","1",{"broadcaster_user_id":broadcaster_id},"channel:read:redemptions"),
    ]
    registered=[]
    for event_type,version,condition,scope in definitions:
        if scope and scope not in scopes:
            registered.append({"type":event_type,"status":"missing scope: "+scope}); continue
        try:
            result=helix_request("eventsub/subscriptions",access,"POST",{"type":event_type,"version":version,"condition":condition,"transport":{"method":"websocket","session_id":session_id}})
            registered.append({"type":event_type,"status":"enabled" if result.get("data") else "unknown"})
        except Exception as exc:
            registered.append({"type":event_type,"status":str(exc)[:160]})
            print(f"EventSub subscription {event_type} failed: {exc}",flush=True)
    if "user:manage:whispers" not in scopes:
        registered.append({"type":"private viewer replies","status":"missing scope: user:manage:whispers"})
    twitch_status["subscriptions"]=registered

def eventsub_event(message):
    meta=message.get("metadata",{}); sub=message.get("payload",{}).get("subscription",{}); raw=message.get("payload",{}).get("event",{})
    kind=sub.get("type",""); event_id=meta.get("message_id")
    if not event_id or not raw: return
    if kind=="channel.update":
        from .relics import update_stream_context
        context=update_stream_context(raw.get("category_name",""),raw.get("title",""))
        from .runtime import publish
        publish({"kind":"stream_context","context":context})
        return
    names={"channel.chat.message":"chat.message","stream.online":"stream.online","stream.offline":"stream.offline","channel.follow":"follow","channel.subscribe":"subscription","channel.subscription.message":"subscription","channel.subscription.gift":"gift_sub","channel.raid":"raid.incoming","channel.channel_points_custom_reward_redemption.add":"redemption","channel.cheer":"cheer"}
    event_type=names.get(kind)
    if not event_type: return
    viewer=raw.get("chatter_user_name") or raw.get("user_name") or raw.get("from_broadcaster_user_name") or "A viewer"
    viewer_id=raw.get("chatter_user_id") or raw.get("user_id") or raw.get("from_broadcaster_user_id") or "twitch-anonymous"
    if event_type=="chat.message": text=raw.get("message",{}).get("text","")
    elif event_type=="raid.incoming": text=f"{raw.get('viewers',0)} viewers raided from {raw.get('from_broadcaster_user_name','another channel')}"
    elif event_type=="redemption": text=f"Redeemed {raw.get('reward',{}).get('title','a channel reward')}: {raw.get('user_input','')}"
    elif kind=="channel.subscription.message": text=f"Resubscribed for {raw.get('cumulative_months',1)} months"
    elif event_type=="subscription": text=f"Subscribed at tier {raw.get('tier','1')}"
    elif event_type=="gift_sub": text=f"Gifted {raw.get('total',1)} subscription(s)"
    elif event_type=="cheer": text=f"Cheered {raw.get('bits',0)} bits"
    elif event_type=="follow": text="Followed the channel"
    elif event_type=="stream.online": text="The stream is live"
    else: text="The stream ended"
    if event_type=="chat.message" and text.lower().startswith("!forgetme"):
        from .controls import forget_viewer
        event_text="Viewer requested memory deletion"
        emit_event(event_type,viewer,event_text,source="twitch_eventsub",mode="live",viewer_id=viewer_id,payload={"display_name":viewer,"text":event_text,"twitch_event_type":kind},event_id="twitch:eventsub:"+event_id)
        forget_viewer(viewer_id,viewer,ack=True)
    elif event_type=="chat.message" and (text.strip().lower()=="!rememberme" or text.strip().lower().startswith("!rememberme ")):
        from .controls import remember_viewer
        event_text="Viewer opted back in to memory"
        emit_event(event_type,viewer,event_text,source="twitch_eventsub",mode="live",viewer_id=viewer_id,payload={"display_name":viewer,"text":event_text,"twitch_event_type":kind},event_id="twitch:eventsub:"+event_id)
        remember_viewer(viewer_id,viewer)
    elif event_type=="chat.message" and text.lower().startswith("!whatdoyouknow"):
        with runtime.db() as c: forgotten=c.execute("SELECT 1 FROM forgotten WHERE viewer_id=?",(str(viewer_id),)).fetchone() is not None
        # Preserve the command for projection, where the private profile reply is
        # assembled. Forgotten viewers are redacted there and receive a generic
        # private response below.
        emit_event(event_type,viewer,text,source="twitch_eventsub",mode="live",viewer_id=viewer_id,payload={"display_name":viewer,"text":text,"twitch_event_type":kind},event_id="twitch:eventsub:"+event_id)
        if forgotten:
            # Do not put personal account status on the shared stream overlay.
            enqueue_whisper(viewer_id,"You don't have any saved memories or profile stats in Lorekeeper.")
    else:
        emit_event(event_type,viewer,text,source="twitch_eventsub",mode="live",viewer_id=viewer_id,payload={"display_name":viewer,"text":text,"twitch_event_type":kind,"broadcaster_user_id":raw.get("broadcaster_user_id"),"stream_id":raw.get("id") if event_type=="stream.online" else None},event_id="twitch:eventsub:"+event_id)

def sync_stream_context(access,broadcaster_id):
    """Seed category context at connect; channel.update keeps it fresh afterwards."""
    try:
        query=urllib.parse.urlencode({"broadcaster_id":broadcaster_id})
        channels=helix_request("channels?"+query,access).get("data",[])
        if not channels: return
        channel=channels[0]
        from .relics import update_stream_context
        context=update_stream_context(channel.get("game_name",""),channel.get("title",""),channel.get("tags",[]))
        from .runtime import publish
        publish({"kind":"stream_context","context":context})
    except Exception as exc:
        # Category discovery is optional; lore text remains the deterministic fallback.
        print(f"Twitch stream category unavailable; relics will use memory context: {str(exc)[:180]}",flush=True)

async def eventsub_connection(url,stop,reconnecting=False):
    import websockets
    async with websockets.connect(url,ping_interval=None,close_timeout=5,max_size=2**20) as ws:
        twitch_status.update(connected=True,eventsub=False,error="")
        last_message=time.monotonic(); keepalive_timeout=10
        while not stop.is_set():
            try: raw=await asyncio.wait_for(ws.recv(),timeout=1)
            except asyncio.TimeoutError:
                if time.monotonic()-last_message>keepalive_timeout+5: raise TimeoutError("Twitch EventSub keepalive timed out")
                continue
            last_message=time.monotonic()
            message=json.loads(raw); meta=message.get("metadata",{}); kind=meta.get("message_type")
            if kind=="session_welcome":
                keepalive_timeout=message.get("payload",{}).get("session",{}).get("keepalive_timeout_seconds") or 10
                twitch_status.update(connected=True,eventsub=True,error="")
                runtime.eventsub_connected.set(1)
                if not reconnecting:
                    session=message["payload"]["session"]["id"]; token=await asyncio.to_thread(current_token)
                    with token_lock: authorized=dict(twitch_tokens or {})
                    twitch_status["channel"]=authorized.get("login",""); twitch_status["login"]=authorized.get("login","")
                    scopes=set(authorized.get("scope",[]))
                    twitch_status["whisper_status"]=("Private viewer replies enabled." if "user:manage:whispers" in scopes
                                                      else "Reconnect Twitch and grant user:manage:whispers to enable private replies.")
                    await asyncio.to_thread(sync_stream_context,token,authorized["user_id"])
                    await asyncio.to_thread(subscribe_eventsub,token,session,authorized["user_id"],scopes)
                reconnecting=False
            elif kind=="notification":
                await asyncio.to_thread(eventsub_event,message)
            elif kind=="session_reconnect":
                reconnect_url=message.get("payload",{}).get("session",{}).get("reconnect_url")
                if reconnect_url: await eventsub_connection(reconnect_url,stop,True)
                return
            elif kind=="revocation":
                twitch_status["error"]="Twitch revoked an EventSub subscription: "+str(message.get("payload",{}).get("subscription",{}).get("status","unknown"))

def twitch_eventsub_worker(stop):
    import asyncio
    delay=1
    while not stop.is_set():
        try:
            current_token()
            asyncio.run(eventsub_connection("wss://eventsub.wss.twitch.tv/ws",stop))
            delay=1
        except Exception as exc:
            twitch_status.update(connected=False,eventsub=False,error=str(exc)[:300])
            runtime.eventsub_connected.set(0)
            print(f"Twitch EventSub disconnected; reconnecting: {exc}",flush=True)
            stop.wait(random.uniform(delay*0.8,delay*1.2)); delay=min(30,delay*2)
