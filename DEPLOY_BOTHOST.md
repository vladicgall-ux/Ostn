# Deploying to BotHost

This repo bundles a Telegram bot (`telegram_bot.py`) that starts SpiderFoot
internally (bound to `127.0.0.1`, not exposed to the internet) and lets you
control scans through Telegram commands.

Vercel is not used here: SpiderFoot needs a long-running process and a
persistent disk for its SQLite database, neither of which serverless
functions provide. BotHost runs your script as a normal persistent process,
which is what SpiderFoot needs.

## 1. Create the bot in Telegram

1. Talk to [@BotFather](https://t.me/BotFather), send `/newbot`, follow the
   prompts, and copy the token it gives you.
2. Get your own numeric Telegram ID from [@userinfobot](https://t.me/userinfobot)
   — you'll restrict the bot to yourself (or your team) with it.

## 2. Push this repo

Push this branch/repo to GitHub (or wherever BotHost pulls from), or upload
the project as a zip if BotHost supports that instead.

## 3. Configure the project on BotHost

In the BotHost panel, create a new Python project pointing at this
repository and set:

- **Entry point / main file:** `telegram_bot.py`
- **Requirements file:** `requirements.txt` (already includes SpiderFoot's
  own dependencies plus `python-telegram-bot` and `python-dotenv`)
- **Python version:** 3.9+ (match what SpiderFoot's `requirements.txt`
  supports)

Set these environment variables in the panel (see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | token from BotFather |
| `ALLOWED_USER_IDS` | strongly recommended | comma-separated Telegram user IDs allowed to use the bot |
| `SPIDERFOOT_USECASE` | no | default scan profile: `passive`, `footprint`, `investigate`, `all` (default `footprint`) |
| `POLL_INTERVAL_SECONDS` | no | how often the bot checks scan progress (default `20`) |

Do **not** set `SPIDERFOOT_HOST`/`SPIDERFOOT_PORT` unless you need to change
the defaults (`127.0.0.1:5001`) — SpiderFoot's web UI must stay bound to
localhost only, since it has no authentication of its own and BotHost gives
you no reason to expose it publicly.

## 4. Start the process

Start/restart the project from the BotHost panel. On boot,
`telegram_bot.py`:

1. launches `sf.py -l 127.0.0.1:5001` as a child process (the SpiderFoot
   web UI/API, local only),
2. waits for it to respond,
3. starts long-polling Telegram for commands.

Persistent storage (`spiderfoot.db`, logs, cache) lives under the project's
working directory — make sure BotHost keeps that directory across restarts
if you want scan history to survive a redeploy.

## 5. Using the bot

- `/scan <target> [usecase]` — start a scan, e.g. `/scan example.com` or
  `/scan example.com passive`. Only scan targets you're authorized to
  investigate.
- `/status <scan_id>` — check progress.
- `/results <scan_id>` — download findings as CSV.
- `/list` — list recent scans and their status/IDs.

The bot automatically messages you back when a scan you started finishes.
