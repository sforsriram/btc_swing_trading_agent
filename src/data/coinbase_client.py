# src/data/coinbase_client.py
"""
Coinbase Advanced Trade API client.
- Auth via API key + secret (TRADE-ONLY scope)
- Rate-limit backoff on 429 responses
- Raises typed exceptions for caller error handling
"""
from __future__ import annotations
import time
import uuid
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.core.logging_setup import get_logger

log = get_logger("coinbase_client")

# Coinbase Advanced Trade base URL
BASE_URL = "https://api.coinbase.com"


class CoinbaseAPIError(Exception):
    """Raised when Coinbase returns an error response."""
    def __init__(self, status_code: int, message: str, preview_id: str | None = None):
        self.status_code = status_code
        self.message = message
        self.preview_id = preview_id
        super().__init__(f"CoinbaseAPI {status_code}: {message}")


class RateLimitError(CoinbaseAPIError):
    """Raised on HTTP 429 — caller should back off."""
    pass


class CoinbaseClient:
    """
    Thin wrapper around Coinbase Advanced Trade REST API.
    Uses JWT-less API key auth (legacy key/secret format).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = False,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api-public.sandbox.exchange.coinbase.com" if sandbox else BASE_URL

        # Session with retry on 5xx (NOT on 429 — we handle that manually)
        self._session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)

        log.info("CoinbaseClient initialized", sandbox=sandbox)

    def _headers(self) -> dict[str, str]:
        """Build auth headers for Coinbase Advanced Trade."""
        return {
            "Content-Type": "application/json",
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-PASSPHRASE": "",  # not used in Advanced Trade key auth
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: dict | None = None,
        rate_limit_sleep: float = 0.5,
    ) -> dict[str, Any]:
        """
        Execute authenticated request with manual 429 handling.
        Raises CoinbaseAPIError on non-2xx responses.
        """
        url = f"{self.base_url}{path}"

        try:
            from coinbase.rest import RESTClient  # type: ignore
        except ImportError:
            pass  # fallback to raw requests below

        resp = self._session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=json,
            headers=self._headers(),
            timeout=30,
        )

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", rate_limit_sleep * 2))
            log.warning("Rate limited by Coinbase", retry_after=retry_after)
            time.sleep(retry_after)
            raise RateLimitError(429, "Rate limit exceeded")

        if not resp.ok:
            raise CoinbaseAPIError(resp.status_code, resp.text[:200])

        return resp.json()

    # ---- Public API methods ----

    def get_candles(
        self,
        product_id: str,
        granularity: str,
        start: int,
        end: int,
        rate_limit_sleep: float = 0.5,
    ) -> list[dict]:
        """
        Fetch OHLCV candles for a product.
        Returns max ~300 candles per call — caller must paginate.

        Args:
            product_id: e.g. "BTC-USD"
            granularity: e.g. "ONE_DAY", "FOUR_HOUR", "ONE_WEEK"
            start: Unix timestamp (seconds) for range start
            end:   Unix timestamp (seconds) for range end
            rate_limit_sleep: seconds to sleep between paginated calls

        Returns:
            List of candle dicts with keys: start, low, high, open, close, volume
        """
        try:
            from coinbase.rest import RESTClient  # type: ignore
            client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
            response = client.get_candles(
                product_id=product_id,
                start=start,
                end=end,
                granularity=granularity,
            )
            candles = response.candles if hasattr(response, "candles") else response.get("candles", [])
            time.sleep(rate_limit_sleep)
            return candles
        except ImportError:
            # Fallback to raw REST if SDK not installed
            log.warning("coinbase-advanced-py not found, using raw REST")
            data = self._request(
                "GET",
                f"/api/v3/brokerage/products/{product_id}/candles",
                params={"start": start, "end": end, "granularity": granularity},
                rate_limit_sleep=rate_limit_sleep,
            )
            time.sleep(rate_limit_sleep)
            return data.get("candles", [])

    def get_best_bid_ask(self, product_id: str) -> dict:
        """Get current best bid/ask for a product."""
        try:
            from coinbase.rest import RESTClient  # type: ignore
            client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
            response = client.get_best_bid_ask(product_ids=[product_id])
            return response
        except ImportError:
            return self._request(
                "GET",
                "/api/v3/brokerage/best_bid_ask",
                params={"product_ids": product_id},
            )

    def get_accounts(self) -> list[dict]:
        """Fetch all accounts (to get USD and BTC balances)."""
        try:
            from coinbase.rest import RESTClient  # type: ignore
            client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
            response = client.get_accounts()
            return response.accounts if hasattr(response, "accounts") else response.get("accounts", [])
        except ImportError:
            data = self._request("GET", "/api/v3/brokerage/accounts")
            return data.get("accounts", [])

    def create_order(
        self,
        product_id: str,
        side: str,
        order_type: str,
        size: str,
        limit_price: str | None = None,
        stop_price: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """
        Place an order. Returns order dict from Coinbase.
        client_order_id is our idempotency key.
        """
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        try:
            from coinbase.rest import RESTClient  # type: ignore
            client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)

            if order_type == "market":
                if side == "BUY":
                    response = client.market_order_buy(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        quote_size=size,  # USD amount for buys
                    )
                else:
                    response = client.market_order_sell(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        base_size=size,  # BTC amount for sells
                    )
            elif order_type == "limit":
                if side == "BUY":
                    response = client.limit_order_gtc_buy(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        base_size=size,
                        limit_price=limit_price,
                    )
                else:
                    response = client.limit_order_gtc_sell(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        base_size=size,
                        limit_price=limit_price,
                    )
            else:
                raise ValueError(f"Unsupported order_type: {order_type}")

            return response if isinstance(response, dict) else vars(response)
        except ImportError:
            raise RuntimeError("coinbase-advanced-py required for order placement")

    def get_order(self, order_id: str) -> dict:
        """Fetch order status by order_id."""
        try:
            from coinbase.rest import RESTClient  # type: ignore
            client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
            response = client.get_order(order_id=order_id)
            return response if isinstance(response, dict) else vars(response)
        except ImportError:
            return self._request("GET", f"/api/v3/brokerage/orders/historical/{order_id}")

    def health_check(self) -> bool:
        """Validate API connectivity and credentials. Returns True if healthy."""
        try:
            accounts = self.get_accounts()
            log.info("Coinbase API health check passed", account_count=len(accounts))
            return True
        except Exception as e:
            log.error("Coinbase API health check FAILED", error=str(e))
            return False
