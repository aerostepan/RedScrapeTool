"""Thin CDP abstraction used only by browser fallback code."""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


class CDPClient:
    """Wrap Chrome DevTools Protocol calls behind a small interface."""

    def __init__(self, driver: Any | None = None) -> None:
        self.driver = driver

    def attach(self, driver: Any) -> None:
        self.driver = driver

    def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.driver is None:
            raise RuntimeError("No browser driver attached to CDP client")
        if hasattr(self.driver, "execute_cdp_cmd"):
            return self.driver.execute_cdp_cmd(method, params or {})
        raise RuntimeError("Attached driver does not expose execute_cdp_cmd")

    def inspect_page_state(self) -> dict[str, Any]:
        """Return a small amount of page state without changing the page."""

        try:
            document = self.command("Runtime.evaluate", {"expression": "document.title"})
            location = self.command("Runtime.evaluate", {"expression": "location.href"})
            return {"title": document, "location": location}
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("CDP page inspection failed: %s", exc)
            return {"error": str(exc)}

