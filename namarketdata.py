import os
import json
import aiohttp
import asyncio
from datetime import datetime

ARSHA_BASE = "https://api.arsha.io/v2/NA"
UTIL_BASE = "https://api.arsha.io/util"
MARKET_CACHE = "na_market_cache.json"
ITEM_DB_FILE = "na_item_db.json"


# ----------------------- Utility -----------------------

def save_json(path, data):
    """Write data to a JSON file (pretty-formatted)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path):
    """Load JSON from file, return empty list/dict if missing or broken."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load {path}: {e}")
        return []


# ----------------------- Core Fetching -----------------------

async def fetch_json(url, params=None):
    """Fetch JSON data asynchronously with aiohttp."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params or {}) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HTTP {resp.status} for {url}\n{text[:200]}")
            return await resp.json()


async def fetch_market_data():
    """Fetch all items currently on the NA Central Market."""
    url = f"{ARSHA_BASE}/market"
    data = await fetch_json(url, params={"lang": "en"})
    save_json(MARKET_CACHE, data)
    print(f"✅ Cached {len(data)} market entries at {datetime.now().strftime('%H:%M:%S')}")
    return data


async def fetch_item_db():
    url = f"{UTIL_BASE}/db"
    data = await fetch_json(url, params={"lang": "en"})
    save_json(ITEM_DB_FILE, data)
    print(f"✅ Saved item DB ({len(data)} entries)")
    return data


async def get_item_price(item_id: int, sub_id: int = 0):
    """Get live price data for a specific item and sub-ID (enhancement level)."""
    url = f"{ARSHA_BASE}/GetMarketPriceInfo"
    params = {"id": item_id, "sid": sub_id, "lang": "en"}
    return await fetch_json(url, params)


# ----------------------- Local Lookup -----------------------

def search_item_by_name(name: str):
    """Search item in local DB by partial name (case-insensitive)."""
    db = load_json(ITEM_DB_FILE)
    name = name.lower()
    matches = [item for item in db if name in item["name"].lower()]
    return matches


# ----------------------- Startup Task -----------------------

async def update_cache():
    """Run both data fetches sequentially and save JSONs."""
    print("🔄 Updating item DB and market cache...")
    await fetch_item_db()
    await fetch_market_data()
    print("✅ All data updated successfully!")


# ----------------------- Script Entry -----------------------

if __name__ == "__main__":
    try:
        asyncio.run(update_cache())
    except Exception as e:
        print(f"❌ Update failed: {e}")

    # Optional: example usage
    matches = search_item_by_name("blackstar")
    for m in matches[:5]:
        print(f"{m['id']} - {m['name']}")

    async def test_price():
        if matches:
            first = matches[0]
            data = await get_item_price(first["id"], 0)
            print("\n💰 Example Price Data:")
            print(json.dumps(data, indent=2))

    asyncio.run(test_price())
