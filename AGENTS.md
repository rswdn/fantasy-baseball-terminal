# Fantasy Baseball Terminal — Agent Guide

## Project overview

Fantasy Baseball Terminal is a Streamlit application for identifying trending fantasy baseball hitters, pitchers, and streaming options.

The project uses:

- Streamlit for the user interface
- pandas for data manipulation
- pybaseball / Statcast for advanced baseball data
- MLB Stats API for official player and schedule data
- CSV files for fallback/reference data

Keep the application simple and maintainable. Do not introduce additional frameworks, databases, services, or architectural layers unless explicitly requested.

## Repository structure

- `app.py`
  - Streamlit UI
  - filters
  - tables
  - player-detail views
  - presentation logic

- `data_provider.py`
  - MLB Stats API access
  - Statcast / pybaseball access
  - data normalization
  - caching
  - fallback data handling
  - streamer matchup data

- `scoring.py`
  - fantasy scoring algorithms
  - score normalization
  - hitter, pitcher, and streamer scoring weights

- `tests/`
  - automated tests

- `data/`
  - tracked fallback/reference CSV files

- `data/cache/`
  - generated runtime cache
  - must not be committed

## Setup

Install dependencies with:

    pip install -r requirements.txt

Run the application with:

    streamlit run app.py

Run all automated tests with:

    python -m unittest discover -s tests

Always run the relevant tests after changing application logic.

## Critical scoring rules

The scoring models represent intentional fantasy-baseball methodology.

DO NOT change scoring weights, metric direction, normalization methodology, or metric definitions unless the user explicitly asks for a scoring-model change.

Current hitter model:

- wRC+: 0.30
- Barrel%: 0.22
- HardHit%: 0.18
- SB: 0.12
- BB%: 0.10
- K%: 0.08 (lower is better)

Current pitcher model:

- K-BB%: 0.30
- SwStr%: 0.20
- ERA: 0.20 (lower is better)
- WHIP: 0.17 (lower is better)
- HardHit%: 0.13 (lower is better)

Current streamer model:

- K-BB%: 0.24
- SwStr%: 0.16
- Opp wRC+: 0.22 (lower is better)
- Park Factor: 0.13 (lower is better)
- Projected IP: 0.15
- Roster%: 0.10 (lower is better)

If a task does not explicitly concern the scoring methodology, preserve these values exactly.

## Data-source rules

Prefer official MLB Stats API data for statistics MLB directly provides.

Use Statcast / pybaseball for advanced batted-ball and pitch metrics.

Do not silently replace an existing data source with another source.

Network/API failures should degrade gracefully where practical rather than breaking the entire Streamlit application.

Preserve the existing offline/fallback-data behaviour.

## Cache behaviour

Runtime-generated data belongs under `data/cache/`.

Do not commit generated cache files.

The application currently treats cached live data as stale after 12 hours.

Do not change cache lifetime or refresh behaviour unless explicitly requested.

## Streamlit conventions

Keep business/data logic out of `app.py` when it reasonably belongs in `data_provider.py` or `scoring.py`.

Keep UI-specific formatting and interaction logic in `app.py`.

Avoid duplicating calculations between UI and data layers.

Prefer small functions with descriptive names over large blocks of inline Streamlit code.

Maintain the existing wide dashboard layout unless a task specifically changes the design.

## Testing expectations

When modifying scoring behaviour:

    python -m unittest tests.test_scoring

When modifying data retrieval or transformations:

    python -m unittest tests.test_data_provider

Before completing a substantial task:

    python -m unittest discover -s tests

Add or update tests for behavioural changes where practical.

Never weaken or remove tests merely to make a change pass.

## Scope discipline

Make the smallest coherent change that satisfies the requested task.

Do not:

- redesign unrelated screens
- rename unrelated functions
- change scoring methodology without explicit instruction
- perform large refactors as part of a small feature
- add dependencies unless they materially improve the requested feature
- commit secrets or local configuration
- commit files from `data/cache/`

If a larger refactor would be beneficial, explain it separately rather than including it automatically.

## Completion

Before finishing a coding task:

1. Review the diff for unrelated changes.
2. Run relevant tests.
3. Run the full test suite for substantial changes.
4. Summarize what changed.
5. Report which tests were run and whether they passed.
6. Mention any limitations or follow-up work.
