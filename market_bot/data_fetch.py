from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable, List
from urllib.error import URLError
from urllib.request import Request, urlopen
import json

import pandas as pd


@dataclass(frozen=True)
class PriceData:
    symbol: str
    data: pd.DataFrame


def fetch_history(symbols: Iterable[str], start: date, end: date) -> List[PriceData]:
    symbols = [symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()]
    outputs: List[PriceData] = []
    if not symbols:
        return outputs

    for symbol in symbols:
        try:
            frame = _download_symbol(symbol, start=start, end=end)
        except Exception as exc:
            continue
        if frame is None or frame.empty:
            continue
        frame["Symbol"] = symbol
        outputs.append(PriceData(symbol=symbol, data=frame))

    return outputs


def _download_symbol(symbol: str, start: date, end: date) -> pd.DataFrame:
    start_ts = _to_timestamp(start)
    end_ts = _to_timestamp(end) + 24 * 60 * 60

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}"
        f"?period1={start_ts}"
        f"&period2={end_ts}"
        "&interval=1d&events=div,split&includeAdjustedClose=true"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except URLError as exc:
        raise RuntimeError(f"{symbol}: failed to download from Yahoo ({exc.reason})") from exc
    payload_json = json.loads(payload)

    results = payload_json.get("chart", {}).get("result")
    if not results:
        raise RuntimeError(f"{symbol}: missing chart payload from Yahoo")

    item = results[0]
    timestamps = item.get("timestamp", [])
    quote = (item.get("indicators", {}).get("quote") or [])
    if not timestamps or not quote:
        return pd.DataFrame()
    quote = quote[0]

    frame = pd.DataFrame(
        {
            "Date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None),
            "Open": quote.get("open", []),
            "High": quote.get("high", []),
            "Low": quote.get("low", []),
            "Close": quote.get("close", []),
            "Volume": quote.get("volume", []),
        }
    )
    frame = frame.sort_values("Date").reset_index(drop=True)
    return frame[["Date", "Open", "High", "Low", "Close", "Volume"]]

def _to_timestamp(value: date) -> int:
    dt = datetime.combine(value, time.min).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
