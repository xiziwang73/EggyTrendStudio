from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
PLATFORMS = {
    "微博热搜": "微博",
    "抖音热搜": "抖音",
    "百度热搜": "百度",
    "知乎热榜": "知乎",
    "B站热搜": "B站",
}


def clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^\d+\.?\s*", "", text)
    text = re.sub(r"\s*(?:热度|热搜值|热搜指数)\s*[\d.]+万.*$", "", text)
    text = re.sub(r"\s*[\d.]+万热度.*$", "", text)
    return text.strip()


def collect(date_str: str) -> dict:
    url = f"https://noobclaw.com/cn/hot-topics/{date_str}/"
    r = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 EggyTrendStudio/1.0"},
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    seen = set()

    for heading_name, source in PLATFORMS.items():
        h2 = next((h for h in soup.find_all("h2") if heading_name in h.get_text(" ", strip=True)), None)
        if not h2:
            continue

        block_texts = []
        node = h2.find_next_sibling()
        while node and getattr(node, "name", None) != "h2":
            if getattr(node, "name", None):
                for li in node.find_all("li") if node.name != "li" else [node]:
                    txt = li.get_text(" ", strip=True)
                    if txt:
                        block_texts.append(txt)
            node = node.find_next_sibling()

        rank = 0
        for raw in block_texts:
            title = clean_title(raw)
            if not title or title in seen:
                continue
            if len(title) < 2:
                continue
            rank += 1
            seen.add(title)
            items.append({
                "id": f"{date_str}-{source}-{rank}",
                "source": source,
                "rank": rank,
                "title": title,
                "date": date_str,
                "status": "pending",
                "type": "原始热搜",
            })
            if rank >= 20:
                break

    if not items:
        raise RuntimeError(f"No trend items parsed from {url}")

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return {
        "date": date_str,
        "generated_at": now.isoformat(timespec="seconds"),
        "count": len(items),
        "sources": sorted({x["source"] for x in items}, key=lambda x: ["微博", "抖音", "百度", "知乎", "B站"].index(x) if x in ["微博", "抖音", "百度", "知乎", "B站"] else 99),
        "source_url": url,
        "note": "每日热榜自动抓取；原始热点全量进入人工审核，不在采集阶段做主观删选。",
        "items": items,
    }


def main() -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    target = (now - timedelta(days=1)).date().isoformat()
    data = collect(target)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "latest.json").write_text(text, encoding="utf-8")
    (ARCHIVE_DIR / f"{target}.json").write_text(text, encoding="utf-8")
    print(f"updated {target}: {data['count']} items from {', '.join(data['sources'])}")


if __name__ == "__main__":
    main()
