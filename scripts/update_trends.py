from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime
from difflib import SequenceMatcher
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EggyTrendStudio/3.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

DEFAULT_TOPIC_ALIASES = {
    "哈利波特": ["哈利波特", "harrypotter", "霍格沃茨", "格兰芬多", "斯莱特林", "赫奇帕奇", "拉文克劳"],
    "火影忍者": ["火影忍者", "火影", "鸣人", "佐助", "宇智波", "鼬"],
    "王者荣耀": ["王者荣耀", "王者万象棋"],
    "蛋仔派对": ["蛋仔派对", "蛋仔", "蛋仔岛"],
}


def clean_title(value: Any) -> str:
    if value is None or isinstance(value, float):
        return ""
    return " ".join(str(value).split()).strip()


def normalize_title(value: str) -> str:
    value = clean_title(value).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def load_topic_aliases() -> dict[str, list[str]]:
    aliases = {k: list(v) for k, v in DEFAULT_TOPIC_ALIASES.items()}
    path = DATA_DIR / "topic_aliases.json"
    if path.exists():
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(extra, dict):
                for canonical, values in extra.items():
                    if isinstance(values, list):
                        aliases.setdefault(str(canonical), [])
                        aliases[str(canonical)].extend(str(v) for v in values if v)
        except Exception as exc:
            print(f"[warn] could not read {path}: {exc}")
    return aliases


def alias_topic(title: str, aliases: dict[str, list[str]]) -> str | None:
    normalized = normalize_title(title)
    for canonical, values in aliases.items():
        for value in [canonical, *values]:
            nv = normalize_title(value)
            if nv and nv in normalized:
                return canonical
    return None


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if min(len(na), len(nb)) >= 6 and (na in nb or nb in na):
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


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
                items.append({
                    "source_id": source_id,
                    "source": source_name,
                    "rank": len(items) + 1,
                    "title": title,
                    "url": raw.get("url") or "",
                    "mobile_url": raw.get("mobileUrl") or "",
                })
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
        copied.setdefault("observations", 1)
        merged[(source_id, title)] = copied
    for item in fresh:
        key = (item["source_id"], item["title"])
        if key in merged:
            old = merged[key]
            old["rank"] = min(int(old.get("rank") or 999999), int(item["rank"]))
            old["url"] = item.get("url") or old.get("url") or ""
            old["mobile_url"] = item.get("mobile_url") or old.get("mobile_url") or ""
            old["last_seen"] = observed_at
            old["observations"] = int(old.get("observations") or 1) + 1
            continue
        stable_id = hashlib.sha1(f"{date_str}|{item['source_id']}|{item['title']}".encode("utf-8")).hexdigest()[:12]
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
            "observations": 1,
        }
    source_order = {source_id: i for i, (source_id, _) in enumerate(SOURCES)}
    result = list(merged.values())
    result.sort(key=lambda x: (source_order.get(str(x.get("source_id")), 999), int(x.get("rank") or 999999), str(x.get("title") or "")))
    return result


def collect(date_str: str) -> dict:
    observed_at = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    fresh_items: list[dict] = []
    successful_sources: list[str] = []
    failed_sources: list[dict] = []
    for source_id, source_name in SOURCES:
        try:
            source_items = fetch_source(source_id, source_name)
            fresh_items.extend(source_items)
            successful_sources.append(source_name)
        except Exception as exc:
            failed_sources.append({"source_id": source_id, "source": source_name, "error": str(exc)})
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
        "note": "每日自动热榜抓取；同日重复运行合并并保留观测次数。跨日历史由 data/history.json 汇总。",
        "items": merged_items,
    }


def read_archives() -> list[dict]:
    archives: list[dict] = []
    if not ARCHIVE_DIR.exists():
        return archives
    for path in sorted(ARCHIVE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                archives.append(data)
        except Exception as exc:
            print(f"[warn] bad archive {path}: {exc}")
    return archives


def current_streak(dates: list[str]) -> int:
    if not dates:
        return 0
    ds = sorted(set(dates))
    streak = 1
    for i in range(len(ds) - 1, 0, -1):
        if (date.fromisoformat(ds[i]) - date.fromisoformat(ds[i - 1])).days == 1:
            streak += 1
        else:
            break
    return streak


def build_history() -> dict:
    archives = read_archives()
    aliases = load_topic_aliases()
    clusters: list[dict] = []
    for archive in archives:
        date_str = str(archive.get("date") or "")
        if not date_str:
            continue
        for item in archive.get("items", []):
            title = clean_title(item.get("title"))
            if not title:
                continue
            canonical_alias = alias_topic(title, aliases)
            chosen: dict | None = None
            if canonical_alias:
                chosen = next((c for c in clusters if c.get("canonical_alias") == canonical_alias), None)
                if chosen is None:
                    chosen = {
                        "topic_id": "topic-" + hashlib.sha1(canonical_alias.encode("utf-8")).hexdigest()[:12],
                        "canonical_title": canonical_alias,
                        "canonical_alias": canonical_alias,
                        "match_mode": "alias+fuzzy",
                        "entries": [],
                    }
                    clusters.append(chosen)
            else:
                best_score = 0.0
                best_cluster: dict | None = None
                for cluster in clusters:
                    if cluster.get("canonical_alias"):
                        continue
                    score = max([title_similarity(title, e["title"]) for e in cluster["entries"][-12:]] or [0.0])
                    if score > best_score:
                        best_score, best_cluster = score, cluster
                if best_score >= 0.82 and best_cluster is not None:
                    chosen = best_cluster
                else:
                    chosen = {
                        "topic_id": "topic-" + hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:12],
                        "canonical_title": title,
                        "canonical_alias": None,
                        "match_mode": "fuzzy",
                        "entries": [],
                    }
                    clusters.append(chosen)
            chosen["entries"].append({
                "date": date_str,
                "source": str(item.get("source") or ""),
                "rank": int(item.get("rank") or 999999),
                "title": title,
                "item_id": item.get("id"),
            })

    newest_date = max((str(a.get("date") or "") for a in archives), default="")
    topics: list[dict] = []
    item_topic: dict[str, str] = {}
    for cluster in clusters:
        entries = cluster["entries"]
        dates = sorted({e["date"] for e in entries})
        if not dates:
            continue
        first_date, last_date = dates[0], dates[-1]
        by_date: dict[str, dict] = {}
        for e in entries:
            if e.get("item_id"):
                item_topic[str(e["item_id"])] = cluster["topic_id"]
            bucket = by_date.setdefault(e["date"], {"date": e["date"], "titles": [], "platforms": [], "best_rank": 999999})
            if e["title"] not in bucket["titles"]:
                bucket["titles"].append(e["title"])
            if e["source"] and e["source"] not in bucket["platforms"]:
                bucket["platforms"].append(e["source"])
            bucket["best_rank"] = min(bucket["best_rank"], e["rank"])
        streak = current_streak(dates)
        days_seen = len(dates)
        active = last_date == newest_date
        if active and streak >= 4:
            state = "长期持续"
        elif active and streak >= 2:
            state = "持续升温"
        elif active and days_seen >= 2:
            state = "回潮"
        elif active:
            state = "新出现"
        else:
            state = "已退潮"
        latest_titles: list[str] = []
        for e in sorted(entries, key=lambda x: (x["date"], -x["rank"]), reverse=True):
            if e["title"] not in latest_titles:
                latest_titles.append(e["title"])
            if len(latest_titles) >= 5:
                break
        topics.append({
            "topic_id": cluster["topic_id"],
            "canonical_title": cluster["canonical_title"],
            "match_mode": cluster["match_mode"],
            "state": state,
            "active": active,
            "first_seen_date": first_date,
            "last_seen_date": last_date,
            "calendar_span_days": (date.fromisoformat(last_date) - date.fromisoformat(first_date)).days + 1,
            "days_seen": days_seen,
            "current_streak_days": streak if active else 0,
            "total_observations": len(entries),
            "platform_count": len({e["source"] for e in entries if e["source"]}),
            "platforms": sorted({e["source"] for e in entries if e["source"]}),
            "best_rank": min(e["rank"] for e in entries),
            "latest_titles": latest_titles,
            "timeline": [by_date[d] for d in sorted(by_date)],
        })
    topics.sort(key=lambda x: (0 if x["active"] else 1, -x["current_streak_days"], -x["days_seen"], -x["platform_count"], x["best_rank"], x["canonical_title"]))
    return {
        "generated_at": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "latest_archive_date": newest_date,
        "archive_days": len(archives),
        "topic_count": len(topics),
        "active_topic_count": sum(1 for t in topics if t["active"]),
        "persistent_topic_count": sum(1 for t in topics if t["active"] and t["days_seen"] >= 2),
        "method": {
            "stage_1": "别名实体聚类：同一 IP/主题的不同叫法合并。",
            "stage_2": "保守标题近似聚类：相似度 >= 0.82 才合并，避免误合并。",
            "ranking": "活跃主题优先；连续存续天数 > 累计出现天数 > 跨平台数 > 历史最佳排名。",
        },
        "item_topic": item_topic,
        "topics": topics,
    }


def attach_history_to_latest(data: dict, history: dict) -> dict:
    topic_by_id = {t["topic_id"]: t for t in history.get("topics", [])}
    item_topic = history.get("item_topic", {})
    for item in data.get("items", []):
        topic = topic_by_id.get(item_topic.get(str(item.get("id"))))
        if not topic:
            continue
        item["topic_id"] = topic["topic_id"]
        item["topic_title"] = topic["canonical_title"]
        item["topic_state"] = topic["state"]
        item["days_seen"] = topic["days_seen"]
        item["current_streak_days"] = topic["current_streak_days"]
        item["platform_count"] = topic["platform_count"]
        item["topic_first_seen_date"] = topic["first_seen_date"]
    return data


def main() -> None:
    target = datetime.now(TIMEZONE).date().isoformat()
    data = collect(target)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (ARCHIVE_DIR / f"{target}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    history = build_history()
    data = attach_history_to_latest(data, history)
    (DATA_DIR / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DATA_DIR / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {target}: {data['count']} items; history_topics={history['topic_count']}; persistent={history['persistent_topic_count']}")


if __name__ == "__main__":
    main()
