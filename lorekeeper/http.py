from __future__ import annotations
import html, json, os, queue, re, secrets, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from . import runtime, twitch, controls, story, relics, game, signal_game
from .memory import summarize_lore
from .runtime import ROOT, db, listeners, lock, now, publish, stats, time_cutoff

def get_state():
    with db() as c:
        event_rows=c.execute("SELECT * FROM events ORDER BY at DESC LIMIT 35").fetchall()
        events=[]
        for row in event_rows:
            event=dict(row)
            if event.get("type")=="chat.message":
                # Keep raw text in the short-retention event log for processing,
                # but don't echo viewer messages through the dashboard API/feed.
                event["text"]="Twitch chat message received"
                event["payload"]="{}"
            events.append(event)
        lore_rows=c.execute("SELECT l.*,e.provider AS narrator_provider FROM lore l LEFT JOIN lore_enrichments e ON e.lore_id=l.id ORDER BY CASE l.status WHEN 'PENDING' THEN 0 ELSE 1 END,l.created_at DESC LIMIT 30").fetchall()
        lore=[{**dict(item),"summary":summarize_lore(item["summary"])} for item in lore_rows]
        moments=c.execute("SELECT * FROM moments ORDER BY at DESC LIMIT 8").fetchall()
        utterances=c.execute("SELECT * FROM utterances ORDER BY at DESC LIMIT 12").fetchall()
        muted=c.execute("SELECT value FROM settings WHERE key='muted'").fetchone()["value"]=="true"
        pending=c.execute("SELECT count(*) n FROM lore WHERE status='PENDING'").fetchone()["n"]
        lore_counts={row["status"].lower():row["n"] for row in c.execute("SELECT status,count(*) n FROM lore GROUP BY status").fetchall()}
        recent_events=c.execute("SELECT count(*) n FROM events WHERE at>=?",(time_cutoff(10),)).fetchone()["n"]
        relic_inventory=relics.list_relics(c, limit=20)
        relic_stream_context=relics.stream_context(c)
    with db() as c:
        totals={"received":c.execute("SELECT count(*) n FROM events").fetchone()["n"],"reactions":c.execute("SELECT count(*) n FROM utterances").fetchone()["n"]}
    llm_provider=os.environ.get("LOREKEEPER_LLM_PROVIDER","none").strip().casefold()
    return {"events":[dict(x) for x in events],"lore":lore,"lore_counts":lore_counts,"relics":relic_inventory,"stream_context":relic_stream_context,"moments":[dict(x) for x in moments],"utterances":[dict(x) for x in utterances],"chapter":story.current_state(),"chapter_history":story.recent_history(),"game":signal_game.state(),"narrator_provider":"LM Studio configured · local" if llm_provider in ("lm_studio","lmstudio") else "Curated templates","muted":muted,"pending":pending,"stats":{**stats,**totals,"uptime":int(time.time()-stats['started_at'])},"pipeline":pipeline_snapshot(recent_events),"load_test":controls.load_status.copy(),"event_log":"redpanda" if runtime.event_bus else "sqlite-local","twitch_connected":twitch.twitch_status["connected"],"twitch_eventsub":twitch.twitch_status["eventsub"],"twitch_authenticated":bool(twitch.twitch_tokens),"twitch_channel":twitch.twitch_status["channel"],"twitch_login":twitch.twitch_status["login"],"twitch_error":twitch.twitch_status["error"],"twitch_subscriptions":twitch.twitch_status["subscriptions"]}


def pipeline_snapshot(recent_events=0):
    lag=0
    try:
        from .event_bus import consumer_lag
        lag=max((sample.value for metric in consumer_lag.collect() for sample in metric.samples),default=0)
    except Exception:
        pass
    p95_ms=None
    try:
        histogram=runtime.projection_latency.collect()[0]
        buckets=[]; total=0
        for sample in histogram.samples:
            if sample.name.endswith("_count"):
                total=sample.value
            elif sample.name.endswith("_bucket"):
                edge=float(sample.labels.get("le","inf"))
                buckets.append((edge,sample.value))
        if total:
            target=total*.95
            seconds=next((edge for edge,count in sorted(buckets) if count>=target),None)
            if seconds is not None and seconds != float("inf"):
                p95_ms=round(seconds*1000,1)
    except Exception:
        pass
    return {"events_per_second":round(float(recent_events)/10,1),"consumer_lag":int(lag),"projection_p95_ms":p95_ms}

def oauth_callback_page(title,message):
    import html
    dashboard_url=os.environ.get("LOREKEEPER_DASHBOARD_URL","http://localhost:8787").strip()
    return "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Lorekeeper Twitch setup</title><body style='margin:0;background:#101510;color:#ecf0e5;font:16px system-ui;display:grid;place-items:center;min-height:100vh'><main style='max-width:520px;padding:36px;border:1px solid #344033;border-radius:12px;background:#171e18'><h1 style='color:#c6e4a5'>"+html.escape(title)+"</h1><p>"+html.escape(message)+"</p><a style='color:#c6e4a5' href='"+html.escape(dashboard_url,quote=True)+"'>Return to Lorekeeper</a></main></body>"

class Handler(BaseHTTPRequestHandler):
    server_version="Lorekeeper/0.1"
    def log_message(self,fmt,*args): print(f"{self.log_date_time_string()} {self.client_address[0]} {self.command} {urlparse(self.path).path}",flush=True)
    def send_json(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
    def send_html(self,content,status=200):
        data=content.encode(); self.send_response(status); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
    def body(self):
        try: return json.loads(self.rfile.read(int(self.headers.get("Content-Length","0")) or 0) or b"{}")
        except Exception: return {}
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/auth/connect":
            if not twitch.client_id() or not twitch.client_secret(): return self.send_html(oauth_callback_page("Add your Twitch app credentials","Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in your local .env file, then restart Lorekeeper."),503)
            try: twitch.token_cipher()
            except Exception as exc: return self.send_html(oauth_callback_page("Set the token encryption key",str(exc)),503)
            started=time.time()
            for expired in [key for key,expires in twitch.auth_states.items() if expires<started]: twitch.auth_states.pop(expired,None)
            state=secrets.token_urlsafe(32); twitch.auth_states[state]=started+600
            query=urllib.parse.urlencode({"client_id":twitch.client_id(),"redirect_uri":twitch.redirect_uri(),"response_type":"code","scope":" ".join(twitch.TWITCH_SCOPES),"state":state,"force_verify":"true"})
            self.send_response(302); self.send_header("Location","https://id.twitch.tv/oauth2/authorize?"+query); self.end_headers(); return
        if path=="/auth/callback":
            query=parse_qs(urlparse(self.path).query); state=query.get("state",[""])[0]
            if query.get("error"): return self.send_html(oauth_callback_page("Twitch connection cancelled",query.get("error_description",["Authorization was not granted."])[0]),400)
            if twitch.auth_states.pop(state,0)<time.time(): return self.send_html(oauth_callback_page("Authorization expired","Return to Lorekeeper and connect Twitch again."),400)
            try:
                token=twitch.oauth_post({"client_id":twitch.client_id(),"client_secret":twitch.client_secret(),"code":query.get("code",[""])[0],"grant_type":"authorization_code","redirect_uri":twitch.redirect_uri()})
                request=urllib.request.Request("https://id.twitch.tv/oauth2/validate",headers={"Authorization":"OAuth "+token["access_token"]})
                with urllib.request.urlopen(request,timeout=12) as response: identity=json.loads(response.read())
                if identity.get("client_id")!=twitch.client_id(): raise RuntimeError("Twitch returned an unexpected client ID.")
                token.update({"user_id":identity["user_id"],"login":identity["login"],"scope":identity.get("scopes",[]),"expires_at":time.time()+int(token["expires_in"])})
                twitch.persist_tokens(token); twitch.twitch_status.update(channel=token["login"],login=token["login"],error="")
                if not twitch.start_twitch_worker(): return self.send_html(oauth_callback_page("Twitch authorized; EventSub is still shutting down",twitch.twitch_status["error"]),503)
                return self.send_html(oauth_callback_page("Twitch connected",f"Authorized as {token['login']}. EventSub is connecting; return to the dashboard to see live status."))
            except Exception as exc: return self.send_html(oauth_callback_page("Twitch connection failed",str(exc)),502)
        if path=="/api/state": return self.send_json(get_state())
        if path=="/api/health": return self.send_json({"ok":True,"service":"lorekeeper","event_log":"redpanda" if runtime.event_bus else "sqlite-local","eventsub":twitch.twitch_status["eventsub"]})
        if path=="/api/ready":
            try:
                with db() as c: c.execute("SELECT 1").fetchone()
                if runtime.event_bus and not runtime.event_bus.healthy: raise RuntimeError("Kafka projection consumer is not healthy")
                if runtime.redis_client: runtime.redis_client.ping()
                return self.send_json({"ready":True})
            except Exception as exc: return self.send_json({"ready":False,"error":str(exc)},503)
        if path=="/metrics":
            body=generate_latest(); self.send_response(200); self.send_header("Content-Type",CONTENT_TYPE_LATEST); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return
        if path=="/api/stream": return self.sse()
        if path in ("/","/overlay"):
            file=ROOT/"web"/("overlay.html" if path=="/overlay" else "index.html")
        elif path in ("/web/app.js","/web/style.css","/web/twitch.css"):
            file=(ROOT/path.lstrip("/")).resolve()
            if ROOT.joinpath("web").resolve() not in file.parents: return self.send_json({"error":"not found"},404)
        else: return self.send_json({"error":"not found"},404)
        if not file.is_file(): return self.send_json({"error":"not found"},404)
        mime="text/html; charset=utf-8" if file.suffix==".html" else "text/css; charset=utf-8" if file.suffix==".css" else "text/javascript; charset=utf-8"
        body=file.read_bytes(); self.send_response(200); self.send_header("Content-Type",mime); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(body)
    def sse(self):
        self.send_response(200); self.send_header("Content-Type","text/event-stream"); self.send_header("Cache-Control","no-cache"); self.send_header("Connection","keep-alive"); self.end_headers()
        q=queue.Queue(maxsize=100)
        query_since=parse_qs(urlparse(self.path).query).get("since",["0"])[0]
        try: last_seen=int(self.headers.get("Last-Event-ID",query_since))
        except ValueError: last_seen=0
        with db() as c:
            commands=c.execute("SELECT seq,payload FROM overlay_commands WHERE seq>? AND created_at>=? ORDER BY seq LIMIT 100",(last_seen,time_cutoff(2))).fetchall()
        missed=[{"kind":"reaction","reaction":json.loads(row["payload"]),"seq":row["seq"]} for row in commands]
        with lock:
            listeners.add(q)
        for item in missed:
            try: q.put_nowait(item)
            except queue.Full: break
        try:
            self.wfile.write(b"event: ready\ndata: {}\n\n"); self.wfile.flush()
            while True:
                try: item=q.get(timeout=15); packet=json.dumps(item,ensure_ascii=False)
                except queue.Empty:
                    item={"kind":"ping"}; packet=json.dumps(item)
                if item.get("kind")=="reaction":
                    reaction=item.get("reaction")
                    sequence=item.get("seq") or (reaction.get("seq") if isinstance(reaction,dict) else None)
                    if sequence is not None: self.wfile.write(f"id: {sequence}\n".encode())
                self.wfile.write(f"data: {packet}\n\n".encode()); self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError): pass
        finally:
            with lock: listeners.discard(q)
    def do_POST(self):
        path=urlparse(self.path).path; data=self.body()
        if path=="/api/twitch/disconnect":
            twitch.twitch_stop.set()
            if twitch.twitch_thread and twitch.twitch_thread.is_alive(): twitch.twitch_thread.join(timeout=5)
            with twitch.token_lock: saved_tokens=dict(twitch.twitch_tokens or {}); twitch.twitch_tokens=None; runtime.twitch_tokens=None
            revoked=False; revoke_error=""
            if saved_tokens.get("access_token") and twitch.client_id():
                try: twitch.revoke_twitch_token(saved_tokens["access_token"]); revoked=True
                except Exception as exc: revoke_error=str(exc)[:180]
            if runtime.TOKEN_FILE.exists(): runtime.TOKEN_FILE.unlink()
            twitch.twitch_status.update(connected=False,eventsub=False,channel="",login="",subscriptions=[],error=revoke_error)
            runtime.eventsub_connected.set(0)
            publish({"kind":"twitch","connected":False}); return self.send_json({"ok":True,"revoked":revoked,"warning":revoke_error})
        if path=="/api/twitch/reconnect":
            if not twitch.twitch_tokens: return self.send_json({"error":"Connect Twitch before retrying EventSub."},409)
            twitch.twitch_status.update(connected=False,eventsub=False,error="",subscriptions=[])
            if not twitch.start_twitch_worker(): return self.send_json({"error":twitch.twitch_status["error"] or "EventSub could not be restarted."},503)
            return self.send_json({"ok":True,"reconnecting":True})
        if path=="/api/replay":
            ok=controls.replay(data.get("profile","first_stream")); return self.send_json({"ok":ok,"running":stats["replay_running"]},200 if ok else 409)
        if path=="/api/chapter/start":
            chapter,error=story.start_chapter()
            if error: return self.send_json({"error":error},409)
            return self.send_json({"ok":True,"chapter":chapter},201)
        if path=="/api/chapter/close":
            chapter=story.close_chapter()
            if not chapter: return self.send_json({"error":"There is no open chapter vote."},409)
            return self.send_json({"ok":True,"chapter":chapter})
        if path=="/api/game/start":
            if not twitch.twitch_status["connected"]:
                return self.send_json({"error":"Connect Twitch and start a real stream before opening the live signal hunt."},409)
            result,error=signal_game.start(data.get("chapter_id"))
            if error: return self.send_json({"error":error},409)
            return self.send_json({"ok":True,"game":result},201)
        if path=="/api/game/replay":
            ok,message=signal_game.simulate()
            return self.send_json({"ok":ok,"message":message},202 if ok else 409)
        if path=="/api/load-test":
            try: count=int(data.get("events",1000)); rate=float(data.get("rate",100)); profile=str(data.get("profile","raid"))
            except (TypeError,ValueError): return self.send_json({"error":"events and rate must be numeric"},400)
            if profile not in ("raid","steady") or count<1 or count>600000 or rate<1 or rate>10000: return self.send_json({"error":"Use profile raid/steady, 1..600000 events and 1..10000 events/second"},400)
            if not controls.start_load(count,rate,profile): return self.send_json({"error":"A load test is already running"},409)
            return self.send_json({"ok":True,"status":"running","events":count,"target_rate":rate,"profile":profile},202)
        if path=="/api/lore/action":
            action=data.get("action"); lore_id=data.get("id")
            if action not in ("approve","reject","retire","restore","delete"):
                return self.send_json({"error":"Unsupported memory action."},400)
            with db() as c:
                lore=c.execute("SELECT id,summary,evidence FROM lore WHERE id=?",(lore_id,)).fetchone()
                if not lore: return self.send_json({"error":"Memory not found."},404)
                stream_context=relics.stream_context(c)
                stream_style=relics.configured_stream_type(c,lore["summary"])
                evidence=json.loads(lore["evidence"] or "[]")
                source_rows=c.execute("SELECT payload FROM events WHERE id IN (%s)" % ",".join("?" for _ in evidence[:8]),tuple(evidence[:8])).fetchall() if evidence else []
                source_messages=[]
                for source_row in source_rows:
                    try: source_messages.append(json.loads(source_row["payload"]).get("text", ""))
                    except (TypeError,ValueError): pass
            enrichment=None
            if action=="approve":
                fallback=relics.generate(lore["summary"],lore_id,stream_style)
                from .narrator import enrich_memory
                enrichment=enrich_memory(source_messages or [lore["summary"]],{
                    "summary":summarize_lore(lore["summary"]),"relic_title":fallback["name"],
                    "power_name":fallback["power_name"],"power_text":fallback["power_text"],
                    "boss_name":"The Unresolved Hypothesis",
                    "boss_epithet":"A fictional anomaly shaped by approved community lore"},stream_context)
            with db() as c:
                if action=="delete":
                    c.execute("DELETE FROM relics WHERE lore_id=?",(lore_id,))
                    c.execute("DELETE FROM lore_enrichments WHERE lore_id=?",(lore_id,))
                    c.execute("DELETE FROM lore WHERE id=?",(lore_id,))
                else:
                    status={"approve":"ACTIVE","reject":"REJECTED","retire":"RETIRED","restore":"PENDING"}[action]
                    if action=="approve":
                        summary=enrichment["summary"]
                        if runtime.DATABASE_URL:
                            from .embeddings import pgvector_literal
                            c.execute("UPDATE lore SET status=?,summary=?,embedding=?::vector WHERE id=?",(status,summary,pgvector_literal(summary),lore_id))
                        else: c.execute("UPDATE lore SET status=?,summary=? WHERE id=?",(status,summary,lore_id))
                        c.execute("INSERT INTO lore_enrichments(lore_id,summary,relic_title,power_name,power_text,boss_name,boss_epithet,provider,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(lore_id) DO UPDATE SET summary=excluded.summary,relic_title=excluded.relic_title,power_name=excluded.power_name,power_text=excluded.power_text,boss_name=excluded.boss_name,boss_epithet=excluded.boss_epithet,provider=excluded.provider,updated_at=excluded.updated_at",
                                  (lore_id,enrichment["summary"],enrichment["relic_title"],enrichment["power_name"],enrichment["power_text"],enrichment["boss_name"],enrichment["boss_epithet"],enrichment["provider"],now()))
                        relics.ensure_for_lore(c,lore_id,enrichment["summary"])
                        c.execute("UPDATE relics SET name=?,power_name=?,power_text=?,updated_at=? WHERE lore_id=?",(enrichment["relic_title"],enrichment["power_name"],enrichment["power_text"],now(),lore_id))
                    else: c.execute("UPDATE lore SET status=? WHERE id=?",(status,lore_id))
            publish({"kind":"lore","action":action}); return self.send_json({"ok":True})
        if path=="/api/settings/mute":
            muted=bool(data.get("muted"))
            with lock, db() as c:
                c.execute("UPDATE settings SET value=? WHERE key='muted'",("true" if muted else "false",))
                if muted: c.execute("DELETE FROM overlay_commands WHERE created_at>=?",(time_cutoff(10),))
            publish({"kind":"settings","muted":muted}); return self.send_json({"ok":True,"muted":muted})
        if path=="/api/privacy/forget":
            viewer_id=str(data.get("viewer_id",""))
            if not viewer_id: return self.send_json({"error":"viewer_id is required"},400)
            if data.get("demo"): viewer_id="demo-"+re.sub(r"\W+","",viewer_id.lower())[:20]
            controls.forget_viewer(viewer_id,data.get("viewer","viewer")); return self.send_json({"ok":True})
        return self.send_json({"error":"not found"},404)

class LorekeeperServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
