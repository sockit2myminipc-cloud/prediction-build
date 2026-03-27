from __future__ import annotations

from collections import defaultdict

from loguru import logger
from rapidfuzz import fuzz

EVENT_FINGERPRINTS: dict[str, dict] = {
    "fomc_rate": {"keywords": ["fomc", "fed", "rate cut", "rate hike"], "prefer_platform": "kalshi"},
    "cpi_print": {"keywords": ["cpi", "consumer price", "inflation"], "prefer_platform": "kalshi"},
    "btc_price": {"keywords": ["bitcoin", "btc", "$100k", "$120k"], "prefer_platform": "polymarket"},
    "us_election": {"keywords": ["president", "senate", "election 2024"], "prefer_platform": "polymarket"},
    "recession": {"keywords": ["recession", "gdp contraction"], "prefer_platform": "kalshi"},
}


class MarketDeduplicator:
    @staticmethod
    def _fingerprint_for_question(question: str) -> str | None:
        q = question.lower()
        for fp, spec in EVENT_FINGERPRINTS.items():
            if any(kw in q for kw in spec["keywords"]):
                return fp
        return None

    @staticmethod
    def dedup(markets_list: list[dict]) -> tuple[list[dict], list[dict]]:
        fingerprint_groups: dict[str, list[dict]] = defaultdict(list)
        ungrouped: list[dict] = []

        for m in markets_list:
            fp = MarketDeduplicator._fingerprint_for_question(m.get("question", ""))
            if fp:
                fingerprint_groups[fp].append(m)
            else:
                ungrouped.append(m)

        kept: list[dict] = list(ungrouped)
        dropped: list[dict] = []

        for fp, group in fingerprint_groups.items():
            platforms = {m.get("platform") for m in group}
            if len(platforms) < 2:
                kept.extend(group)
                continue

            prefer = EVENT_FINGERPRINTS[fp]["prefer_platform"]
            preferred = [m for m in group if m.get("platform") == prefer]
            if preferred:
                best = max(preferred, key=lambda x: float(x.get("liquidity") or 0))
                for m in group:
                    if m is not best:
                        dropped.append(
                            {
                                **m,
                                "_dedup_reason": f"duplicate_of_fingerprint:{fp}_keep_{prefer}",
                            }
                        )
                    else:
                        kept.append(m)
            else:
                best = max(group, key=lambda x: float(x.get("liquidity") or 0))
                for m in group:
                    if m is not best:
                        dropped.append({**m, "_dedup_reason": f"duplicate_fingerprint:{fp}_liquidity"})
                    else:
                        kept.append(m)

        clustered_kept, clustered_drop = MarketDeduplicator._fuzz_dedup_cross_platform(kept)
        dropped.extend(clustered_drop)
        if dropped:
            logger.info(
                "dedup dropped {} markets: {}",
                len(dropped),
                [(d.get("platform"), (d.get("question") or "")[:60]) for d in dropped[:20]],
            )
        return clustered_kept, dropped

    @staticmethod
    def _fuzz_dedup_cross_platform(
        markets: list[dict], threshold: int = 70
    ) -> tuple[list[dict], list[dict]]:
        by_platform: dict[str, list[dict]] = defaultdict(list)
        for m in markets:
            by_platform[m.get("platform", "")].append(m)

        pm = by_platform.get("polymarket", [])
        kx = by_platform.get("kalshi", [])
        if not pm or not kx:
            return markets, []

        used_k: set[int] = set()
        extra_drop: list[dict] = []
        final: list[dict] = [m for m in markets if m.get("platform") not in ("polymarket", "kalshi")]

        for im, m in enumerate(pm):
            qm = m.get("question", "")
            best_j = None
            best_score = threshold - 1
            for j, k in enumerate(kx):
                if j in used_k:
                    continue
                sc = fuzz.token_set_ratio(qm.lower(), (k.get("question") or "").lower())
                if sc > best_score:
                    best_score = sc
                    best_j = j
            if best_j is not None and best_score >= threshold:
                km = kx[best_j]
                used_k.add(best_j)
                lm, lk = float(m.get("liquidity") or 0), float(km.get("liquidity") or 0)
                if lk > lm:
                    final.append(km)
                    extra_drop.append({**m, "_dedup_reason": "fuzz_pair_keeper_kalshi"})
                else:
                    final.append(m)
                    extra_drop.append({**km, "_dedup_reason": "fuzz_pair_keeper_polymarket"})
            else:
                final.append(m)

        for j, k in enumerate(kx):
            if j not in used_k:
                final.append(k)

        return final, extra_drop
