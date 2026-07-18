# Fantasy Baseball Terminal

A Streamlit app for exploring trending fantasy baseball players using live
Statcast data through `pybaseball` and official MLB Stats API data.

Live results are normalized into CSV files under `data/cache/<window>/`. The
cache refreshes after 12 hours or when **Refresh live data** is clicked. The
original CSVs in `data/` are retained as an offline fallback.

The time-window control supports the last 7, 14, or 30 days and the full
current MLB regular season.

## Run locally

```bash
cd fantasy-baseball-terminal
source .venv/bin/activate
streamlit run app.py
```

Click any dataframe column header to sort the table.

## Data notes

- Statcast contact and pitch metrics come through `pybaseball`.
- Official PA, IP, ERA, WHIP, steals, teams, and schedules come from MLB Stats API.
- Rolling `wRC+` is estimated from player wOBA relative to league wOBA.
- Fantasy roster percentages are not available from MLB. Add or update values in
  `data/roster_percentages.csv`; unknown players default to 0%.
