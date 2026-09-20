# Audita live HackMIT deployment

Public app: https://audita-hackmit.vercel.app

The same UI and policy engine as the local judging demo are now reachable on Vercel. Vercel hosts the static UI and forwards /api requests to the public demo gateway on the judging laptop, through Tailscale Funnel on HTTPS port 8443. This is a live application, not a replay.

Keep the laptop awake, its lid open and its internet connected during judging. The recorded fallback under More is served entirely by Vercel and remains usable if the API goes offline.

## Start or recover the backend

From the repository root:

```
uv run uvicorn demo_public:app --host 127.0.0.1 --port 8797
tailscale funnel --bg --https=8443 http://127.0.0.1:8797
```

Use one gateway worker. It serializes request execution while selecting an isolated sample workspace for the visitor. An HttpOnly secure session cookie identifies the workspace. Visitors cannot approve each other's previews or change the presenter's local state. Demo workspaces are stored under ignored work/public-live; no company uploads or hidden answer keys are included. Maximum 300 visitor workspaces per server instance; each visitor's cookie lasts six hours.

The sample /api routes expose selected reads and deterministic policy preview, approval, band-answer and undo writes. Onboarding uses the separate /company-api routes described below; API-key setup remains private. Jev reviewer suggestions use the existing server-side key, are limited to three attempts per visitor, and retain the existing global 20-call cap. Keys are never bundled with frontend files. Jev is optional; no model is needed for the main approval-and-undo flow.

## Publish UI changes

```
python3 scripts/build_presenter_guide.py
python3 scripts/build_live_demo.py
npx vercel deploy work/live-vercel --prod --yes --project audita-hackmit
```

The external API origin is in scripts/build_live_demo.py. Do not run the older recorded-demo packaging command against the live production project.

## Verified behavior

- Public root asks View the demo or Set up a company; View the demo opens the prepared queue.
- Policy preview shows 9 to 6 review items.
- Approval clears three receipts.
- A second visitor still sees nine review items.
- Both vendor payment holds remain.
- Undo reopens the three receipts.
- The real-data benchmark is available on the same origin.
- Unauthorized routes and grade requests are rejected; responses are never CDN-cached.

## Company onboarding

The public navigation links to /company/index.html?track=main. This restores Andrew Zweiback's setup, CSV identification and mapping, cold-start review, policy induction and reconciliation flow. /company-api requests reach a separate process per visitor, including background jobs; uploads cannot mutate the sample queue or another visitor's company. Public uploads are limited to 8 MB and eight concurrent company workspaces. OpenAI calls share a 20-call demo budget, persisted under work/public-companies/model-call-count; the prepared review queue does not use this budget. Keep this laptop awake and connected.
