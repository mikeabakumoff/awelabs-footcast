# FOOTCAST

A football match forecasting system: collectors that build a dataset from
several public and commercial feeds, a feature builder, an ensemble of
scikit-learn models, a publication gate, and a Flask API that serves the result.

The Telegram Mini App that consumes this API lives in a separate repository:
**[football-app →](https://github.com/mikeabakumoff/football-app)**

This is an engineering write-up of a pipeline, not a tipping service. There are
no accuracy promises here and no claims about returns of any kind.

---

## Ensemble

Five classifiers produce a match result. N5 is an aggregator stacked on the
outputs of the other four rather than another view of the raw features:

| | Model | Algorithm | Reads |
|---|---|---|---|
| N1 | Form | GradientBoostingClassifier | rolling team form |
| N2 | Head-to-head | GradientBoostingClassifier | historical meetings |
| N3 | Market | LogisticRegression | bookmaker-implied probabilities |
| N4 | Combined | GradientBoostingClassifier | the full feature set |
| N5 | **Aggregator** | LogisticRegression | the outputs of N1–N4 |

Two further regressors, N6h and N6a (`GradientBoostingRegressor`), estimate goals
scored by each side. They feed the scoreline model below.

Training set: **17.1K matches** — eight seasons of the English, Spanish, Italian,
French and German top divisions, plus seven seasons of the Champions League and
Europa League. (17.9K are collected; rows where either side has fewer than two
matches played have no meaningful form features and are dropped.)

## Validation

Cross-validation uses **5-fold `TimeSeriesSplit`** — folds respect chronology, so
a model is never validated on matches that precede its training data. Ordinary
k-fold would leak the future into the past and inflate every number here.
Classifiers are scored on accuracy, the goals regressors on mean absolute error.

Measured on 17.1K matches, three-way outcome (home / draw / away):

| | CV accuracy |
|---|---|
| Always predict home — the baseline to beat | 44.0% |
| N1 form | 48.9% ± 1.1 |
| N2 head-to-head | 46.1% ± 2.3 |
| N3 market | 43.8% ± 1.0 |
| **N4 combined** | **52.8% ± 1.3** |

So the full feature set buys about **nine points over always guessing the home
side**. That is the honest size of the effect. Football outcomes are not very
predictable, and a three-way problem with a 44% majority class does not leave
much room — anyone quoting 60%+ on this task is measuring something else.

Two of these deserve comment. **N3 lands below the baseline**, which is not a
broken model: bookmaker odds are sharp, but three raw prices carry almost no
information about draws, and the model pays for that in accuracy. It earns its
place in the ensemble by being *decorrelated* from the form-based models, not by
standing alone. And **N2 has the widest spread of the four** — head-to-head
records thin out badly for newly promoted clubs and for cup ties between sides
that have rarely met.

### A caveat about the aggregator

N5's cross-validation figure is **not** listed above, and deliberately so. Its
input matrix is assembled from `predict_proba` of N1–N4 *after those models have
been fit on the whole training set* — so by the time N5 is validated, its
features have already seen the answers in every fold. The number that procedure
produces is roughly ten points higher than N4's, and that gap is the leak, not
the stacking.

Doing this properly means generating the stack features out-of-fold. Until that
is fixed, **N4's 52.8% is the number to trust**, and it is the one quoted here.

Everything above is cross-validation on historical data. It is not a prediction
of future performance, and it is not a claim about returns of any kind.

## Features

Built by `build_features.py` from the collected raw matches:

- **Form** — points per match, win/draw/loss rates, goals for and against, goal
  difference, shots on target, and a recency-weighted form score
- **Head-to-head** — historical record and goal averages between the two clubs
- **Motivation** — table position, distance to the title race and to the
  relegation zone, days of rest since the last fixture, separate home and away
  win rates
- **UEFA club coefficients** — and the difference between the two sides
- **Expected goals (xG)** — rolling averages
- **Availability** — injuries and suspensions

## Scoreline and first goal

Goals for each side are modelled as independent Poisson processes with rates
λ_home and λ_away taken from the goals regressors. The probability of any exact
scoreline follows from the pair, which also yields an estimate of when the first
goal falls and which side scores it.

## The publication gate

Odds are aggregated across roughly **18 bookmakers** per fixture. The final
probability is a weighted blend in which the ensemble carries the larger share
and the market the rest — so the market can pull a forecast back, but cannot
originate one.

A forecast is published only when:

- ensemble confidence is **≥60%**, confirmed by the odds
- **away favourites clear a stricter 65%** — a side the model favours away from
  home but the market does not is the shape that fails most often

Fixtures below the threshold are still **listed, without a forecast**. Hiding
them would quietly flatter the hit rate, and the absence of a forecast is itself
information.

## Pipeline

```
collectors ──▶ SQLite ──▶ build_features ──▶ train_models ──▶ run_bot ──▶ Telegram
                  │                                             │
                  └──────────────▶ api/api_server ──▶ Mini App  │
                                                                 ▼
                                                         result_watcher
```

`scheduler.py` sequences the runs. `result_watcher.py` reconciles each published
forecast against the final score, which is where the accuracy counter in the
Mini App comes from. `migrate_db.py` handles schema changes.

An LLM step in `agent_collector.py` reads match news and adjusts the forecast
where something material — a manager change, a late fitness update — has not
reached the structured feeds yet.

## Layout

| | |
|---|---|
| `collect_*.py`, `live_collector.py` | feed collectors |
| `agent_collector.py` | news-based adjustment |
| `build_features.py` | raw matches → model inputs |
| `train_models.py` | trains and saves the ensemble |
| `run_bot.py` | forecast generation, gate, publication |
| `scheduler.py` | run sequencing |
| `result_watcher.py` | reconciles forecasts with results |
| `api/api_server.py` | Flask API the Mini App consumes |
| `sample/` | database schema and a small sample |

## What is not in this repository, and why

**The database.** The collected dataset is assembled from several feeds, some of
them commercial services whose terms cover redistribution of the data they
supply. Rather than reason about where the line falls for each one, the dataset
is simply not published.

What is here instead: **`sample/schema.sql`** with the full DDL for all fifteen
tables, and **`sample/sample.db`** with twenty matches of raw and feature rows so
the shape is inspectable. The collectors rebuild the rest from scratch against
your own API keys.

**Trained models.** The `.pkl` files are not published either — they are trained
locally from the pipeline. `train_models.py` reproduces them from a populated
database, which is the honest artefact anyway: a pickle tells you nothing about
how it was made, and unpickling a binary from the internet is a habit worth not
encouraging.

## Data sources

| Source | Used for |
|---|---|
| api-sports.io | fixtures, lineups, injuries, standings |
| the-odds-api.com | bookmaker odds |
| understat.com | expected goals |
| football-data.co.uk | historical results |
| ESPN | events and supplementary data |

The first two are commercial APIs and need your own keys. Check each service's
current terms before running collectors at volume.

## Running it

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
cp env.example env        # then fill it in
venv/bin/python setup.py  # initial collection
venv/bin/python build_features.py
venv/bin/python train_models.py
```

Configuration is a plain `KEY=VALUE` file named `env` in the project root — see
`env.example` for the seven values it reads. No key appears in code.

For the API the Mini App talks to:

```bash
venv/bin/python api/api_server.py
```

## Deployment shape

systemd units for the bot and the API, with a timer driving the collection and
forecast schedule.

## Stack

Python, scikit-learn, pandas, NumPy, Flask, SQLite, python-telegram-bot,
Anthropic API.

---

*This is a sanitized copy. Collected data, trained models, credentials and logs
are excluded by design.*
