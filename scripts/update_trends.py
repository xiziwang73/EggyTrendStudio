from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
TIMEZONE = ZoneInfo("Asia/Shanghai")
API_BASE = os.getenv("NEWSNOW_API_BASE", "https://newsnow.busiyi.world/api/s").rstrip("?")

SOURCES = [
    ("toutiao", "今日头条"),
    ("baidu", "百度热搜"),
    ("wallstreetcn-hot", "华尔街见闻"),
    ("thepaper", "澎湃新闻"),
    ("bilibili-hot-search", "B站热搜"),
    ("cls-hot", "财联社热门"),
    ("ifeng", "凤凰网"),
    ("tieba", "贴吧"),
    ("weibo", "微博"),
    ("douyin", "抖音"),
    ("zhihu", "知乎"),
]

NAME_TO_ID = {name: source_id for source_id, name in SOURCES}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EggyTrendStudio/2.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}


def clean_title(value: Any) -> str:
    if value is None or isinstance(value, float):
        return ""
    return " ".join(str(value).split()).strip()


def fetch_source(source_id: str, source_name: str, max_retries: int = 3) -> list[dict]:
    url = f"{API_BASE}?id={quote(source_id)}&latest"
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status")
            if status not in {"success", "cache"}:
                raise RuntimeError(f"unexpected API status: {status!r}")

            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise RuntimeError("API response has no items list")

            items: list[dict] = []
            seen_titles: set[str] = set()
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                title = clean_title(raw.get("title"))
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                items.append(
                    {
                        "source_id": source_id,
                        "source": source_name,
                        "rank": len(items) + 1,
                        "title": title,
                        "url": raw.get("url") or "",
                        "mobile_url": raw.get("mobileUrl") or "",
                    }
                )

            if not items:
                raise RuntimeError("source returned zero valid trend items")

            print(f"[ok] {source_name}: {len(items)} items ({status})")
            return items
        except Exception as exc:
            last_error = exc
            print(f"[warn] {source_name} attempt {attempt}/{max_retries} failed: {exc}")
            if attempt < max_retries:
                time.sleep(attempt * 2)

    raise RuntimeError(f"{source_name} failed after {max_retries} attempts: {last_error}")


def load_existing_archive(date_str: str) -> dict | None:
    path = ARCHIVE_DIR / f"{date_str}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[warn] could not read existing archive {path}: {exc}")
        return None


def merge_items(existing: list[dict], fresh: list[dict], date_str: str, observed_at: str) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}

    for item in existing:
        source_id = str(item.get("source_id") or NAME_TO_ID.get(str(item.get("source") or "")) or item.get("source") or "")
        title = clean_title(item.get("title"))
        if not source_id or not title:
            continue
        copied = dict(item)
        copied.setdefault("first_seen", copied.get("generated_at") or observed_at)
        copied["last_seen"] = copied.get("last_seen") or copied["first_seen"]
        merged[(source_id, title)] = copied

    for item in fresh:
        key = (item["source_id"], item["title"])
        if key in merged:
            old = merged[key]
            old_rank = int(old.get("rank") or 999999)
            old["rank"] = min(old_rank, int(item["rank"]))
            old["url"] = item.get("url") or old.get("url") or ""
            old["mobile_url"] = item.get("mobile_url") or old.get("mobile_url") or ""
            old["last_seen"] = observed_at
            continue

        stable_id = hashlib.sha1(
            f"{date_str}|{item['source_id']}|{item['title']}".encode("utf-8")
        ).hexdigest()[:12]
        merged[key] = {
            "id": f"{date_str}-{item['source_id']}-{stable_id}",
            "source_id": item["source_id"],
            "source": item["source"],
            "rank": item["rank"],
            "title": item["title"],
            "url": item.get("url") or "",
            "mobile_url": item.get("mobile_url") or "",
            "date": date_str,
            "status": "pending",
            "type": "原始热搜",
            "first_seen": observed_at,
            "last_seen": observed_at,
        }

    source_order = {source_id: i for i, (source_id, _) in enumerate(SOURCES)}
    result = list(merged.values())
    result.sort(
        key=lambda x: (
            source_order.get(str(x.get("source_id")), 999),
            int(x.get("rank") or 999999),
            str(x.get("title") or ""),
        )
    )
    return result


def collect(date_str: str) -> dict:
    now = datetime.now(TIMEZONE)
    observed_at = now.isoformat(timespec="seconds")
    fresh_items: list[dict] = []
    successful_sources: list[str] = []
    failed_sources: list[dict] = []

    for source_id, source_name in SOURCES:
        try:
            source_items = fetch_source(source_id, source_name)
            fresh_items.extend(source_items)
            successful_sources.append(source_name)
        except Exception as exc:
            failed_sources.append(
                {"source_id": source_id, "source": source_name, "error": str(exc)}
            )
            print(f"[error] {source_name}: {exc}")

    if not fresh_items:
        raise RuntimeError("all trend sources failed; refusing to overwrite existing data")

    existing = load_existing_archive(date_str) or {}
    existing_items = existing.get("items") if isinstance(existing.get("items"), list) else []
    merged_items = merge_items(existing_items, fresh_items, date_str, observed_at)

    return {
        "date": date_str,
        "generated_at": observed_at,
        "count": len(merged_items),
        "fresh_count": len(fresh_items),
        "source_count": len(successful_sources),
        "sources": successful_sources,
        "failed_sources": failed_sources,
        "source_api": API_BASE,
        "note": "每日自动热榜抓取。采集阶段不做主观筛选；同日重复运行会合并热点并保留当日观测历史。",
        "items": merged_items,
    }


def main() -> None:
    now = datetime.now(TIMEZONE)
    target = now.date().isoformat()
    data = collect(target)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "latest.json").write_text(text, encoding="utf-8")
    (ARCHIVE_DIR / f"{target}.json").write_text(text, encoding="utf-8")
    print(
        f"updated {target}: {data['count']} unique items; "
        f"fresh={data['fresh_count']}; sources={data['source_count']}/{len(SOURCES)}; "
        f"failed={len(data['failed_sources'])}"
    )


if __name__ == "__main__":
    main()
