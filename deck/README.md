# Deck

Source for the pitch deck. `project/slides/<id>.html` is one slide each (1920x1080, inline styles only), `project/deck.json` is the order.

Edit a slide's HTML directly, or change the text in `build.py` / `agent_results.json` and run `python3 deck/build.py` to regenerate every slide.
Rules we kept: nothing under 32px, one idea per slide, marble behind every slide, colour only for state, no em dashes.
