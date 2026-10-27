from __future__ import annotations
import asyncio, json, os, random, threading, time, urllib.error, urllib.parse, urllib.request
from . import runtime
from .projection import emit_event

twitch_status={"connected":False,"eventsub":False,"channel":"","login":"","error":"","subscriptions":[]}

auth_states: dict[str,float] = {}

token_lock=threading.RLock()

twitch_stop=threading.Event()

twitch_thread=None

twitch_tokens=None

TWITCH_SCOPES=["user:read:chat","moderator:read:followers","channel:read:subscriptions","channel:read:redemptions","bits:read"]

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
        with urllib.request.urlopen(request,timeout=12) as response: return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode("utf-8","replace")[:500]
        raise RuntimeError(f"Twitch API {exc.code}: {detail}") from exc

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
    twitch_status["subscriptions"]=registered

def eventsub_event(message):
    meta=message.get("metadata",{}); sub=message.get("payload",{}).get("subscription",{}); raw=message.get("payload",{}).get("event",{})
    kind=sub.get("type",""); event_id=meta.get("message_id")
    if not event_id or not raw: return
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
    else:
        emit_event(event_type,viewer,text,source="twitch_eventsub",mode="live",viewer_id=viewer_id,payload={"display_name":viewer,"text":text,"twitch_event_type":kind,"broadcaster_user_id":raw.get("broadcaster_user_id")},event_id="twitch:eventsub:"+event_id)

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
