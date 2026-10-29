# Lorekeeper: live test, rehearsal, and demo recording playbook

This is an end-to-end guide for testing Lorekeeper with a real Twitch stream, the broadcaster account, and one alt viewer account. It finishes with a rehearsal plan and a three-minute recording outline.

The game does not reply to guesses in Twitch chat. Real Twitch chat messages go through EventSub; feedback appears in the Lorekeeper dashboard and OBS browser overlay.

## The story you will tell

The chat remembers a strange moment from a science stream. The streamer reviews and approves that observation, which becomes part of the community's story. The audience votes on how its research charm enters a fictional lore-boss encounter. Chat then searches together for a hidden word connected to that approved memory. Every `!guess WORD` produces semantic heat feedback on the overlay. Hints appear during the round, and the exact word resolves the encounter. The result and relic progress carry into the next field study.

For the first rehearsal, use a simple, real story seed such as a comet appearing over an observatory. Replace it with an actual moment from your stream if available. Say in the demo that it is a real stream moment or a prepared earlier moment replayed through the app; do not imply it happened live if it did not.

## What you need open

- Docker Desktop running.
- For open-vocabulary scoring: LM Studio running with the embedding model loaded and reachable from Docker. LM Studio is optional in offline practice mode.
- Lorekeeper dashboard at <http://localhost:8787>.
- OBS with the broadcaster account streaming to Twitch.
- Twitch chat for the broadcaster's channel in two windows: broadcaster account and alt viewer account.
- Lorekeeper overlay at <http://localhost:8787/overlay>, preferably added to OBS as a Browser Source.
- Grafana at <http://localhost:3000> for the engineering portion of the rehearsal.

For a real semantic game, use `EMBEDDING_PROVIDER=lm_studio`, the exact loaded model ID, and `EMBEDDING_DIMENSIONS=768`. Keep LM Studio running and serving on the local network so Docker can reach it. `LOREKEEPER_LLM_PROVIDER=lm_studio` separately enables generated puzzle words, clues, hints, and the lore-linked answer reveal.

For a no-model judge rehearsal, set `EMBEDDING_PROVIDER=demo_lexicon` and restart the app. The dashboard shows its small accepted word list. This mode uses curated concept vectors, not the open-vocabulary LM Studio model; Twitch chat/EventSub can still be real.

## Start the application

Open PowerShell and run:

```powershell
Set-Location "C:\Users\aarya\OneDrive\Desktop\Lorekeeper"
$dockerExe = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
$env:Path = "$(Split-Path $dockerExe);$env:Path"
& $dockerExe compose up --build
```

Leave this terminal running. Wait for the Lorekeeper container to be up, then open these pages:

- Dashboard: <http://localhost:8787>
- Readiness: <http://localhost:8787/api/ready>
- Overlay: <http://localhost:8787/overlay>
- Grafana: <http://localhost:3000>

If the Docker executable is already on `PATH`, `docker compose up --build` is enough. Do not use `docker compose down -v` for routine restarts; `-v` removes persisted data.

## Reset all application data (destructive)

Use this only when you intentionally want a completely fresh Lorekeeper installation. It permanently deletes the PostgreSQL application database, saved Twitch authorization token, Lorekeeper local data, Redpanda event log, Redis data, Grafana data, and Prometheus metrics. Your source files and `.env` are kept. You will need to reconnect Twitch and create/approve lore again.

In PowerShell, from the repository directory, stop the stack and delete its Compose volumes:

```powershell
Set-Location "C:\Users\aarya\OneDrive\Desktop\Lorekeeper"
$dockerExe = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
$env:Path = "$(Split-Path $dockerExe);$env:Path"
& $dockerExe compose down --volumes
```

Then start the application again:

```powershell
& $dockerExe compose up --build
```

Do not run the reset command if you want to keep your stream history, approved observations, votes, relics, viewer profiles, or Twitch connection. A normal restart only needs `& $dockerExe compose up --build`.

## Part 1: verify the real Twitch connection

1. In the dashboard, click **Connect Twitch** if the broadcaster account is not already connected. Complete OAuth as the broadcaster.
2. Confirm the dashboard shows **Live - EventSub** and the correct channel name.
3. Start the stream in OBS. Use an actual Twitch stream for this test; opening chat while the channel is offline does not prove the live demo path.
4. Open the channel's Twitch chat in one window as the broadcaster and a second window/private browser as the alt. Join the same channel in both.
5. Keep the dashboard and overlay visible. Send a normal message from the alt, such as:

   ```text
   The telescope is ready for tonight's observation.
   ```

   The dashboard event feed should show a received chat event. It will not echo the raw message there. The overlay may not show a reaction to every ordinary chat message; the key check is that the live event reached Lorekeeper.

If the dashboard does not show the incoming event, stop here and check the EventSub connection before proceeding. The later game steps depend on the same live chat ingestion.

## Part 2: create and approve the story seed

The lore learner accepts longer Twitch messages containing recurrence language. Use one sentence that sounds like a callback rather than a command.

1. From the alt account, send this exact test message in the live channel:

   ```text
   Remember when the comet appeared over our observatory?
   ```

2. Wait a few seconds, then open **Community observations** in the Lorekeeper dashboard.
3. Find the new pending observation. The streamer, using the dashboard, chooses whether this viewer message becomes canon.
4. Click **Approve**. Wait for the approved lore/relic details to finish updating. If the local narrator is enabled, the observation may be paraphrased and receive generated relic and boss names. If the model is unavailable, curated fallback text should appear instead.
5. Confirm the approved observation is visible before starting the field study.

If it does not appear, send one longer message that includes `remember when`, `again`, or `never forget`. Confirm the message was sent by the alt in the broadcaster's live chat. Do not send `!guess` yet; guesses only count during an active signal hunt.

## Part 3: open a field study and test vote A

1. In the dashboard, click **Start a field study**. The chapter vote should appear with choices A and B and a countdown.
2. From the alt account, send:

   ```text
   !vote A
   ```

   One vote is enough for A to win if there are no B votes. If broadcaster messages are accepted by EventSub, the broadcaster can also send `!vote A` to make the vote count more visible. A viewer can change their own vote before the poll closes; this changes their selection and does not add a second voter.

3. Confirm the A vote count changes in the dashboard. The on-screen explanation should say the charm is being deployed and hint one will be revealed immediately.
4. Click **Reveal the finding** to close the vote now instead of waiting for the countdown.
5. Click **Start lore boss encounter**.

The live encounter should show the lore boss name, field clue, timer, and first hint. The round normally lasts 90 seconds. Choice A spends a relic charge when one is available and adds resonance; the hint is available as soon as the hunt opens.

## Part 4: play the semantic hunt in Twitch chat

Send each line below as a separate Twitch chat message. You may alternate between the broadcaster and alt accounts. Wait at least two seconds before the same account sends another guess; this is the per-viewer cooldown.

```text
Alt:         !guess comet
Broadcaster: !guess space
Alt:         !guess orbit
Broadcaster: !guess telescope
Alt:         !guess sky
Broadcaster: !guess astronomy
Alt:         !guess observatory
Broadcaster: !guess meteor
```

If broadcaster chat messages are not accepted on your setup, use the alt account for every guess and wait two or more seconds between its messages:

```text
!guess comet
!guess space
!guess orbit
!guess telescope
!guess sky
!guess astronomy
!guess observatory
!guess meteor
```

Use the clue, hint, and heat to decide what to try next. The target is picked for the specific round, so these are useful seed-related guesses, not a guaranteed answer list. Avoid repeating the same guess from the same account: duplicate guesses are ignored. The semantic heat is a presentation score for closeness to this round's target, not a global rank across every English word.

Guess rules: enter one English ASCII word from 2 to 24 letters. Multiword and non-English guesses are ignored. With the live embedding model, plurals and misspellings can still receive semantic heat, but only the exact hidden spelling solves the round. Offline practice accepts only the exact words shown in its curated list. If two viewers tie for closest, both receive closest-guess credit. Assist credit goes to guesses that set a new shared best.

Check the displayed score p95 during play or the Prometheus histogram `lorekeeper_guess_embedding_latency_seconds`. Under 1 second is a goal to measure on this machine, not a guaranteed latency.

To verify embedding failure recovery during a rehearsal, stop LM Studio while a live-model hunt is active and send one new `!guess WORD`. Within the configured two-second request timeout, the dashboard and overlay should show **Signal lost** and should not add the unscored guess to the feed. Start LM Studio again and send a new guess; a successful score should clear the warning. Do not use this disruption during the recording take.

Expected behavior:

- Each accepted guess appears in the dashboard/overlay feed with its viewer and heat band.
- A better guess raises the displayed best heat and reduces the boss's signal-distance bar.
- Hints unlock as time passes. With choice A, hint one is already visible at the start; later hints unlock during the round.
- The exact target word resolves the encounter immediately. The overlay briefly reveals the answer and explains how it connects to the approved memory. The closest guess and helpful best-score improvements receive extra XP, shown in the round leaderboard.
- If nobody finds the exact word, let the clock expire. The app reveals the target and timeout result. This still verifies real chat ingestion, embedding scoring, hint timing, and timeout handling.

For a more natural rehearsal, have the alt read the clue aloud and discuss guesses with the broadcaster instead of rapidly pasting the entire list. It makes the audience collaboration legible on the recording.

## Part 5: test vote B and its different consequence

Only do this after the first live encounter has resolved.

1. Click **Start a field study** for the next chapter.
2. From the alt account, send:

   ```text
   !vote B
   ```

3. Confirm the B vote count changes. Click **Reveal the finding**, then **Start lore boss encounter**.
4. Confirm the hunt has 20 extra seconds compared with the normal duration and hint one is still sealed initially. It unlocks later as the search progresses.
5. Send a few different `!guess WORD` messages and confirm their heat still updates.

Choice B preserves the sample and awards a relic ward. The encounter starts without hint one and gives chat more time. This makes the choice visibly different from deploying the charm for an immediate hint.

## Part 6: verify the guess safety filter

Do this before recording, using a harmless test token rather than a real slur:

1. Set `LOREKEEPER_GUESS_BLOCKLIST=filteredword` in `.env` and restart Lorekeeper.
2. During a live test round, send `!guess filteredword` from the alt account.
3. Confirm the term does not appear in the recent-guesses feed and the dashboard/overlay shows that a guess was filtered.
4. Remove the temporary test token from `.env`, set your channel-specific prohibited terms, and restart before the real demo.

Also leave Twitch AutoMod and your usual channel moderation enabled. The app filter is a configurable defense layer, not a promise to catch every slur, obfuscation, or language. Never test an actual slur in a public Twitch chat.

## Part 7: verify privacy after gameplay

Do this after both live encounter checks because forgetting the alt can remove lore contributed only by that account.

1. In Twitch chat as the alt, send:

   ```text
   !whatdoyouknow
   ```

2. Check the overlay/dashboard response for the alt's saved lore or profile summary.
3. Then send:

   ```text
   !forgetme
   ```

4. Confirm the acknowledgement appears and the viewer's saved profile/derived lore is removed or anonymized according to whether other viewers also contributed to that lore.
5. Send `!whatdoyouknow` again and confirm the response reports no saved memory for the forgotten viewer.

The commands produce an overlay/dashboard response; they do not post a bot reply into Twitch chat.

## Part 8: exercise the labelled synthetic preview

After a real live encounter resolves, click **Preview with 200 simulated guessers** in the dashboard. Watch the overlay and Grafana. The preview uses synthetic events and is explicitly labelled; do not present it as real viewer activity. Wait for the preview to resolve before starting another chapter.

For the engineering view, keep Grafana beside the dashboard and show event rate, consumer lag, and latency panels that are populated in your deployment. Capture a screenshot and save measured load-test output separately; do not describe a target rate as a measured rate.

## Troubleshooting during the test

| What you see | What to do |
|---|---|
| **Connect Twitch** remains visible | Reauthorize as the broadcaster, wait for EventSub to reconnect, and refresh the dashboard. |
| Chat message does not appear in the dashboard event feed | Confirm the stream is live, the alt joined the broadcaster's channel, and the dashboard says **Live - EventSub**. Check Lorekeeper logs for EventSub errors. |
| Observation does not appear | Use a sentence longer than 14 characters containing a recurrence cue such as `remember when` or `again`; make sure it is ordinary chat, not a command. |
| **Start a field study** is disabled | Approve an observation first. Resolve any open vote or active encounter before continuing. |
| `!vote A` does not change the count | Confirm the vote is open, send the command in the broadcaster's live Twitch chat, and wait for the dashboard refresh. One vote per account is counted. |
| Signal hunt cannot start and reports an embedding error | Confirm LM Studio is serving the embedding model, Docker can reach its host URL, the `EMBEDDING_MODEL` ID is exact, and the model returns 768 dimensions. |
| `!guess WORD` is not shown | Confirm the encounter status is LIVE, the message contains exactly one alphabetic word after `!guess`, and that account has not sent another guess in the last two seconds. |
| Guesses show COLD | Try a word closer to the clue or a timed hint. A distant meaning should get a cold score. |
| OBS overlay looks stale | Refresh <http://localhost:8787/overlay> and confirm it is an OBS Browser Source pointed at that URL. |

To inspect Lorekeeper logs from another PowerShell window:

```powershell
$dockerExe = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
& $dockerExe compose logs -f lorekeeper
```

For a solo smoke test, the broadcaster and one alt are enough. Before recording, invite 3-5 real viewers so the clue discussion, vote, and assists read as a group game.

## Rehearsal plan before recording

### Rehearsal 1: prove the complete path

Run Parts 1 through 7 once without recording. Confirm the real Twitch chat observation appears, approval works, the A/B vote changes the hunt, guesses get semantic heat, the target is revealed on solve or timeout, privacy works, and the synthetic preview remains labelled. Write down any error and fix it before the next rehearsal.

### Rehearsal 2: rehearse the story and pacing

Run the real Twitch sequence again. Keep the observation short and clear, use one vote outcome, and guide chat through three to six guesses with the clue/hints visible. Do not rely on reading a long command list aloud. Make sure the solve or timeout occurs before moving to the next demo segment.

### Rehearsal 3: record-ready check

- Restart Lorekeeper with the startup command and confirm Twitch reconnects.
- If using open-vocabulary scoring, confirm LM Studio is warmed up and its embedding model is loaded. For offline practice, confirm the curated word list is visible instead.
- Confirm the approved story seed is present; avoid seeding during the final take if it costs time.
- Check OBS source sizing, dashboard zoom, Twitch chat readability, and Grafana layout.
- Keep the real Twitch segment separate from the synthetic preview in the edit.
- Aim to finish the edited piece around 2:45, leaving about 15 seconds below the three-minute limit.

## Suggested three-minute demo cut

| Time | What to show | Suggested narration |
|---|---|---|
| 0:00-0:20 | Real Twitch stream, real chat, approved observation, and the generated/curated relic. | "Lorekeeper turns a streamer-approved community memory into a puzzle the whole chat can solve together." |
| 0:20-0:40 | Alt votes in Twitch chat; dashboard shows the vote and selected early-hint effect. | "The audience chooses how the charm enters the encounter. Deploy it for an early hint, or preserve it for a longer search." |
| 0:40-1:25 | Real `!guess WORD` messages; OBS shows clue, heat, timed hints, and guesses. | "Every guess gives the whole chat a semantic temperature. Viewers compare ideas and get warmer together." |
| 1:25-1:45 | Show exact solve or timeout reveal, XP/title, and relic progression. | "The answer closes the encounter, and the result becomes part of the next chapter." |
| 1:45-2:10 | Brief synthetic preview and Grafana evidence. Keep the synthetic label visible. | "This is a separate simulation through the game service, not live audience traffic." |
| 2:10-2:35 | Show `!whatdoyouknow`, `!forgetme`, panic mute, and a safety replay if time allows. | "Viewers can inspect and remove their saved memory, and the streamer controls what becomes canon." |
| 2:35-2:50 | Show a brief Codex session/build clip and session ID. | "Codex helped build the Twitch integration, local narration, event flow, and game." |
| 2:50-3:00 | Show a simple architecture card and close. | "A real community memory becomes a live Twitch puzzle, and its answer carries forward." |

The final video must show the real Twitch stream and real Twitch interaction. The synthetic preview is only the engineering segment. Keep the recording publicly viewable on YouTube and below three minutes.
