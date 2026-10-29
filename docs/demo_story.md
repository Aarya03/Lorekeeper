# Lorekeeper live demo: Chat hunts a word from its own story

## One-sentence pitch

**Chat hunts a hidden word drawn from its inside jokes, guided by meaning-based heat.** An approved memory shapes the clue and the end-of-round reveal explains why the answer belongs to that community.

## Prepare a real Twitch rehearsal

- Connect the broadcaster through Twitch OAuth and confirm the dashboard says **Live · EventSub**.
- Start an actual Twitch stream in OBS. Keep the real broadcaster chat open with the alt account and at least two other viewers if possible.
- Approve a short, safe recurring community observation before recording. Use a real callback from your channel; the loose-sensor story below is only an example.
- For the full open-vocabulary experience, load an embedding model in LM Studio and set `EMBEDDING_PROVIDER=lm_studio`, the exact `EMBEDDING_MODEL`, and its dimension. Warm the model before the take.
- If rehearsing without LM Studio, set `EMBEDDING_PROVIDER=demo_lexicon`. This is a clearly labelled offline practice mode with a small curated word list, not a substitute for the open-vocabulary semantic model. The dashboard displays accepted words.
- Have 3–5 real viewers ready for the recording. One broadcaster and one alt are enough to verify the path, but a few people make chat collaboration easier to see.
- Keep OBS with the overlay, the Lorekeeper dashboard, and Grafana visible. Preview traffic is synthetic and must remain labelled as such.

## Rehearse the real loop

1. In the live channel, an alt posts a recurring callback, for example: `Remember when chat spotted the loose sensor cable during the experiment?`
2. In **Community observations**, approve it. Check the short paraphrase, relic, and lore-boss details.
3. Start a field study. Real viewers vote `!vote A` to reveal hint one at the start, or `!vote B` to keep that hint sealed initially and add 20 seconds.
4. Reveal the vote and start the lore boss encounter. Read the clue together and let chat discuss guesses.
5. Viewers send one English word at a time using `!guess WORD`. Watch the overlay show the feed and shared heat. Better semantic matches feel warmer; the exact spelling solves the encounter.
6. Show the answer reveal and its explanation of the approved inside joke. The closest guess earns a bonus, tied closest viewers share it, and guesses that improve the shared best earn assist credit.
7. After the live round resolves, use **Preview with 200 simulated guessers** only if you want to show synthetic activity and the pipeline. The UI must identify it as simulated.

Guess rules: one ASCII English word, 2–24 letters; multiword and non-English entries are ignored. Plurals and misspellings can be scored by the live embedding model but do not solve the puzzle unless they exactly match the hidden spelling. Repeated guesses from one account are ignored, with a two-second rate limit. Offline practice only accepts words in its displayed curated list.

## Three-minute recording outline

| Time | Show | Narration |
|---|---|---|
| 0:00–0:25 | Real Twitch chat, an approved observation, the relic and lore boss. | “Lorekeeper turns an approved community memory into a puzzle the whole chat can solve together.” |
| 0:25–0:45 | Real chat casts `!vote A` or `!vote B`; show the different hint and timer outcomes. | “Chat chooses an early hint or a longer search. That changes how the encounter begins.” |
| 0:45–1:30 | Live `!guess WORD` messages, shared heat, clue and timed hint on the OBS overlay. | “Guesses are compared by meaning. Everyone can see when the chat is getting warmer.” |
| 1:30–1:50 | Solve or timeout; show the answer and lore connection, then XP credit. | “The answer reveals why this word belongs to the community’s story. Closest guesses and helpful leads earn credit.” |
| 1:50–2:15 | Clearly labelled simulated preview and Grafana metrics. | “This is synthetic traffic for engineering rehearsal, separate from the live Twitch round.” |
| 2:15–2:40 | Show `!whatdoyouknow`, `!forgetme`, panic mute and the safe-guess filter. | “Viewers can inspect or delete their saved profile, and the streamer controls what appears on stream.” |
| 2:40–3:00 | Codex clip/session ID, then a simple architecture slide. | “Built with Codex: Twitch EventSub feeds the durable event pipeline, which drives the shared game and OBS overlay.” |

Record the live Twitch portion and synthetic preview separately, then edit them together. Keep the real Twitch activity visible for the live segment, keep simulation labels visible for synthetic traffic, and state measured engineering numbers only.
