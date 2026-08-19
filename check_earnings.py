"""
earnings-bot: check_earnings.py

For every ticker in tickers.json, checks whether a new quarterly report
has come out since the last time we checked (tracked in posted.json).
If a new quarter is found, builds a two-panel chart (revenue on top,
EPS on bottom — gray bars for history, red bar + red quarter label for
the quarter that just reported) and posts it to X.

Run on a schedule by .github/workflows/check-earnings.yml — you should
not normally need to run this by hand, except to test.

Required environment variables (set as GitHub Actions secrets):
    FMP_API_KEY        - Financial Modeling Prep API key (free tier works)
    X_API_KEY          - X API consumer key
    X_API_SECRET       - X API consumer secret
    X_ACCESS_TOKEN     - X access token (for your account)
    X_ACCESS_SECRET    - X access token secret
"""

import os
import json
import time
from datetime import datetime

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import tweepy

FMP_API_KEY = os.environ["FMP_API_KEY"]
X_API_KEY = os.environ["X_API_KEY"]
X_API_SECRET = os.environ["X_API_SECRET"]
X_ACCESS_TOKEN = os.environ["X_ACCESS_TOKEN"]
X_ACCESS_SECRET = os.environ["X_ACCESS_SECRET"]

TICKERS_FILE = "tickers.json"
POSTED_FILE = "posted.json"
QUARTERS_SHOWN = 6  # how many quarters of history to chart, including the new one

GRAY = "#9a9a9a"
RED = "#dc2626"


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_quarterly_financials(ticker, limit=QUARTERS_SHOWN + 1):
    """
    Pulls the most recent quarterly income-statement rows from Financial
    Modeling Prep. NOTE: FMP occasionally tweaks field names between plan
    tiers/versions — if this starts returning empty revenue/eps, print
    a raw response once and adjust the field names below.
    """
    url = (
        "https://financialmodelingprep.com/stable/income-statement"
        f"?symbol={ticker}&period=quarter&limit={limit}&apikey={FMP_API_KEY}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected FMP response for {ticker}: {data}")
    data.sort(key=lambda row: row["date"])  # oldest -> newest
    return data


def quarter_label(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    q = (dt.month - 1) // 3 + 1
    return f"Q{q}'{str(dt.year)[2:]}"


def make_chart(ticker, financials, out_path):
    labels = [quarter_label(row["date"]) for row in financials]
    revenue_b = [row["revenue"] / 1e9 for row in financials]
    eps = [row.get("epsdiluted") or row.get("eps") or 0 for row in financials]

    colors = [GRAY] * (len(labels) - 1) + [RED]

    fig, (ax_rev, ax_eps) = plt.subplots(2, 1, figsize=(8, 8), dpi=200)
    fig.suptitle(f"${ticker} — Quarterly Results", fontsize=16, fontweight="bold")

    ax_rev.bar(labels, revenue_b, color=colors)
    ax_rev.set_title("Revenue ($B)", fontsize=12, loc="left")
    ax_rev.spines[["top", "right"]].set_visible(False)

    ax_eps.bar(labels, eps, color=colors)
    ax_eps.set_title("EPS ($)", fontsize=12, loc="left")
    ax_eps.spines[["top", "right"]].set_visible(False)

    # color + bold the x-axis label for the just-reported quarter only
    for ax in (ax_rev, ax_eps):
        for i, tick in enumerate(ax.get_xticklabels()):
            if i == len(labels) - 1:
                tick.set_color(RED)
                tick.set_fontweight("bold")

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(out_path)
    plt.close(fig)


def post_to_x(ticker, financials, image_path):
    latest = financials[-1]
    rev_b = latest["revenue"] / 1e9
    eps = latest.get("epsdiluted") or latest.get("eps") or 0
    qlabel = quarter_label(latest["date"])

    text = (
        f"${ticker} just reported {qlabel} earnings\n\n"
        f"Revenue: ${rev_b:.2f}B\n"
        f"EPS: ${eps:.2f}"
    )

    auth = tweepy.OAuth1UserHandler(
        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
    )
    api_v1 = tweepy.API(auth)  # media upload still goes through v1.1
    media = api_v1.media_upload(image_path)

    client = tweepy.Client(
        consumer_key=X_API_KEY,
        consumer_secret=X_API_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_SECRET,
    )
    client.create_tweet(text=text, media_ids=[media.media_id])


def main():
    tickers = load_json(TICKERS_FILE, [])
    posted = load_json(POSTED_FILE, {})

    for raw_ticker in tickers:
        ticker = raw_ticker.upper().strip()
        try:
            financials = get_quarterly_financials(ticker)
        except Exception as e:
            print(f"[{ticker}] fetch error: {e}")
            continue

        if not financials:
            print(f"[{ticker}] no data returned")
            continue

        latest_date = financials[-1]["date"]

        if posted.get(ticker) == latest_date:
            continue  # nothing new since last check

        print(f"[{ticker}] new quarter detected: {quarter_label(latest_date)}")

        img_path = f"/tmp/{ticker}_earnings.png"
        window = financials[-QUARTERS_SHOWN:]
        make_chart(ticker, window, img_path)

        try:
            post_to_x(ticker, window, img_path)
            print(f"[{ticker}] posted to X")
        except Exception as e:
            print(f"[{ticker}] post error: {e}")
            continue  # don't mark as posted if the post failed

        posted[ticker] = latest_date
        time.sleep(2)

    save_json(POSTED_FILE, posted)


if __name__ == "__main__":
    main()
