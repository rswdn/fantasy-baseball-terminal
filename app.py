"""Fantasy Baseball Terminal — a Streamlit player-trend dashboard."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from data_provider import DataSnapshot, load_data, refresh_live_data_windows
from scoring import score_hitters, score_pitchers, score_streamers


DATA_DIR = Path(__file__).resolve().parent / "data"


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


def display_table(dataframe: pd.DataFrame, *, empty_message: str) -> None:
    """Render a formatted, interactive dataframe."""
    if dataframe.empty:
        st.info(empty_message)
        return

    st.dataframe(
        dataframe,
        use_container_width=True,
        hide_index=True,
        column_config={
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
        use_container_width=True,
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
    )

with tab_pitchers:
    st.subheader(f"Trending pitchers · {window_label}")
    st.caption("Score favors strikeout skill, run prevention, and weak contact.")
    display_table(
        score_pitchers(filtered_pitchers),
        empty_message="No pitchers match the current roster and IP filters.",
    )

with tab_streamers:
    st.subheader("Upcoming streamers")
    st.caption("Score combines pitcher skill, opponent quality, park, and availability.")
    display_table(
        score_streamers(filtered_streamers),
        empty_message="No streamers match the current roster and IP filters.",
    )
