# Demo checklist

Things that will bite on stage if nobody checks them. Work top to bottom the morning of judging.

## The night before

- [ ] **Time the three model-backed actions yourself**, on the rehearsal track (`?track=rehearsal`): a queue correction, the experiment's "Run it live", and the conflict outcome "the policy has changed". There is no API key on this machine, so they go through the `claude` CLI, which can be far slower than the 10 to 25 s the screen promises. If any takes 40 s or more, show it from saved state and keep only the instant beats live.
- [ ] Look at `/` once the main-track playbooks exist. The split screen must show **A writes off, B escalates**. Flip "As induced / After sign-off" and confirm the contrast. If both sides still escalate, Client A's write-off question has not been answered on the main track.
- [ ] `/results.html` shows month-4 numbers, not the March holdout fallback. If `/api/metrics` is empty, `runs/results.json` was never written.
- [ ] Record the backup video of the whole 3 minutes. Live runs fail on stage.
- [ ] Decide what you will say about: simulated controller sign-off, synthetic clients, zero-shot being about as accurate as the playbook on ordinary exceptions, and which BenchRec precision you are quoting (pair-level vs strict). Judges will ask.

## One hour before

- [ ] `git pull`, then **stop committing**. Tell every Claude session to stop writing files in the repo.
- [ ] Start the server **without `--reload`**: `uv run uvicorn shadow.server:app --port 8787`. With `--reload`, any file save anywhere in the repo restarts the server mid-demo.
- [ ] `scripts/reset_rehearsal.sh` so the taught list is empty and the band questions are back.
- [ ] Warm both experiment caches so no GET returns "not computed yet": open `/`, press Run once for the same-transaction experiment and once for Bank change, wait for both.
- [ ] Laptop on power, notifications off, display at 1280 px wide or more (below about 1100 px the nav wraps and the band card's payoff drops under the fold).

## At the podium

- [ ] **Open a fresh browser tab.** The track (`?track=`) is remembered per tab and Light/Dark per browser. A tab used for rehearsal keeps showing the REHEARSAL banner to the judges. Decide the track on purpose: the band answer and undo beats are safest on `?track=rehearsal`, and it is honest to say so ("this is a sandbox copy of Client A's playbook").
- [ ] No `?grades=1` in any URL. That flag puts answer-key data on the projector.
- [ ] If the server dies, only the experiment screen falls back to saved fixtures, under a "FIXTURE DATA, NOT LIVE" banner. Queue, playbook and results show an error panel. Switch to the backup video rather than restarting on stage.

## The instant beats (no model call, measured under 1 s)

1. `/playbook.html?client=A&track=rehearsal`. The card asks about mobile deposits seen up to $42.94.
2. Hand over the laptop: "You are the owner. What is your limit?" They type 50, Enter.
3. The band closes to a line, the rule sentence now says 50.00, and "Now cleared, at $0.00: MOBILE DEPOSIT $46.17" appears.
4. "Undo this answer", confirm. "3 past items checked, 1 re-opened", the deposit is back in review, the sentence says 42.94 again.
5. Optional: add `&role=bookkeeper` to show a junior being held: "Recorded, not applied".
