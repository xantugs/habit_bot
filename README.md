# Habit Bot

A Discord-first habit accountability bot for two-person self-improvement tracking.

The bot lets users tick daily habits through Discord buttons, stores progress in SQLite, and generates visual progress reports such as tables, heatmaps, comparisons, streak reports, and random habit facts.

## Features

- Daily habit button panels
- Per-user habit tracking
- SQLite progress storage
- 30-day visual habit tables
- CSV export
- GitHub-style heatmap
- User comparison dashboard
- Streak tracking
- Random habit facts
- Automatic morning panels
- Automatic daily progress feed reports
- Proxy support for local Discord access

## Recommended Discord Server Layout

Keep the server simple:

```text
#rules
#habit-tracker
#progress-feed
daily-journal forum
```

### Channel Usage

| Channel | Purpose |
|---|---|
| `#rules` | Rules, command guide, punishment/reward rules |
| `#habit-tracker` | Daily button habit panels |
| `#progress-feed` | Progress reports, streak checks, random facts |
| `daily-journal` forum | Daily reflections and explanations |

## Requirements

- Python 3.11+
- Discord bot token
- Discord server
- Required Python packages listed in `requirements.txt`

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root.

```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_server_id_here
HABIT_CHANNEL_ID=your_habit_tracker_channel_id_here
REPORT_CHANNEL_ID=your_progress_feed_channel_id_here
TZ=Asia/Shanghai
DISCORD_PROXY=http://127.0.0.1:12334
```

### Optional Variables

If these are not set, streaks and random facts will be posted to `REPORT_CHANNEL_ID`.

```env
STREAK_CHANNEL_ID=
FACTS_CHANNEL_ID=
```

## Important Security Note

Never commit `.env` to GitHub.

Your `.gitignore` should include:

```gitignore
.env
.venv/
habit_bot.sqlite3
__pycache__/
*.pyc
```

## Setup

### 1. Create a Discord Bot

Go to the Discord Developer Portal:

1. Create a new application.
2. Go to **Bot**.
3. Reset/copy the bot token.
4. Put the token into `.env` as `DISCORD_TOKEN`.

### 2. Invite the Bot

In the Developer Portal:

1. Go to **OAuth2 → URL Generator**.
2. Select scopes:
   - `bot`
   - `applications.commands`
3. Select permissions:
   - View Channels
   - Send Messages
   - Use Slash Commands
   - Embed Links
   - Read Message History
4. Open the generated URL and invite the bot to your server.

### 3. Get Discord IDs

Enable Developer Mode in Discord:

```text
User Settings → Advanced → Developer Mode
```

Then copy:

- Server ID → `GUILD_ID`
- `#habit-tracker` channel ID → `HABIT_CHANNEL_ID`
- `#progress-feed` channel ID → `REPORT_CHANNEL_ID`

### 4. Run the Bot

```powershell
python bot.py
```

If successful, the terminal should show:

```text
Logged in as habit_bot#....
```

Keep the terminal open while using the bot locally.

## Commands

### Habit Setup

```text
/addhabit name:Gym points:15
/removehabit name:Gym
```

### Daily Use

```text
/panel
/today
```

### Progress and Analysis

```text
/stats days:30
/dashboard days:30
/table days:30
/heatmap days:90
/compare user:@friend days:30
/export days:30
```

### Streaks and Facts

```text
/streak
/streak user:@friend
/fact days:30
/streakreport
/factreport days:30
```

### Manual Report

```text
/report days:30
```

## Daily Automation

The bot automatically posts:

| Time | Action | Channel |
|---|---|---|
| 09:00 | Daily habit panels | `#habit-tracker` |
| 12:00 | Random habit fact | `#progress-feed` |
| 23:30 | Streak warning/check | `#progress-feed` |
| 23:59 | Daily visual progress report | `#progress-feed` |

Times follow the timezone in `.env`, for example:

```env
TZ=Asia/Shanghai
```

## Streak Logic

A day counts as valid if:

```text
completed habits / total habits >= 60%
```

The threshold is controlled in `bot.py`:

```python
VALID_DAY_RATE = 0.60
```

Example:

- 5 total habits
- 3 completed habits
- 3 / 5 = 60%
- streak continues

## Data Storage

The bot stores data locally in SQLite:

```text
habit_bot.sqlite3
```

Main tables:

| Table | Purpose |
|---|---|
| `habits` | Active habits per user |
| `habit_logs` | Daily completion records |
| `daily_messages` | Daily panel messages |
| `daily_reports` | Prevents duplicate report posts |
| `daily_streak_posts` | Prevents duplicate streak posts |
| `daily_fact_posts` | Prevents duplicate fact posts |

## Proxy Notes

If Discord is blocked on the current network, use a local proxy.

Example:

```env
DISCORD_PROXY=http://127.0.0.1:12334
```

This was tested with Hiddify using local proxy port `12334`.

To test proxy access:

```powershell
curl.exe -x http://127.0.0.1:12334 https://discord.com/api/v10/gateway
```

A successful response should include:

```json
{"url":"wss://gateway.discord.gg"}
```

## Local Development Workflow

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py
```

Commit changes:

```powershell
git add bot.py requirements.txt README.md .gitignore
git commit -m "Update habit bot"
git push
```

## Current MVP Status

Working:

- Button habit panels
- SQLite tracking
- Visual progress tables
- Heatmaps
- Compare dashboard
- Streak reports
- Random facts
- Simplified progress feed
- Local proxy support

Possible future upgrades:

- Daily journal detection from Discord Forum posts
- Streak freeze tokens
- Weekly punishment/reward summary
- Web dashboard
- Cloud hosting
- Multi-server support

## Project Philosophy

The bot is designed around a simple loop:

```text
Tick habits.
Track progress.
Keep streaks alive.
Review results.
Stay accountable.
```

Silence, action.
