# Lorekeeper live demo: The Unresolved Hypothesis

## The story

Chat remembers a recurring science stream moment: an experiment behaved strangely until viewers noticed a loose sensor cable. The streamer approves the observation. Lorekeeper turns it into a field-note summary, a research charm, and a fictional lore boss. Chat votes to deploy the charm and reveal an early hint, or preserve the sample and get a longer hunt. Then viewers solve a lore-linked semantic word puzzle together in real Twitch chat.

Use a real recurring moment from your stream. The sensor-cable example is only a rehearsal prompt. Lorekeeper keeps the science-fiction framing fictional and does not present generated text as a scientific finding.

## What to prepare

- Connect the broadcaster through Twitch OAuth and confirm Live - EventSub.
- In LM Studio load an instruct model for optional narration and an embedding model for guess scoring. Configure `EMBEDDING_PROVIDER=lm_studio`, the exact `EMBEDDING_MODEL` identifier, and `EMBEDDING_DIMENSIONS=768`.
- Seed two safe callbacks in real Twitch chat, approve them in Community Observations, and check that the summaries and relic names are distinct.
- Keep real Twitch chat, dashboard, OBS overlay, and Grafana visible. Have at least two real viewers ready.
- Test one complete signal hunt before recording. The synthetic preview is labelled and cannot stand in for the live Twitch segment.

## Rehearse the real loop

1. An alt account posts a recurring callback, for example: "Remember when chat spotted the loose cable during the experiment?"
2. The streamer approves the pending observation. Confirm the card shows a short paraphrase and the narrator source.
3. Start a field study. Real viewers vote with `!vote A` or `!vote B`. A reveals hint one immediately; B keeps that hint sealed initially and adds 20 seconds.
4. Reveal the vote and start the lore boss signal hunt. Chat enters `!guess WORD`. Show the clue, shared heat, timed hints, and best guesses updating on the OBS overlay. Exact spelling solves the encounter.
5. Show the solve reveal, researcher XP/titles, and charm resonance. Start the next field study and show the previous result carry forward.
6. Demonstrate `!whatdoyouknow`, then `!forgetme`, and confirm the viewer profile and derived lore are removed.

## Three-minute recording outline

| Time | Show | Narration |
|---|---|---|
| 0:00-0:20 | Real Twitch chat, the approved observation, and its research charm/lore boss. | "Lorekeeper turns an approved community memory into a puzzle the whole chat can solve together." |
| 0:20-0:40 | Real viewers vote `!vote A` or `!vote B`; show the tally and selected hint/time effect. | "Chat chooses between an early hint or a longer search. That choice changes the encounter." |
| 0:40-1:25 | Start the live signal hunt. Chat submits `!guess WORD`; show heat and a timed hint on OBS. | "Every guess gives everyone a semantic temperature. Viewers compare ideas, get warmer together, and race to find the word." |
| 1:25-1:45 | Show solve reveal, XP/title credit, and relic resonance. | "The solve becomes part of the community's continuing story." |
| 1:45-2:10 | Run Preview with 200 simulated guessers and show Grafana events/sec, lag, and p95. | "This is labelled synthetic chat through the same game service, not a claim about live audience size." |
| 2:10-2:35 | Show `!whatdoyouknow`, `!forgetme`, panic mute, and the safety replay. | "Viewers can inspect and delete their saved memory and profile. The streamer can mute the narrator immediately." |
| 2:35-2:50 | Show a brief Codex clip and session ID. | "Codex helped build the Twitch integration, local story generation, event pipeline, and live game." |
| 2:50-3:00 | Show Twitch EventSub -> Redpanda -> semantic signal hunt -> dashboard and OBS. | "A real community memory becomes a live puzzle, and its answer carries into the next chapter." |

Record the live Twitch portion and synthetic preview separately, then edit them together. Keep synthetic activity visibly labelled and report measured load-test numbers only.
