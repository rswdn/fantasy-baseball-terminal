"""Fantasy Baseball Terminal — a Streamlit player-trend dashboard."""

from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from data_provider import (
    DataSnapshot,
    PlayerType,
    fetch_player_season_stats,
    load_data,
    refresh_live_data_windows,
)
from scoring import score_hitters, score_pitchers, score_streamers


DATA_DIR = Path(__file__).resolve().parent / "data"
TABLE_KEYS = ("hitters_table", "pitchers_table", "streamers_table")


st.set_page_config(
    page_title="Fantasy Baseball Terminal",
    page_icon="⚾",
    layout="wide",
)


def format_refresh_time(value: datetime | None) -> str:
    """Return a concise cache timestamp for the sidebar."""
    if value is None:
        return "No live refresh yet"
    return value.strftime("%b %d, %Y · %I:%M %p %Z")


def _native_value(value: Any) -> Any:
    """Convert pandas and NumPy scalars into Session State friendly values."""
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _handle_player_selection(
    table_key: str,
    dataframe: pd.DataFrame,
    player_type: PlayerType,
    context_label: str,
) -> None:
    """Store the selected row and clear selections from the other tables."""
    table_state = st.session_state.get(table_key)
    # Streamlit stores dataframe widget state as a dictionary. Depending on
    # whether it came directly from the widget or was reset through session
    # state, it may also be an attribute-access wrapper around that dict.
    selection = table_state.get("selection", {}) if table_state else {}
    rows = selection.get("rows", []) if selection else []
    if not rows:
        return

    row = dataframe.iloc[rows[0]]
    st.session_state.player_detail = {
        "player_type": player_type,
        "context_label": context_label,
        "row": {column: _native_value(value) for column, value in row.items()},
    }
    for other_key in TABLE_KEYS:
        if other_key != table_key and other_key in st.session_state:
            st.session_state[other_key] = {"selection": {"rows": []}}


def display_table(
    dataframe: pd.DataFrame,
    *,
    empty_message: str,
    key: str,
    player_type: PlayerType,
    context_label: str,
) -> None:
    """Render a formatted, interactive dataframe."""
    if dataframe.empty:
        st.info(empty_message)
        return

    st.dataframe(
        dataframe,
        key=key,
        width="stretch",
        hide_index=True,
        on_select=partial(
            _handle_player_selection,
            key,
            dataframe,
            player_type,
            context_label,
        ),
        selection_mode="single-row",
        column_config={
            "MLBAM_ID": None,
            "Score": st.column_config.ProgressColumn(
                "Score",
                help="Weighted score relative to the currently filtered player pool.",
                min_value=0,
                max_value=100,
                format="%.1f",
            ),
            "Roster%": st.column_config.NumberColumn("Roster %", format="%.0f%%"),
            "ERA": st.column_config.NumberColumn("ERA", format="%.2f"),
            "WHIP": st.column_config.NumberColumn("WHIP", format="%.2f"),
        },
    )


@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def cached_player_season_stats(
    player_name: str,
    player_id: int | None,
    player_type: PlayerType,
    season: int,
) -> dict[str, Any]:
    """Cache player details independently from the dashboard snapshots."""
    return fetch_player_season_stats(
        player_name,
        player_id,
        player_type,
        season,
    )


def _clear_player_detail() -> None:
    st.session_state.pop("player_detail", None)
    for table_key in TABLE_KEYS:
        if table_key in st.session_state:
            st.session_state[table_key] = {"selection": {"rows": []}}


def _render_stat_metrics(metrics: list[tuple[str, Any]]) -> None:
    """Render season totals in stable rows of four metrics."""
    for start in range(0, len(metrics), 4):
        row = metrics[start : start + 4]
        columns = st.columns(4)
        for column, (label, value) in zip(columns, row, strict=False):
            column.metric(label, value if value not in (None, "") else "-")


@st.dialog(
    "Player details",
    width="large",
    icon=":material/person:",
    on_dismiss=_clear_player_detail,
)
def show_player_detail(selection: dict[str, Any]) -> None:
    """Show season totals and selected-window context for one player."""
    row = selection["row"]
    player_type: PlayerType = selection["player_type"]
    player_name = str(row["Player"])
    team = row.get("Team") or "MLB"
    position = row.get("Pos") or row.get("Role") or ""

    st.subheader(player_name)
    st.caption(" · ".join(value for value in (str(team), str(position)) if value))

    player_id_value = row.get("MLBAM_ID")
    player_id = int(player_id_value) if player_id_value is not None else None
    season = datetime.now().year
    try:
        with st.spinner("Loading season stats..."):
            profile = cached_player_season_stats(
                player_name,
                player_id,
                player_type,
                season,
            )
    except Exception as error:
        st.warning(f"Season stats are currently unavailable. {error}")
    else:
        stats = profile["stats"]
        st.markdown(f"**{profile['season']} season**")
        if not stats:
            st.info("No MLB season totals are available for this player.")
        elif player_type == "hitter":
            _render_stat_metrics(
                [
                    ("G", stats.get("gamesPlayed")),
                    ("PA", stats.get("plateAppearances")),
                    ("HR", stats.get("homeRuns")),
                    ("RBI", stats.get("rbi")),
                    ("R", stats.get("runs")),
                    ("SB", stats.get("stolenBases")),
                    ("AVG", stats.get("avg")),
                    ("OBP", stats.get("obp")),
                    ("SLG", stats.get("slg")),
                    ("OPS", stats.get("ops")),
                    ("BB", stats.get("baseOnBalls")),
                    ("K", stats.get("strikeOuts")),
                ]
            )
        else:
            record = (
                f"{stats.get('wins', 0)}-{stats.get('losses', 0)}"
                if stats
                else "-"
            )
            _render_stat_metrics(
                [
                    ("G", stats.get("gamesPlayed")),
                    ("GS", stats.get("gamesStarted")),
                    ("IP", stats.get("inningsPitched")),
                    ("W-L", record),
                    ("K", stats.get("strikeOuts")),
                    ("BB", stats.get("baseOnBalls")),
                    ("SV", stats.get("saves")),
                    ("ERA", stats.get("era")),
                    ("WHIP", stats.get("whip")),
                    ("K/9", stats.get("strikeoutsPer9Inn")),
                    ("H/9", stats.get("hitsPer9Inn")),
                    ("HR/9", stats.get("homeRunsPer9")),
                ]
            )

        st.link_button(
            "Open MLB profile",
            f"https://www.mlb.com/player/{profile['player_id']}",
            icon=":material/open_in_new:",
        )

    st.divider()
    st.markdown(f"**{selection['context_label']} snapshot**")
    snapshot_row = {
        column: value
        for column, value in row.items()
        if column not in {"MLBAM_ID", "Player", "Team", "Pos", "Role"}
    }
    st.dataframe(
        pd.DataFrame([snapshot_row]),
        hide_index=True,
        width="stretch",
        column_config={
            "Roster%": st.column_config.NumberColumn("Roster %", format="%.0f%%"),
            "Score": st.column_config.ProgressColumn(
                "Score",
                min_value=0,
                max_value=100,
                format="%.1f",
            ),
        },
    )


@st.cache_data(show_spinner=False)
def cached_load_data(data_dir: str, window: int | str) -> DataSnapshot:
    """Load cached window data without triggering a live refresh."""
    return load_data(Path(data_dir), window, auto_refresh=False)


with st.sidebar:
    st.header("Player filters")
    max_roster = st.slider(
        "Maximum roster percentage",
        min_value=0,
        max_value=100,
        value=65,
        step=5,
        help="Show players rostered in no more than this percentage of leagues.",
    )
    minimum_pa = st.number_input(
        "Minimum plate appearances",
        min_value=0,
        max_value=100,
        value=10,
        step=1,
    )
    minimum_ip = st.number_input(
        "Minimum innings pitched",
        min_value=0.0,
        max_value=50.0,
        value=5.0,
        step=1.0,
    )
    time_window = st.segmented_control(
        "Time window",
        options=[7, 14, 30, "season"],
        default=30,
        format_func=lambda window: (
            "Full season" if window == "season" else f"Last {window} days"
        ),
    )
    refresh_requested = st.button(
        "Refresh live data",
        type="primary",
        width="stretch",
    )

time_window = time_window or 30
window_label = (
    "Current season"
    if time_window == "season"
    else f"Last {time_window} days"
)
if refresh_requested:
    with st.spinner("Refreshing all MLB data windows…"):
        snapshots = refresh_live_data_windows(DATA_DIR)
    cached_load_data.clear()
    snapshot = snapshots.get(time_window) or cached_load_data(str(DATA_DIR), time_window)
else:
    with st.spinner(f"Loading cached {window_label.lower()} MLB data…"):
        snapshot = cached_load_data(str(DATA_DIR), time_window)

hitters = snapshot.hitters
pitchers = snapshot.pitchers
streamers = snapshot.streamers

with st.sidebar:
    st.divider()
    st.caption(f"Source: {snapshot.source}")
    st.caption(f"Updated: {format_refresh_time(snapshot.fetched_at)}")
    st.caption(
        "Caches refresh every 12 hours. Unknown roster percentages default to 0%; "
        "edit data/roster_percentages.csv to supply fantasy-platform values."
    )

filtered_hitters = hitters[
    (hitters["Roster%"] <= max_roster) & (hitters["PA"] >= minimum_pa)
]
filtered_pitchers = pitchers[
    (pitchers["Roster%"] <= max_roster) & (pitchers["IP"] >= minimum_ip)
]
filtered_streamers = streamers[
    (streamers["Roster%"] <= max_roster)
    & (streamers["Projected IP"] >= minimum_ip)
]

st.title("⚾ Fantasy Baseball Terminal")
st.write(
    "Surface rising hitters, pitchers, and short-term streaming options with "
    "customizable availability and workload filters."
)
if snapshot.source in {"demo fallback", "stale live cache"}:
    st.warning(snapshot.message)
elif refresh_requested:
    st.success("Live MLB data refresh completed for all time windows.")

tab_hitters, tab_pitchers, tab_streamers = st.tabs(
    ["Trending Hitters", "Trending Pitchers", "Streamers"]
)

with tab_hitters:
    st.subheader(f"Trending hitters · {window_label}")
    st.caption(
        "Score favors production, quality of contact, discipline, and speed. "
        "wRC+ is estimated from rolling Statcast wOBA relative to the league."
    )
    display_table(
        score_hitters(filtered_hitters),
        empty_message="No hitters match the current roster and PA filters.",
        key="hitters_table",
        player_type="hitter",
        context_label=window_label,
    )

with tab_pitchers:
    st.subheader(f"Trending pitchers · {window_label}")
    st.caption("Score favors strikeout skill, run prevention, and weak contact.")
    display_table(
        score_pitchers(filtered_pitchers),
        empty_message="No pitchers match the current roster and IP filters.",
        key="pitchers_table",
        player_type="pitcher",
        context_label=window_label,
    )

with tab_streamers:
    st.subheader("Upcoming streamers")
    st.caption("Score combines pitcher skill, opponent quality, park, and availability.")
    display_table(
        score_streamers(filtered_streamers),
        empty_message="No streamers match the current roster and IP filters.",
        key="streamers_table",
        player_type="pitcher",
        context_label="Upcoming streamer",
    )

if "player_detail" in st.session_state:
    show_player_detail(st.session_state.player_detail)
