"""Phase 6: Telegram / email alerts (stub)."""

from loguru import logger


class PMAlertService:
    def send_opportunity_alert(self, opportunity: dict) -> None:
        logger.debug("alert stub opportunity {}", opportunity.get("rank"))

    def send_news_lag_alert(self, news_lag: dict) -> None:
        logger.debug("alert stub news_lag {}", news_lag.get("question", "")[:60])

    def send_daily_digest(self) -> None:
        logger.debug("alert stub daily digest")
