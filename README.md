# Market Pulse — one-glance market dashboard

Builds a visual dashboard three times every trading day:

| Snapshot | New York time | Your time, Nov–Mar | Your time, Mar–Nov |
|---|---|---|---|
| Opening Bell | 9:35 am | 7:35 am | 6:35 am |
| Midday | 12:30 pm | 10:30 am | 9:30 am |
| Closing Bell | 4:05 pm | 2:05 pm | 1:05 pm |

Your clocks stay on UTC-7 all year, but New York's clocks still change. The script works in New York time, so the snapshots always land on the real open, midday and close. You never need to change the schedule.

**On the dashboard:** a risk-on/off regime gauge (9 signals) · S&P 500, Nasdaq 100, Dow, Russell 2000, TSX · index futures · today's intraday path · cross-asset scoreboard · S&P 500 heatmap sized by market cap (click a sector to zoom) · sector bars and 1D/5D/1M/3M/YTD heat table · VIX and the full Treasury curve (3M–30Y) with a 1-month comparison · dollar index, USD/CAD, gold, silver, WTI, Brent, natural gas, copper · Bitcoin, Ethereum, Solana · breadth (advancers/decliners, % above the 50-day and 200-day averages, 52-week highs and lows, equal-weight vs cap-weight) · top gainers and losers · six risk-on/off ratio charts · calendar links.

Each tile shows a 3-month sparkline, 5D/1M/YTD changes and where the price sits in its 52-week range. Hover a tile for 3M.

---

## 1. Install (one time, about 5 minutes)

1. Install **Python 3.11+** from python.org. On Windows, tick **"Add python.exe to PATH"**.
2. Put this folder somewhere permanent, e.g. `Documents\market_pulse`.
3. Open a terminal in the folder and run:
   ```
   pip install -r requirements.txt
   ```
4. Try it:
   ```
   python market_pulse.py --demo     # instant preview with fake data
   python market_pulse.py            # real data (first run ~1–2 min while it caches market caps)
   ```
   On Windows you can also double-click `run_dashboard.bat`.

The dashboard opens in your browser. It's saved as `output/market_pulse_latest.html`, and every snapshot is archived in `output/archive/` (e.g. `2026-11-17_open.html`).

## 2. Schedule it

**Windows:** in PowerShell, in this folder, run once:
```
powershell -ExecutionPolicy Bypass -File setup_windows_schedule.ps1
```
This creates the task "Market Pulse". It runs every 15 minutes on weekdays from 6:00 am to 2:45 pm, and each run builds a snapshot only if one is due. The task is set to wake the PC from sleep. It can't run if the PC is shut down.

**Mac:** run `crontab -e` and add (adjust the path):
```
*/15 6-14 * * 1-5  cd /Users/YOU/market_pulse && /usr/bin/env python3 market_pulse.py --auto
```

## 3. Optional: get it on your phone (Telegram)

1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Send any message to your new bot. Then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}`.
3. Copy `settings.example.json` to `settings.json`, fill in both values, and set `"telegram_always": true`.

Each snapshot then sends you a text summary plus the dashboard file. Tap the file to open the full interactive version on your phone.

Set `"never_open_browser": true` if you only want Telegram and no browser pop-ups.

## Customise

Everything is in the **CONFIG** block at the top of `market_pulse.py`:
- Add or remove tickers (any Yahoo Finance symbol, e.g. `XIU.TO`, `^N225`, `EURUSD=X`)
- Change the snapshot times (`SESSIONS`)
- Change the risk-on/off ratios (`RATIOS`)
- Colours: set the environment variable `MP_COLORS=cvd` for a blue/red colour-blind-safe palette

## Notes
- Data comes from Yahoo Finance through `yfinance`. It's free and unofficial: quotes can be ~15 min delayed, and Yahoo occasionally breaks it for a day. If that happens, run `pip install -U yfinance`.
- The S&P 500 member list (from Wikipedia) and market caps are cached for a week in `cache/`. Delete that folder to refresh.
- Regime score = share of the 9 signals that are risk-on: 6 ratios above their 50-day average, S&P above its 200-day, VIX < 20, and more than 50% of stocks above their 50-day. ≥67 = Risk-On, ≤33 = Risk-Off.
- For information only — not investment advice.
