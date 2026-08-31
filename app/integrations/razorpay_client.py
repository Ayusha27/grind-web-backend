# app/integrations/razorpay_client.py
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class RazorpayClient:
    """Thin async wrapper over the Razorpay Orders API.

    One instance per process, created in the app lifespan (Phase 8), so the
    TLS handshake and connection pool are shared across all requests.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.RAZORPAY_BASE_URL,
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            timeout=httpx.Timeout(settings.RAZORPAY_TIMEOUT, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            headers={"Content-Type": "application/json"},
        )

    async def create_order(
        self, *, amount: int, currency: str, receipt: str
    ) -> dict[str, Any]:
        """amount is in the smallest currency unit (paise)."""
        body = {"amount": amount, "currency": currency, "receipt": receipt}

        # Retry ONLY on ConnectError: the request provably never reached
        # Razorpay, so replaying it cannot create a second order. A timeout
        # or a 5xx is NOT retried — Razorpay does not deduplicate on receipt,
        # so a retry there could double-charge intent. Fail loudly instead.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post("/orders", json=body)
                break
            except httpx.ConnectError as exc:
                last_exc = exc
                logger.warning(
                    "razorpay_connect_retry", extra={"attempt": attempt + 1, "receipt": receipt}
                )
        else:
            logger.error("razorpay_unreachable", extra={"receipt": receipt})
            raise ExternalServiceError("Payment gateway unavailable. Please retry.") from last_exc

        if response.status_code >= 400:
            # Razorpay's own error text may contain account detail — log it,
            # never return it (verify_payment.php leaked raw messages).
            logger.error(
                "razorpay_error",
                extra={"status": response.status_code, "body": response.text[:500]},
            )
            raise ExternalServiceError("Could not create payment order.")

        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


razorpay_client = RazorpayClient()
