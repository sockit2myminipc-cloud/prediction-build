"""Seed 300 synthetic resolved markets into historical_markets for backtesting."""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines.prediction_markets.storage.db import get_session, init_db
from engines.prediction_markets.storage.models import HistoricalMarket

random.seed(42)

CATEGORIES = ["politics", "crypto", "sports", "finance", "science", "entertainment"]
QUESTIONS = {
    "politics": [
        "Will {leader} win the {year} {country} election?",
        "Will Congress pass the {topic} bill before {month}?",
        "Will {country} hold a snap election before {month} {year}?",
        "Will the {party} party maintain its majority after {month} {year}?",
        "Will {leader} resign before the end of {year}?",
    ],
    "crypto": [
        "Will Bitcoin exceed ${price}k before {month} {year}?",
        "Will Ethereum reach ${price}k before {month} {year}?",
        "Will {coin} be listed on a major exchange by {month}?",
        "Will BTC dominance exceed {pct}% before {month}?",
        "Will there be a major exchange hack in {year}?",
    ],
    "sports": [
        "Will {team} win the {year} championship?",
        "Will {player} score 30+ points in the next game?",
        "Will {team} make the playoffs in {year}?",
        "Will {country} win gold at the next Olympics in {sport}?",
        "Will {team} beat {team2} in the {year} finals?",
    ],
    "finance": [
        "Will the S&P 500 exceed {price} by {month} {year}?",
        "Will the Fed raise rates in {month} {year}?",
        "Will {company} stock exceed ${price} before {month}?",
        "Will inflation drop below {pct}% in {year}?",
        "Will the dollar index exceed {price} before {month} {year}?",
    ],
    "science": [
        "Will {company} launch a crewed Mars mission before {year}?",
        "Will a new AI model surpass GPT-4 on major benchmarks by {month}?",
        "Will {country} achieve nuclear fusion net energy gain by {year}?",
        "Will there be a significant earthquake (>7.0) in {region} before {month}?",
        "Will {company} receive FDA approval for {drug} by {month} {year}?",
    ],
    "entertainment": [
        "Will {movie} gross over ${price}M in its opening weekend?",
        "Will {show} be renewed for another season?",
        "Will {artist} win the Grammy for Best Album in {year}?",
        "Will {movie} win Best Picture at the {year} Oscars?",
        "Will {show} have over {pct}M viewers for its finale?",
    ],
}

LEADERS = ["Biden", "Trump", "Macron", "Sunak", "Scholz", "Meloni", "Modi", "Trudeau"]
COUNTRIES = ["USA", "UK", "France", "Germany", "Canada", "Australia", "Japan", "Brazil"]
PARTIES = ["Democratic", "Republican", "Labour", "Conservative", "CDU", "SPD"]
TEAMS = ["Lakers", "Warriors", "Cowboys", "Patriots", "Yankees", "Dodgers", "Chiefs", "Eagles"]
PLAYERS = ["LeBron", "Curry", "Mahomes", "Jokic", "Tatum", "Durant", "Giannis"]
COMPANIES = ["SpaceX", "Tesla", "Apple", "Google", "Microsoft", "Nvidia", "Amazon"]
COINS = ["SOL", "ADA", "XRP", "DOT", "AVAX", "MATIC", "LINK"]
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
SPORTS = ["swimming", "athletics", "gymnastics", "cycling", "rowing"]
REGIONS = ["California", "Japan", "Turkey", "Indonesia", "Chile"]
MOVIES = ["Avengers 6", "Star Wars XII", "Dune 3", "Fast & Furious 11", "Mission Impossible 8"]
SHOWS = ["Succession", "Game of Thrones", "The Crown", "Stranger Things", "The Bear"]
ARTISTS = ["Taylor Swift", "Beyoncé", "Drake", "Ed Sheeran", "Billie Eilish"]
TOPICS = ["climate", "healthcare", "immigration", "infrastructure", "defense"]
DRUGS = ["Alzheimer drug XZ-100", "cancer treatment BT-200", "diabetes pill GLP-3"]


def _fill_question(template: str) -> str:
    year = random.choice([2024, 2025, 2026])
    price = random.choice([50, 100, 150, 200, 4000, 5000, 6000, 500, 550, 600])
    pct = random.choice([5, 10, 20, 50, 55, 60])
    team2 = random.choice(TEAMS)
    return template.format(
        leader=random.choice(LEADERS),
        year=year,
        country=random.choice(COUNTRIES),
        topic=random.choice(TOPICS),
        month=random.choice(MONTHS),
        party=random.choice(PARTIES),
        price=price,
        coin=random.choice(COINS),
        pct=pct,
        team=random.choice(TEAMS),
        team2=team2,
        player=random.choice(PLAYERS),
        company=random.choice(COMPANIES),
        sport=random.choice(SPORTS),
        region=random.choice(REGIONS),
        movie=random.choice(MOVIES),
        show=random.choice(SHOWS),
        artist=random.choice(ARTISTS),
        drug=random.choice(DRUGS),
    )


def _generate_price_history(
    open_price: float,
    final_price: float,
    start_ts: int,
    end_ts: int,
    n_points: int = 20,
) -> list[dict]:
    """Generate n_points price readings from open_price to final_price with realistic noise."""
    if n_points < 2:
        n_points = 2
    duration = end_ts - start_ts
    if duration <= 0:
        duration = 86400

    history = []
    for i in range(n_points):
        frac = i / (n_points - 1)
        # Interpolated base + random walk noise
        base = open_price + frac * (final_price - open_price)
        noise = random.gauss(0, 0.04)
        p = max(0.01, min(0.99, base + noise))
        t = start_ts + int(frac * duration)
        # Add occasional velocity spike (every ~5th point)
        if i > 0 and i % 5 == 0:
            spike = random.choice([-1, 1]) * random.uniform(0.05, 0.15)
            p = max(0.01, min(0.99, p + spike))
        history.append({"t": t, "p": round(p, 4)})

    # Ensure last point matches final_price closely
    history[-1]["p"] = round(final_price, 4)
    return history


def seed(n: int = 300) -> None:
    init_db()
    session = get_session()
    now = dt.datetime.now(dt.UTC)
    inserted = 0
    skipped = 0

    try:
        for i in range(n):
            category = CATEGORIES[i % len(CATEGORIES)]
            templates = QUESTIONS[category]
            template = templates[i % len(templates)]
            question = _fill_question(template)

            source = "polymarket" if i % 2 == 0 else "kalshi"
            market_id = f"pm_test_{i}" if source == "polymarket" else f"kalshi_test_{i}"

            resolution_bool = random.random() > 0.45  # slight YES bias
            resolution_str = "YES" if resolution_bool else "NO"
            final_price = random.uniform(0.05, 0.95)
            noise = random.uniform(0.05, 0.25) * random.choice([-1, 1])
            open_price = max(0.05, min(0.95, final_price + noise))

            days_ago_start = random.randint(90, 180)
            days_ago_end = random.randint(1, 89)
            start_date = now - dt.timedelta(days=days_ago_start)
            end_date = now - dt.timedelta(days=days_ago_end)

            start_ts = int(start_date.timestamp())
            end_ts = int(end_date.timestamp())

            n_points = random.randint(15, 30)
            price_history = _generate_price_history(
                open_price, final_price, start_ts, end_ts, n_points
            )

            market = HistoricalMarket(
                source=source,
                market_id=market_id,
                question=question,
                category=category,
                resolution=resolution_str,
                open_date=start_date,
                close_date=end_date,
                resolution_date=end_date,
                price_history=json.dumps(price_history),
                created_at=now,
            )
            session.add(market)
            inserted += 1

            if (i + 1) % 50 == 0:
                try:
                    session.flush()
                    print(f"  Flushed {i + 1} markets...")
                except Exception as e:
                    print(f"  Flush error at {i}: {e}")

        session.commit()
        print(f"\nDone. Inserted {inserted} markets ({skipped} skipped).")
    except Exception as exc:
        session.rollback()
        print(f"ERROR: {exc}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print("Seeding 300 synthetic historical markets...")
    seed(300)
