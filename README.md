# Lorekeeper

Lorekeeper is a Twitch stream mascot with persistent, streamer-curated community memory. It connects to a real Twitch broadcaster account with OAuth, subscribes to Twitch EventSub over WebSocket, keeps an event and lore archive, and sends live mascot lines to an OBS browser source. Replay profiles remain available for rehearsals and offline judging.

## Service boundaries

The implementation is organized as a modular service application with `server.py` as the composition root:

- `lorekeeper/twitch.py` owns OAuth, encrypted token lifecycle, EventSub reconnects, and event normalization.
- `lorekeeper/projection.py` owns idempotent event projection, lore extraction, mascot decisions, and rate limiting.
- `lorekeeper/controls.py` owns replay and viewer privacy workflows.
- `lorekeeper/http.py` owns the dashboard API, OAuth callback routes, OBS overlay delivery, and SSE fanout.
- `lorekeeper/runtime.py` owns configuration, database adapters, metrics, and process-local fanout; `lorekeeper/event_bus.py` owns the Kafka-compatible log client.

The hackathon Compose deployment keeps these application services in one container for a low-friction local run; PostgreSQL, Redpanda, and Redis remain separate services. This code separation is the foundation for splitting the API, Twitch ingestor, and projection worker into independently scaled containers. They are not yet independently deployable because OAuth state and live connection status still use process-local state, and the overlay fanout currently assumes one API process.

## Connect a real Twitch channel

### 1. Create a Twitch developer application

In the [Twitch Developer Console](https://dev.twitch.tv/console/apps), register an application and set its OAuth redirect URL to exactly:

```text
http://localhost:8787/auth/callback
```

Copy its Client ID and Client Secret. The browser callback is local; EventSub uses Twitch's outbound WebSocket transport, so you do not need a public webhook URL.

### 2. Configure and run Lorekeeper

Python 3.10+ is required. In PowerShell, from this project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`. Generate a Fernet key, then set `TOKEN_ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

For the event-log demo, start Redpanda and Lorekeeper together:

```powershell
docker compose up --build
```

This starts Redpanda, PostgreSQL with pgvector, Redis, Prometheus, Grafana, and Lorekeeper. Events are acknowledged to a channel-keyed Redpanda topic with seven-day retention, then projected into PostgreSQL; Redis enforces the global speech token bucket and per-trigger cooldowns. Prometheus scrapes `/metrics`, and Grafana is provisioned with a Lorekeeper dashboard. Docker Desktop is required. For a simpler local fallback without Docker, `python server.py` uses SQLite and an in-process speech limiter.

Local dashboards: Lorekeeper `http://localhost:8787`, OBS overlay `http://localhost:8787/overlay`, Grafana `http://localhost:3000` (login `admin`; password is `GRAFANA_PASSWORD` or the local demo default `lorekeeper-demo`), and Prometheus `http://localhost:9090`. Keep these ports bound to localhost for a demo machine.

Open [http://localhost:8787](http://localhost:8787) and choose **Connect Twitch**. Sign in as the broadcaster and grant the requested permissions. Once the dashboard says **Live · EventSub**, send a chat message in the connected channel. The event should appear in the timeline; lore cues can create a pending memory, and the mascot reaction appears in the dashboard and OBS overlay at [http://localhost:8787/overlay](http://localhost:8787/overlay).

The app requests permissions for chat reading, follower events, subscriptions, channel point redemptions, and cheers. Twitch may enable only subscriptions whose scopes were granted; inspect server output for per-subscription status. Raid, stream online, and stream offline events are also subscribed.

The dashboard reports the number of active EventSub subscriptions. If a permission was missed, click **Connect Twitch** to authorize again with the requested scopes. The local authorization callback must be `http://localhost:8787/auth/callback` in both `.env` and the Twitch Developer Console.

Twitch access and refresh tokens are encrypted in `data/twitch_tokens.bin` with `TOKEN_ENCRYPTION_KEY`. Keep `.env` and the encryption key private. Disconnecting Twitch revokes the active Twitch access token and removes the saved local authorization; if Twitch is unreachable, the local token is still removed and the dashboard reports the revocation warning. Raw event rows are pruned after seven days by default (`LOREKEEPER_RAW_RETENTION_DAYS`); Redpanda raw-topic retention is also seven days. `!forgetme` scrubs the viewer's relational event rows, derived lore, utterances, pending overlay commands, and limiter keys, and keeps only a suppression tombstone and audit record. The app binds to `127.0.0.1` by default.

## Live demo flow

1. Open the dashboard and `/overlay` as an OBS Browser Source.
2. Connect your Twitch broadcaster account.
3. Send a real message in that channel, then use `!lore`, `!whatdoyouknow`, and `!forgetme` in chat to demonstrate community memory controls.
4. Approve a pending lore candidate in the dashboard and use **Previously on…** to play the recap on the overlay.
5. Trigger a real follow, subscription, raid, or channel point redemption if available; the mascot reacts to the EventSub notification. The panic switch mutes new lines immediately.
6. Use replay only as an optional rehearsal or fallback. It is not the live integration.

## Optional OpenAI reactions

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env` to personalize eligible live mascot reactions with the OpenAI Responses API. Calls happen only after local trigger and rate-limit checks, have a 1.5-second timeout, set `store: false`, and fall back to templates when the request fails. Replay never makes model calls. Keep the API key out of browser code and source control.

## Current implementation

- Local OAuth authorization-code flow with CSRF state validation and encrypted token persistence.
- Twitch EventSub WebSocket ingestion for chat, follows, new subscriptions, resubscriptions, gift subscriptions, cheers, raids into the channel, channel point redemptions, and stream online/offline events.
- WebSocket keepalive watchdog, Twitch `session_reconnect` handoff, bounded reconnect retry, notification deduplication, and a normalized event envelope.
- Redpanda/Kafka-compatible event topic with channel-key ordering, acknowledged producer writes, consumer offset commits after projection, duplicate-safe processing, retry and DLQ handling, and seven-day retention.
- PostgreSQL projections with a pgvector lore column, durable overlay-command outbox, SQLite fallback for the no-Docker path, and seven-day raw event retention.
- Local lore candidate extraction with evidence IDs, streamer approval lifecycle, event moments, mascot templates and optional model phrasing.
- OBS overlay over server-sent events, recap, speech limiting, panic mute, `!whatdoyouknow`, and deletion/redaction on `!forgetme`.
- Synthetic replay profiles that use the same event-processing path without Twitch or model side effects.

This is a single-channel hackathon implementation; it does not claim every production scale target in the design doc has been met. The pgvector column is provisioned, but lore embeddings and semantic reranking are not yet populated; the current extractor uses local salience heuristics. OpenAI personalizes eligible reactions, but does not extract lore. Distributed multi-channel workers, load-tested 10K events/second capacity, and further safety and failure-mode hardening remain. See [Lorekeeper_System_Design.md](Lorekeeper_System_Design.md) for the remaining target architecture.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/connect` | Start Twitch OAuth |
| GET | `/auth/callback` | Validate and store authorization, then start EventSub |
| POST | `/api/twitch/disconnect` | Stop EventSub, revoke the Twitch token, and remove saved authorization |
| GET | `/api/state` | Dashboard projection and Twitch connection/subscription status |
| GET | `/api/stream` | Live server-sent event fanout to dashboard and overlay |
| GET | `/api/health` | Liveness |
| GET | `/api/ready` | Database, event log, and Redis readiness |
| GET | `/metrics` | Prometheus metrics |
| POST | `/api/replay` | Start a synthetic rehearsal profile |
| POST | `/api/lore/action` | Approve, reject, retire, or delete lore |
| POST | `/api/settings/mute` | Set the mascot panic mute |
| POST | `/api/privacy/forget` | Delete a viewer's derived memory |
| POST | `/api/recap` | Compose a recap from approved lore and moments |

The Twitch EventSub WebSocket session uses the `session_welcome` ID to create subscriptions, follows Twitch's reconnect URL without dropping the old socket before handoff, and deduplicates redelivered notification IDs. Twitch requires a user access token for WebSocket subscriptions; scopes are subscription-specific. See [Twitch EventSub WebSocket messages](https://dev.twitch.tv/docs/eventsub/websocket-reference/), [subscription types and scopes](https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types), and [OAuth authorization-code flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/).

## Hackathon submission

The [Codex × TwitchCon Hackathon](https://twitchcon-codex.devpost.com/) asks for a Twitch-connected project, source and run instructions, a public YouTube demo up to three minutes, a project description, and the Codex session ID. This repository provides the local source and real Twitch connection flow; the team still needs to record and submit the demo video and project description.


### Codex Session Details

```text
>_ OpenAI Codex (v0.164.0)

Model:              GPT-6-Luna (reasoning medium, summaries auto)
Model Provider:     OpenAI
Directory:          ~\OneDrive\Desktop\Lorekeeper
Permissions:        Workspace (granular)
Agents.md:          <none>
Account:            aaryavartjoshi@gmail.com (Go)
Collaboration Mode: Default
Session ID:         01a17e90-6f6b-7640-b71f-e99fa3e451b9

Monthly Limit:      [████████████████████] 100% left (resets 23:42 on 26 Nov)
Usage Reset:        1 reset available (run /usage)
```
