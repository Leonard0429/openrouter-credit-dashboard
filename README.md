# openrouter-credits

A [Hermes Agent](https://hermes-agent.nousresearch.com/docs/) plugin that answers
*"how much OpenRouter credit do I have left, and how fast am I burning it?"* —
as a first-class agent tool, not a shell one-liner.

It registers the **`openrouter_credits`** tool, which reports:

- **Current balance** — `total_credits − total_usage`, straight from OpenRouter's `/credits`
- **Spend windows** — today, this week, this month, all time (from `/key`)
- **API-key spend cap** — remaining vs. limit, with its reset date, when the key has one
- **Burn rate** — measured from the snapshot history, so it improves over time
- **Estimated runway** — days of credit left at the current rate

Sample output:

```
OpenRouter credit
💳 Balance: $12.12
Lifetime: $42.88 spent of $55.00 purchased
Spend: $0.85 today • $4.57 this week • $28.48 this month • $42.88 all time
API key cap: none — no spend limit set on this key
Approx burn rate: ~$0.65/day (from 7-day spend)
Est. runway: ~18.6 days at that rate
History: 9 snapshot(s) since 2026-10-07
```

## Install

From the Hermes plugin catalog:

```bash
hermes plugins install openrouter-credits
```

Or drop the directory into `$HERMES_HOME/plugins/` and enable it:

```bash
hermes plugins enable openrouter-credits
hermes gateway restart        # a live gateway loads it in the next session
```

## Desktop panel

The package also ships a **live desktop panel** for the Hermes desktop app:

- a **status-bar chip** showing the current balance at a glance, and
- a **full panel page** (sidebar row + ⌘K command) with balance, a
  spent-of-purchased meter, today / this week / this month / all-time spend,
  burn rate, runway, the API-key cap, and a **Record snapshot** button.

It refreshes itself every 60s via React Query, and `mod+alt+r` forces a refresh.

The desktop half is **opt-in**, like the Python half: it inventories in
**Settings → Plugins** but stays off until you toggle it on there.

> **Your API key never reaches the UI.** The renderer holds no credential — the
> panel calls this plugin's own backend route
> (`/api/plugins/openrouter-credits/credits`), which runs inside the Hermes
> process and reuses the exact same fetch code as the agent tool. The panel only
> ever receives dollar amounts.

Routes mount at gateway start, so after enabling the desktop half, restart the
gateway once (`hermes gateway restart`) if the panel shows a backend error.

## Usage

Just ask the agent — *"how much OpenRouter credit do I have left?"* — or call the
tool directly:

```jsonc
{"window_days": 7, "record": true}
```

| Argument | Default | Meaning |
|---|---|---|
| `window_days` | `7` | Trailing window used to measure the burn rate |
| `record` | `true` | Append a snapshot to the history file |

The tool is gated on an OpenRouter key resolving, so it costs no context in
sessions that never touch OpenRouter.

## Tracking credit over time

Every call appends a timestamped snapshot to
`$HERMES_HOME/cache/openrouter-credits/history.jsonl` (capped at 2000 rows,
oldest compacted away). Burn rate is derived from **spend** deltas rather than
balance deltas, so a top-up never distorts it.

History covered by less than 6 hours is deliberately ignored — two snapshots
minutes apart imply a tiny span, and dividing a rounding-level spend delta by it
produces a wildly inflated rate. Short spans fall back to OpenRouter's own
rolling windows.

### Daily watchdog (optional)

Pair it with a Hermes cron job for a hands-off low-balance ping. This script
prints nothing while the balance is healthy, so the daily run is free and silent:

```bash
hermes cron create "OpenRouter credit watchdog" --schedule "every day at 9am" \
  --script openrouter-credit-watch.py --no-agent --deliver bot-chat
```

See `examples/openrouter-credit-watch.py` for the script. On a desktop-only
install (no messaging platform connected) `deliver='bot-chat'` is the working
in-app lane; the default `deliver='local'` saves output without ever showing it.

## Requirements

- Hermes Agent `>= 0.21.5`
- An OpenRouter API key (`OPENROUTER_API_KEY`), resolved through Hermes' own
  credential resolution — including the config file, `.env`, and credential pools.

## Disclosure

What this plugin does, so you can decide before installing:

- **Network calls.** Two HTTPS GETs to the configured OpenRouter base URL
  (default `https://openrouter.ai/api/v1`): `/credits` and `/key`. Nothing else
  is contacted, and there is no telemetry or usage reporting.
- **Credentials.** It reads your own OpenRouter API key, via Hermes'
  `agent.secret_scope.get_secret("OPENROUTER_API_KEY")`, and sends it as a
  `Authorization: Bearer` header to that base URL only. The key is never logged,
  written to disk, or included in tool output.
- **Local writes.** One file, `$HERMES_HOME/cache/openrouter-credits/history.jsonl`,
  holding timestamps and dollar amounts. No credentials.
- **Desktop panel backend.** `dashboard/plugin_api.py` exposes two routes under
  `/api/plugins/openrouter-credits/` (`GET /credits`, `POST /record`). They add
  no new network targets — they run the same two OpenRouter GETs above in the
  Hermes process and return dollar amounts to the UI. `GET /credits` is
  read-only and never records a snapshot; only the agent tool and the panel's
  explicit **Record snapshot** button append to the history file. Responses are
  cached for 30s so a polling panel never amplifies upstream requests.
- **No other reads.** It does not touch other tools' credential stores, browser
  profiles, or any path outside its own history file.
- **Unattended-safe.** No prompts, no OAuth flow, no shell commands, no
  background processes. Reads are bounded by a 15-second HTTP timeout and fail
  with a short message.
- **Read-only against OpenRouter.** It never creates keys, changes limits, or
  moves money.

## License

MIT — see [LICENSE](LICENSE).
