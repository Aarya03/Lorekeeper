# Hackathon readiness plan

Working plan based on the review and the 2026-11-03 03:30 IST deadline. The project is a real Twitch product; synthetic traffic is for rehearsal and engineering measurements only.

## Implemented in this pass

- Summarize lore locally into short third-person clues; avoid republishing raw chat in the dashboard activity feed, lore cards, relic sources, chapter clues, and recap text. No LLM call is introduced.
- Generate concise, context-aware lore summaries, relic names/powers, boss identities, and chapter copy from approved memories across stream categories; retain curated visual fallbacks.
- Rotate chapters through least-played approved memories and vary opening/result narration.
- Make the lore count say “pending,” and give rejected/retired memories Restore and Delete actions.
- Expose raid replay, prompt-injection replay, a bounded load run, events/sec, consumer lag, and projection p95 in the dashboard.
- Keep the real chat commands and streamer panic mute visible in the rehearsal area. Synthetic replay no longer creates lore.

## Oct 28–29: protect the live loop

- Rebuild with `docker compose up --build` and confirm `/api/ready` plus **Live · EventSub**.
- In a real stream, send a running memory, confirm its summary appears as pending, approve it, then start a chapter.
- Confirm two real accounts can vote with `!vote A` and `!vote B`, the overlay tally updates, and the revealed outcome appears in the next chapter.
- Approve three different memories and check that successive chapters rotate instead of repeating one clue.
- If live chat → approved lore → chapter → vote → overlay fails, fix that path before doing more load work.

## Oct 30: safety and privacy

- On the real channel, try `!forgetme` and then `!whatdoyouknow` from a test account; confirm the saved lore and vote records are removed and no new lore is created for that viewer.
- Run **Safety replay** with prompt-injection text. Confirm the text stays out of the overlay and lore inbox.
- Toggle panic mute during an overlay reaction; confirm it takes effect immediately.
- Capture a short screen recording of these checks for the demo edit.

## Oct 31: replay, metrics, and evidence

- Run **Replay raid burst** and the dashboard's short load run; watch the pipeline values recover.
- Run the sustained profile: `python tools/load_test.py --duration 60 --rate 10000 --profile raid`.
- Save the generated JSON in `load-test-results/` and a Grafana screenshot. Report measured results honestly, including lag and whether all events projected.
- Repeat the same deterministic replay twice and compare the summaries/story outcome manually; do not claim a golden replay until a checked-in snapshot exists.

## Nov 1: live rehearsal and freeze

- Rehearse with friends on the real channel, including a return to a previously seeded memory and at least two different chapter choices.
- Run a 30-minute soak with replay traffic in a separate rehearsal window. Check for stuck consumers, growing lag, or stray overlay speech.
- Freeze features after the live path and safety checks pass. Fix only demo-blocking issues afterward.

## Nov 2: record and submit

- Record the real stream demo in short segments, then edit to under three minutes with a few seconds of slack.
- Include the real Twitch vote, approval gate, relic reveal, visible privacy/safety controls, Codex use, and a clearly labelled synthetic engineering result.
- Update README with the clean run steps, actual load numbers, Grafana screenshot, project description, repository URL, and Codex session ID.
- Verify the public YouTube link and submit before the deadline.

## Do not claim until verified

- 10,000 events/sec achieved: the runner's target is not its measured throughput.
- No event loss: confirm projected count equals sent count and dead-letter delta is zero.
- Full privacy deletion: check lore, chapter votes, overlay commands, and the post-forget behavior.
- Audience entertainment: use the five-minute live test in [demo_story.md](demo_story.md) and record viewer comprehension/participation instead of assuming it.
