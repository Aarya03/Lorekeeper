# Lorekeeper

Lorekeeper is a Twitch community story game. Streamer-approved observations become research charms and fictional lore bosses. Chat votes between investigations, then plays a cooperative semantic signal hunt: viewers send `!guess WORD`, see meaning-based heat feedback, and try to find the hidden word together. A local embedding model grades guesses; LM Studio can create lore-linked words and clues, with curated fallbacks when narration is unavailable.

## The live story loop

1. A streamer approves a recurring community observation. LM Studio can paraphrase it and create a research charm, a lore-boss name, and an accessible ability. If generation fails, curated templates are used.
2. The streamer opens a field study. Viewers vote with `!vote A` or `!vote B`: A deploys the charm and reveals hint one immediately; B preserves the sample and extends the hunt by 20 seconds while keeping hint one sealed initially.
3. The lore boss opens a 90-second signal hunt. Chat guesses with `!guess WORD`. Local embeddings compare each guess to the hidden lore-linked target and show a shared heat score; the exact word solves the encounter. Guesses are rate limited per viewer, and the target stays private until the round resolves.
4. The OBS overlay shows the lore boss, clue, timed hints, best heat, recent guesses, and countdown.
5. At round end, participants earn persistent XP and titles. `!whatdoyouknow` reports a viewer's saved lore and game profile; `!forgetme` deletes both.

The default vote window is 45 seconds and the signal hunt is 90 seconds. Set `LOREKEEPER_CHAPTER_VOTE_SECONDS` to 15-180 seconds and `LOREKEEPER_SIGNAL_SECONDS` to 45-180 seconds. For semantic guessing, run an embedding model in LM Studio, set `EMBEDDING_PROVIDER=lm_studio`, set `EMBEDDING_MODEL` to its exact model ID, and set `EMBEDDING_DIMENSIONS=768` for the Nomic 768 model used in this setup. Guess scoring never falls back to feature hashing, because hashes do not represent meaning. Ordinary chat messages are not sent to a text-generation LLM; guesses go to the local embedding endpoint for scoring.

### Optional local LM Studio narrator

In LM Studio, load an instruct model and start its OpenAI-compatible local server on port 1234. If Lorekeeper runs in Docker, enable LM Studio's **Serve on Local Network** option so the container can reach the host. Set these values in `.env` (the Docker service reaches LM Studio on the host through `host.docker.internal`):

```dotenv
LOREKEEPER_LLM_PROVIDER=lm_studio
LOREKEEPER_LLM_BASE_URL=http://host.docker.internal:1234/v1
LOREKEEPER_LLM_MODEL=
LOREKEEPER_LLM_TIMEOUT_SECONDS=8
```

Leave the model blank to use the model loaded in LM Studio. For Lorekeeper running directly on Windows instead of Docker, use `http://localhost:1234/v1`. If LM Studio is unavailable, times out, or returns invalid output, curated templates are used. Chat text is treated as untrusted data and the endpoint must be local.

## Run with real Twitch

### Twitch app setup

In the [Twitch Developer Console](https://dev.twitch.tv/console/apps), create an application and set its redirect URL to the value in `TWITCH_REDIRECT_URI`. This project defaults to `http://localhost:8787/auth/callback`. If Twitch requires HTTPS for your app, use an HTTPS tunnel and set the same tunnel callback in both Twitch and `.env`. EventSub uses an outbound WebSocket connection, so no public webhook is needed.

### Environment and services

In PowerShell, from the repository directory:

```powershell
Copy-Item .env.example .env
```

Set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` in `.env`. Generate a token-encryption key and set `TOKEN_ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

For the live signal-hunt feature, load an embedding model in LM Studio and set `EMBEDDING_PROVIDER=lm_studio`, `EMBEDDING_MODEL` to the exact loaded model ID, and `EMBEDDING_DIMENSIONS=768`. Set `LOREKEEPER_LLM_PROVIDER=lm_studio` separately if you also want LM Studio to write the lore-linked target and clues; otherwise the game uses a lore-word fallback.

Start the services with Docker Desktop:

```powershell
docker compose up --build
```

This starts Lorekeeper, PostgreSQL/pgvector, Redpanda, Redis, Prometheus, and Grafana. Lorekeeper is at `http://localhost:8787`; use `http://localhost:8787/overlay` as an OBS Browser Source. Grafana is at `http://localhost:3000` (`admin`, password from `GRAFANA_PASSWORD`, default `lorekeeper-demo`).

Open the dashboard, choose **Connect Twitch**, and authorize the broadcaster account. Wait for **Live · EventSub** before starting a chapter. The connected channel name appears in the dashboard.

### Seed a useful community observation

Lorekeeper only uses approved observations as story clues. In live channel chat, share a short, non-sensitive callback from a science stream, such as “Remember when chat spotted the loose cable during the experiment?” It will appear in **Community Observations**. Approve it before starting a field study. Streamer approval is deliberate: unreviewed messages never become the story seed.

### Start the audience chapter

Keep the OBS overlay open. Start a field study and let chat choose with `!vote A` or `!vote B`. Reveal the finding, then choose **Start lore boss encounter**. On Twitch chat, viewers enter `!guess WORD`; the overlay shows the clue, timed hints, recent guesses, and the closest signal heat. Exact answer or timeout reveals the target. When it resolves, the dashboard shows researcher XP/titles and lets you start the next field study. **Preview with 200 simulated guessers** is clearly labelled synthetic activity; it does not create real Twitch votes.

For a detailed real-Twitch test and rehearsal sequence, see [docs/live_twitch_test_guide.md](docs/live_twitch_test_guide.md). For the timed demo narration, see [docs/demo_story.md](docs/demo_story.md).

## Privacy and safety

- Lore stays pending until the streamer approves it.
- Vote/game records use Twitch user IDs for deduplication and credit. `!forgetme` removes the viewer's derived lore, relics, chapter text derived from that memory, action history, XP/profile, and vote records, then suppresses future learning and game actions for that account.
- `!whatdoyouknow` reports the viewer's saved lore and game title/XP. A forgotten account receives an empty-memory response.
- The dashboard panic mute immediately stops mascot speech.
- Twitch tokens are encrypted at rest in `data/twitch_tokens.bin`. Keep `.env` and its key private.
- Raw events are retained for seven days by default. Redpanda is append-only, so raw log records can remain until topic retention expires.

## Architecture and observability

`server.py` composes the application. `lorekeeper/twitch.py` handles OAuth and EventSub, `lorekeeper/projection.py` projects live events/lore and sends `!guess` events into the durable `game.actions` topic, `lorekeeper/signal_game.py` grades semantic guesses and manages live encounters, `lorekeeper/narrator.py` provides local LM Studio story enrichment with template fallback, `lorekeeper/relics.py` manages stream-aware artifacts, and `lorekeeper/story.py` manages chapter votes. `lorekeeper/http.py` serves the dashboard/API and overlay. The Compose deployment runs these application modules with PostgreSQL, Redpanda, and Redis as separate services.

Check readiness at `http://localhost:8787/api/ready` and metrics at `http://localhost:8787/metrics`. The dashboard's **Rehearsal & safety** panel includes a raid replay, a prompt-injection profile, and a 1,000-event load run; all are explicitly synthetic and cannot create lore. For a sustained load report, run `python tools/load_test.py --duration 60 --rate 10000`; report measured results, not the requested target. Synthetic replay/load traffic is for engineering rehearsal and is not a substitute for the real Twitch demo.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/connect` | Start Twitch OAuth |
| GET | `/auth/callback` | Validate authorization and start EventSub |
| GET | `/api/state` | Dashboard, lore, relic vault, chapter, and Twitch status |
| GET | `/api/stream` | Live server-sent events for dashboard and overlay |
| POST | `/api/chapter/start` | Start a chapter from approved lore |
| POST | `/api/chapter/close` | Close the active vote and reveal the result |
| POST | `/api/game/start` | Start the resolved chapter's live Twitch boss encounter |
| POST | `/api/game/replay` | Preview a separate simulated 200-viewer encounter |
| POST | `/api/lore/action` | Approve, reject, restore, retire, or delete a memory |
| POST | `/api/settings/mute` | Set panic mute |
| POST | `/api/privacy/forget` | Delete a viewer's derived memory |
| GET | `/api/ready` | Database, event log, and Redis readiness |
| GET | `/metrics` | Prometheus metrics |

## Hackathon submission

The [Codex x TwitchCon Hackathon](https://twitchcon-codex.devpost.com/) asks for the project repository, a public YouTube demo up to three minutes, a project description, and the Codex session ID. The demo should show this chapter loop working on a real Twitch channel and include how Codex was used to build it.
