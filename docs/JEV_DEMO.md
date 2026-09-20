# Jev in the review queue

Open http://127.0.0.1:8795/queue.html?track=stage. Jev is an optional assistant inside each unresolved group. The current local server is connected to TypeSafe.

## Use it

1. Choose a company and unresolved case or group.
2. Under **Jev · Find the next step**, click **Get suggestion**.
3. Read the suggested reviewer and next step. Expand **Model choices and uncertainty** to inspect alternatives.
4. Optionally add context and ask again. Notes remain unverified and do not count as policy approval or payment verification.
5. Use the existing policy preview and approval flow to change company policy. Jev does not execute that action.

Repeated requests with the same case and note show a labelled cached result. Refreshing the queue makes no model calls. The original request latency is shown for cached results.

## A short judging beat

Start on Lucky Quarter's vendor payment group. Ask Jev for the next step. It reads the linked bank-change email and recommends a responsible reviewer. Point out that the verification hold stays in place.

Switch to Meridian's $12.40 shortfall and ask again. The company context changes the reviewer suggestion. Then return to the grouped policy question and demonstrate the existing preview, approval and Undo flow.

Three actual calls during integration verification on September 19:

| Case | Suggested reviewer | Next step shown | Request latency |
| --- | --- | --- | --- |
| Lucky Quarter vendor payments | Ops manager | Independently verify payment details | 622 ms |
| Lucky Quarter three shortfalls | Owner | Confirm company policy | 473 ms |
| Meridian single $12.40 shortfall | AR lead | Manual triage, next-step confidence too low | 395 ms |

These are observed responses on synthetic demo cases, not an accuracy or speed benchmark. Jev selected AR lead at 0.99 confidence for Meridian, but its next-step confidence was 0.42, below the demo's 0.65 presentation threshold. The interface displays that uncertainty instead of guessing. Future model responses may differ.

## Setup on another machine

Start the stage server as usual, expand **Jev review assistant · Connect Jev**, and paste a TypeSafe API key. The key stays in the server's ignored sandbox with owner-only permissions. It is never stored in browser storage or committed. Alternatively use the `TYPESAFE_API_KEY` environment variable. Resetting the sandbox removes its saved key.

This server caps usage at 20 uncached requests, including failures. The cache and counter last until restart. Each request asks two questions in one TypeSafe call. No live model calls are needed for the test suite. Anthropic still handles policy induction and investigation. The original walkthrough and offline replay remain available.
