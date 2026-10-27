"""Lorekeeper composition root. Start with `python server.py`."""
from __future__ import annotations
import os, threading
from lorekeeper import runtime, twitch
from lorekeeper.event_bus import EventBus
from lorekeeper.projection import process_event
from lorekeeper.http import Handler, LorekeeperServer


def main():
    runtime.init_db()
    runtime.prune_raw_events()
    threading.Thread(target=runtime.raw_retention_worker,daemon=True,name="raw-event-retention").start()
    redis_url=os.environ.get("REDIS_URL","").strip()
    if redis_url:
        import redis
        runtime.redis_client=redis.Redis.from_url(redis_url,socket_connect_timeout=3,socket_timeout=2,health_check_interval=30)
        runtime.redis_client.ping()
        print("Redis speech limiter connected",flush=True)
    bootstrap=os.environ.get("KAFKA_BOOTSTRAP_SERVERS","").strip()
    if bootstrap:
        runtime.event_bus=EventBus(bootstrap,process_event)
        runtime.event_bus.start()
        print(f"Durable event log connected: {bootstrap}",flush=True)
    twitch.restore_tokens()
    if twitch.twitch_tokens and twitch.client_id() and twitch.client_secret():
        twitch.twitch_status.update(channel=twitch.twitch_tokens.get("login",""),login=twitch.twitch_tokens.get("login",""))
        twitch.start_twitch_worker()
    host=os.environ.get("LOREKEEPER_HOST","127.0.0.1"); port=int(os.environ.get("PORT","8787"))
    print(f"Lorekeeper is ready at http://localhost:{port}",flush=True)
    print(f"OBS overlay: http://localhost:{port}/overlay",flush=True)
    http_server=LorekeeperServer((host,port),Handler)
    try: http_server.serve_forever()
    finally:
        twitch.twitch_stop.set()
        http_server.server_close()
        if runtime.event_bus: runtime.event_bus.stop()
        if runtime.db_pool: runtime.db_pool.close()


if __name__=="__main__":
    main()
