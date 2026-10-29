"""Lorekeeper composition root. Start with `python server.py`."""
from __future__ import annotations
import os, threading
from lorekeeper import heat, runtime, twitch
from lorekeeper.event_bus import EventBus
from lorekeeper.projection import process_event, backfill_lore_embeddings
from lorekeeper.http import Handler, LorekeeperServer
from lorekeeper.signal_game import apply_guess as process_game_action


def _start_background_services():
    """Start slow or optional integrations without delaying the dashboard bind."""
    heat.begin_reference_warmup()
    try:
        embedded=backfill_lore_embeddings()
        if embedded: print(f"Backfilled {embedded} lore vectors",flush=True)
    except Exception as exc:
        print(f"Lore vector backfill deferred: {str(exc)[:180]}",flush=True)
    try:
        runtime.prune_raw_events()
        threading.Thread(target=runtime.raw_retention_worker,daemon=True,name="raw-event-retention").start()
    except Exception as exc:
        print(f"Raw event retention worker did not start: {str(exc)[:180]}",flush=True)
    redis_url=os.environ.get("REDIS_URL","").strip()
    if redis_url:
        try:
            import redis
            redis_client=redis.Redis.from_url(redis_url,socket_connect_timeout=3,socket_timeout=2,health_check_interval=30)
            redis_client.ping()
            runtime.redis_client=redis_client
            print("Redis speech limiter connected",flush=True)
        except Exception as exc:
            runtime.redis_client=None
            print(f"Redis unavailable; using local rate limiter: {str(exc)[:180]}",flush=True)
    bootstrap=os.environ.get("KAFKA_BOOTSTRAP_SERVERS","").strip()
    if bootstrap:
        try:
            event_bus=EventBus(bootstrap,process_event,game_handler=process_game_action)
            event_bus.start()
            runtime.event_bus=event_bus
            print(f"Durable event log connected: {bootstrap}",flush=True)
        except Exception as exc:
            runtime.event_bus=None
            print(f"Event log unavailable; dashboard is running in local-storage mode: {str(exc)[:180]}",flush=True)
    try:
        twitch.restore_tokens()
        if twitch.twitch_tokens and twitch.client_id() and twitch.client_secret():
            twitch.twitch_status.update(channel=twitch.twitch_tokens.get("login",""),login=twitch.twitch_tokens.get("login",""))
            twitch.start_twitch_worker()
    except Exception as exc:
        print(f"Twitch worker did not start: {str(exc)[:180]}",flush=True)


def main():
    runtime.init_db()
    host=os.environ.get("LOREKEEPER_HOST","127.0.0.1"); port=int(os.environ.get("PORT","8787"))
    http_server=LorekeeperServer((host,port),Handler)
    threading.Thread(target=_start_background_services,daemon=True,name="lorekeeper-background-startup").start()
    print(f"Lorekeeper dashboard is available at http://localhost:{port}",flush=True)
    print(f"OBS overlay: http://localhost:{port}/overlay",flush=True)
    try: http_server.serve_forever()
    finally:
        twitch.twitch_stop.set()
        http_server.server_close()
        if runtime.event_bus: runtime.event_bus.stop()
        if runtime.db_pool: runtime.db_pool.close()


if __name__=="__main__":
    main()
