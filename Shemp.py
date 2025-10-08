import json
from datetime import datetime, timedelta, timezone
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import discord
from discord import app_commands
from discord.ext import tasks
import requests
import pytz
import threading
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum
import os

# ─── CONFIG ──────────────────────────────────────────────────────────────
DISCORD_TOKEN = "DISCORD_TOKEN"
POLL_INTERVAL = 15
ALERT_LEAD_MINUTES = [60, 30, 5]

DATA_FILE = "alerts_sent.json"
CONFIG_FILE = "guild_config.json"
ALERT_MSG_FILE = "last_alerts.json"
PATCH_CONFIG_FILE = "patch_config.json"
LAST_PATCH_FILE = "last_patch.json"
PATCH_URL = "https://www.naeu.playblackdesert.com/en-US/News/Notice?boardType=2"  # Patch Notes board
# ─── Constants ───────────────────────────────────────────
TAX_RATE_NORMAL = 0.35   # No value pack
TAX_RATE_VP = 0.155       # With value pack (adjust as needed)


BOSS_NAMES = [
    "Garmoth", "Karanda", "Kutum", "Kzarka", "Muraka",
    "Nouver", "Offin", "Quint", "Vell", "Golden Pig King",
    "Bulgasal", "Uturi", "Sangoon",
]
# ─────────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.guild_messages = True




# ─── Load/Save JSON ───────────────────────────────────────────────
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ─── Scraper ──────────────────────────────────────────────────────
async def fetch_latest_patch():
    """
    Scrapes the latest patch note from the official site.
    Returns (title, url) or None if failed.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(PATCH_URL) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    first_post = soup.select_one(".news_list li a")
    if not first_post:
        return None

    title = first_post.select_one(".tit").get_text(strip=True)
    url = "https://www.naeu.playblackdesert.com" + first_post["href"]
    return (title, url)

# ─── Patch Notes Loop ─────────────────────────────────────────────
@tasks.loop(minutes=30)
async def patch_notes_check(bot):
    config = load_json(PATCH_CONFIG_FILE)
    last_patch = load_json(LAST_PATCH_FILE)
    last_title = last_patch.get("last_title")

    result = await fetch_latest_patch()
    if not result:
        return

    title, url = result
    if title == last_title:
        return  # already posted

    for guild_id, channel_id in config.items():
        channel = bot.get_channel(channel_id)
        if not channel:
            continue
        try:
            embed = discord.Embed(
                title=f"📰 New Patch Notes: {title}",
                url=url,
                description=f"[Read full patch notes here]({url})",
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Black Desert Online Patch Notes")
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ Failed to send patch notes to guild {guild_id}: {e}")

    save_json(LAST_PATCH_FILE, {"last_title": title})


# ─── Init ─────────────────────────────────────────────────────────
def setup_patch_commands(bot):
    if not patch_notes_check.is_running():
        patch_notes_check.start(bot)


# ─── Boss Scraper ─────────────────────────────────────────────────────────
class BossScraper:
    def __init__(self, server="NA"):
        self.server = server
        self.url = "https://mmotimer.com/bdo/?server=na"
        self.data = []

    def scrape(self):
        content = requests.get(self.url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Connection": "keep-alive",
        }).content

        soup = BeautifulSoup(content, 'html.parser')
        table = soup.find('table', class_='main-table')
        if not table:
            return []

        thead = table.find('thead')
        time_headers = [th.text.strip() for th in thead.find_all('th')][1:]

        self.data = []
        tbody = table.find('tbody')
        for row in tbody.find_all('tr'):
            cells = row.find_all(['th', 'td'])
            day = cells[0].text.strip()

            for i, cell in enumerate(cells[1:]):
                time_str = time_headers[i]
                if cell.text.strip() == "-":
                    continue
                bosses = [span.text.strip() for span in cell.find_all('span')]
                if bosses:
                    for boss_name in bosses:
                        self.data.append({
                            "name": boss_name,
                            "time_str": f"{day} {time_str}"
                        })
        return self.data

async def fetch_bosses(server="NA"):
    loop = asyncio.get_running_loop()
    scraper = BossScraper(server)
    return await loop.run_in_executor(None, scraper.scrape)


# ==============================
# ENUMS & CONFIG
# ==============================

class MarketRegion(Enum):
    EU = "eu"
    NA = "na"
    SEA = "sea"
    MENA = "mena"

class ApiVersion(Enum):
    V1 = "v1"
    V2 = "v2"

class Locale(Enum):
    English = "en"
    Korean = "kr"

# ==============================
# RESPONSE MODEL
# ==============================

@dataclass
class ApiResponse:
    success: bool = False
    status_code: int = 0
    message: str = ""
    content: Any = None

# ==============================
# UTILITIES
# ==============================


def timestamp_to_datetime(ts: float) -> datetime:
    """Convert UNIX timestamp (in seconds) to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def check_for_updates():
    """Optional background updater — can be adapted or removed."""
    # Placeholder: in a real project this might check item lists or update cached data
    return

# ==============================
# MAIN CLASS
# ==============================

# ─── Discord Bot ──────────────────────────────────────────────────────────
class BossBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.sent_alerts = self._load_json(DATA_FILE)
        self.guild_config = self._load_json(CONFIG_FILE)
        self.boss_roles = {}
        self.sent_alert_msg = self._load_json(ALERT_MSG_FILE)

    def _load_json(self, path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _save_json(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    async def ensure_boss_roles(self, guild: discord.Guild):
        existing = {r.name: r for r in guild.roles}
        for boss in BOSS_NAMES:
            if boss not in existing:
                role = await guild.create_role(name=boss, mentionable=True, reason="Boss alert role")
                print(f"Created role: {boss}")
            else:
                role = existing[boss]
            if not role.mentionable:
                await role.edit(mentionable=True)
            self.boss_roles[boss] = role
        print(f"[{guild.name}] Boss roles ready.")

    def cleanup_old_alerts(self, hours=1):
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - hours * 3600
        to_delete = [aid for aid, ts in self.sent_alerts.items() if ts < cutoff]
        for aid in to_delete:
            del self.sent_alerts[aid]
        if to_delete:
            self._save_json(DATA_FILE, self.sent_alerts)
            print(f"Cleaned up {len(to_delete)} old alerts.")
            
        
    async def ensure_boss_roles_on_ready(self):
        """Ensure all boss roles exist for every guild."""
        for guild in self.guilds:
            try:
                await self.ensure_boss_roles(guild)
                print(f"[⚙️] Verified boss roles in {guild.name}")
            except Exception as e:
                print(f"[❌] Failed to ensure roles in {guild.name}: {e}")



# Instantiate the bot
bot = BossBot()


class Market:
    def __init__(self, region: MarketRegion = MarketRegion.EU, apiversion: ApiVersion = ApiVersion.V2, language: Locale = Locale.English):
        self._base_url = "https://api.arsha.io"
        self._api_version = apiversion.value
        self._api_region = region.value
        self._api_lang = language.value
        self._session = requests.Session()
        threading.Thread(target=check_for_updates, daemon=True).start()

    # ------------- INTERNAL REQUEST HANDLERS -------------

    async def _make_request_async(self, method: str, endpoint: str,
                                  json_data: Optional[Any] = None,
                                  data: Optional[Any] = None,
                                  headers: Optional[Dict] = None,
                                  params: Optional[Dict] = None) -> ApiResponse:
        url = f"{self._base_url}/{self._api_version}/{self._api_region}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                content = await response.json()
                return ApiResponse(
                    success=200 <= response.status <= 299,
                    status_code=response.status,
                    message=response.reason or "No message provided",
                    content=content
                )

    def _make_request_sync(self, method: str, endpoint: str,
                           json_data: Optional[Any] = None,
                           data: Optional[Any] = None,
                           headers: Optional[Dict] = None,
                           params: Optional[Dict] = None) -> ApiResponse:
        url = f"{self._base_url}/{self._api_version}/{self._api_region}/{endpoint}"
        try:
            if self._session is None:
                self._session = requests.Session()
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                data=data,
                headers=headers,
                timeout=10
            )
            content = response.json() if response.content else {}
            return ApiResponse(
                success=200 <= response.status_code <= 299,
                status_code=response.status_code,
                message=response.reason or "No message provided",
                content=content
            )
        except requests.RequestException as e:
            return ApiResponse(success=False, message=str(e))

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

    # ------------- API METHODS -------------

    # Wait List
    async def get_world_market_wait_list(self) -> ApiResponse:
        return await self._make_request_async("GET", "GetWorldMarketWaitList")

    def get_world_market_wait_list_sync(self) -> ApiResponse:
        return self._make_request_sync("GET", "GetWorldMarketWaitList")

    # Hot List
    async def get_world_market_hot_list(self) -> ApiResponse:
        return await self._make_request_async("GET", "GetWorldMarketHotList")

    def get_world_market_hot_list_sync(self) -> ApiResponse:
        return self._make_request_sync("GET", "GetWorldMarketHotList")

    # Price Info
    async def get_market_price_info(self, ids: List[str], sids: List[str], convertdate: bool = True, formatprice: bool = False) -> ApiResponse:
        params = {"id": ids, "sid": sids, "lang": self._api_lang}
        result = await self._make_request_async("GET", "GetMarketPriceInfo", params=params)
        return self._convert_price_history(result, convertdate, formatprice)

    def get_market_price_info_sync(self, ids: List[str], sids: List[str], convertdate: bool = True, formatprice: bool = False) -> ApiResponse:
        params = {"id": ids, "sid": sids, "lang": self._api_lang}
        result = self._make_request_sync("GET", "GetMarketPriceInfo", params=params)
        return self._convert_price_history(result, convertdate, formatprice)

    def _convert_price_history(self, result: ApiResponse, convertdate: bool, formatprice: bool) -> ApiResponse:
        if not result.success or not result.content:
            return result
        content_list = [result.content] if isinstance(result.content, dict) else result.content
        for item in content_list:
            if "history" in item:
                new_history = {}
                for k, v in item["history"].items():
                    new_key = timestamp_to_datetime(float(k) / 1000).strftime("%Y-%m-%d") if convertdate else k
                    new_value = f"{v:,}" if formatprice else v
                    new_history[new_key] = new_value
                item["history"] = new_history
        result.content = content_list
        return result

    # Search
    async def get_world_market_search_list(self, ids: List[str]) -> ApiResponse:
        return await self._make_request_async("GET", "GetWorldMarketSearchList", params={"ids": ids, "lang": self._api_lang})

    def get_world_market_search_list_sync(self, ids: List[str]) -> ApiResponse:
        return self._make_request_sync("GET", "GetWorldMarketSearchList", params={"ids": ids, "lang": self._api_lang})

    # Category List
    async def get_world_market_list(self, main_category: str, sub_category: str) -> ApiResponse:
        params = {"mainCategory": main_category, "subCategory": sub_category, "lang": self._api_lang}
        return await self._make_request_async("GET", "GetWorldMarketList", params=params)

    def get_world_market_list_sync(self, main_category: str, sub_category: str) -> ApiResponse:
        params = {"mainCategory": main_category, "subCategory": sub_category, "lang": self._api_lang}
        return self._make_request_sync("GET", "GetWorldMarketList", params=params)

    # Sub List
    async def get_world_market_sub_list(self, ids: List[str]) -> ApiResponse:
        return await self._make_request_async("GET", "GetWorldMarketSubList", params={"id": ids, "lang": self._api_lang})

    def get_world_market_sub_list_sync(self, ids: List[str]) -> ApiResponse:
        return self._make_request_sync("GET", "GetWorldMarketSubList", params={"id": ids, "lang": self._api_lang})


# ─── Slash Commands ──────────────────────────────────────────────────────

@bot.tree.command(name="setupalerts", description="Set the channel for boss alerts (admin only).")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def setupalerts(interaction: discord.Interaction, channel: discord.TextChannel):
    bot.guild_config[str(interaction.guild_id)] = {"channel_id": channel.id}
    bot._save_json(CONFIG_FILE, bot.guild_config)
    await interaction.response.send_message(f"✅ Alerts will now be sent in {channel.mention}.", ephemeral=True)

@setupalerts.error
async def setupalerts_error(interaction, error):
    if isinstance(error, discord.app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You need 'Manage Server' permission to use this command.", ephemeral=True)

@bot.tree.command(name="subscribe", description="Subscribe to alerts for a boss.")
@discord.app_commands.describe(boss="Name of the boss to subscribe to.")
async def subscribe(interaction: discord.Interaction, boss: str):
    boss = boss.title()
    if boss not in BOSS_NAMES:
        await interaction.response.send_message("Invalid boss name.", ephemeral=True)
        return

    role = bot.boss_roles.get(boss)
    if not role:
        await interaction.response.send_message("Boss role not found.", ephemeral=True)
        return

    member = interaction.user
    if role in member.roles:
        await interaction.response.send_message(f"You're already subscribed to {boss}.", ephemeral=True)
        return

    await member.add_roles(role, reason="Boss subscription")
    await interaction.response.send_message(f"✅ Subscribed to {boss} alerts!", ephemeral=True)

@bot.tree.command(name="unsubscribe", description="Unsubscribe from alerts for a boss.")
@discord.app_commands.describe(boss="Name of the boss to unsubscribe from.")
async def unsubscribe(interaction: discord.Interaction, boss: str):
    boss = boss.title()
    if boss not in BOSS_NAMES:
        await interaction.response.send_message("Invalid boss name.", ephemeral=True)
        return

    role = bot.boss_roles.get(boss)
    if not role:
        await interaction.response.send_message("Boss role not found.", ephemeral=True)
        return

    member = interaction.user
    if role not in member.roles:
        await interaction.response.send_message(f"You're not subscribed to {boss}.", ephemeral=True)
        return

    await member.remove_roles(role, reason="Boss unsubscription")
    await interaction.response.send_message(f"❎ Unsubscribed from {boss}.", ephemeral=True)
    
@bot.tree.command(name="subscribeall", description="Subscribe to all boss alerts.")
async def subscribeall(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)  # ⬅️ immediately acknowledge

    member = interaction.user
    added = 0

    # Make sure all boss roles exist
    await bot.ensure_boss_roles(interaction.guild)

    for boss, role in bot.boss_roles.items():
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Subscribed to all bosses")
                added += 1
            except Exception as e:
                print(f"Failed to add {role.name} to {member}: {e}")

    if added == 0:
        msg = "✅ You're already subscribed to all bosses."
    else:
        msg = f"✅ Subscribed to **{added}** bosses."

    await interaction.followup.send(msg, ephemeral=True)  # ⬅️ final response

@bot.tree.command(name="unsubscribeall", description="Unsubscribe from all boss alerts.")
async def unsubscribeall(interaction: discord.Interaction):
    member = interaction.user
    removed = 0

    for boss, role in bot.boss_roles.items():
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Unsubscribed from all bosses")
                removed += 1
            except Exception as e:
                print(f"Failed to remove {role.name} from {member}: {e}")

    if removed == 0:
        await interaction.response.send_message("You're not subscribed to any boss alerts.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❎ Unsubscribed from **{removed}** bosses.", ephemeral=True)

@bot.tree.command(name="createroles", description="Manually create any missing boss roles.")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def createroles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    before = len(guild.roles)
    await bot.ensure_boss_roles(guild)
    after = len(guild.roles)

    created_count = after - before
    if created_count == 0:
        msg = "✅ All boss roles already exist."
    else:
        msg = f"✅ Created **{created_count}** missing boss roles."

    await interaction.followup.send(msg, ephemeral=True)
    
@bot.tree.command(name="testpoll", description="Force the bot to post alerts for the next bosses.")
async def testpoll(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild_id)
    channel_id = bot.guild_config.get(guild_id, {}).get("channel_id")
    channel = bot.get_channel(channel_id)
    if not channel:
        await interaction.followup.send("Alert channel not set! Use /setupalerts first.", ephemeral=True)
        return

    now = datetime.now(timezone.utc)
    try:
        bosses = await fetch_bosses("NA")
        if not bosses:
            await interaction.followup.send("⚠️ No boss data available.", ephemeral=True)
            return

        # Find next spawn per boss
        next_spawns = {}
        for boss in bosses:
            spawn_utc = parse_time_str_to_utc(boss["time_str"])
            if spawn_utc < now:
                continue
            if boss["name"] not in next_spawns or spawn_utc < next_spawns[boss["name"]]:
                next_spawns[boss["name"]] = spawn_utc

        if not next_spawns:
            await interaction.followup.send("⚠️ No upcoming boss spawns found.", ephemeral=True)
            return

        # Compose alert messages like the poll loop
        messages_to_send = []
        for name, spawn in next_spawns.items():
            minutes_until = max(int((spawn - now).total_seconds() // 60), 0)
            role = bot.boss_roles.get(name)
            mention = role.mention if role else name
            messages_to_send.append(f"⚠️ {mention} spawns in {minutes_until} minutes! [TEST]")

        if messages_to_send:
            # Delete previous alert if exists
            last_msg_id = bot.sent_alert_msg.get(guild_id)
            if last_msg_id:
                try:
                    last_msg = await channel.fetch_message(last_msg_id)
                    await last_msg.delete()
                except Exception:
                    pass

            new_msg = await channel.send("\n".join(messages_to_send))
            bot.sent_alert_msg[guild_id] = new_msg.id
            bot._save_json(ALERT_MSG_FILE, bot.sent_alert_msg)

            await interaction.followup.send(f"✅ Test poll sent for {len(messages_to_send)} bosses.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ No bosses to alert.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ Failed to fetch boss timers: {e}", ephemeral=True)
        
patch_group = app_commands.Group(name="patch", description="Patch notes settings & tools.")

@patch_group.command(name="setchannel", description="Set the patch notes channel for this server.")
@app_commands.checks.has_permissions(administrator=True)
async def set_patch_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    config = load_json(PATCH_CONFIG_FILE)
    config[str(interaction.guild_id)] = channel.id
    save_json(PATCH_CONFIG_FILE, config)
    await interaction.response.send_message(f"✅ Patch notes channel set to {channel.mention}")

@patch_group.command(name="check", description="Force check patch notes now.")
@app_commands.checks.has_permissions(administrator=True)
async def check_patch_now(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    result = await fetch_latest_patch()
    if not result:
        await interaction.followup.send("❌ Could not fetch patch notes.")
        return

    title, url = result
    embed = discord.Embed(
        title=f"📰 Latest Patch Notes: {title}",
        url=url,
        description=f"[Read full patch notes here]({url})",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    await interaction.followup.send(embed=embed)
    
# ─── Slash Command Tree ───────────────────────────────────
market_group = app_commands.Group(name="market", description="Marketplace tools and utilities.")

# ─── PRICE ────────────────────────────────────────────────
@market_group.command(name="price", description="Check current marketplace price for an item.")
@app_commands.describe(item="Item name to check")
async def price(interaction: discord.Interaction, item: str):
    data = get_item_data(item)
    if not data:
        await interaction.response.send_message(f"❌ Item `{item}` not found.")
        return

    embed = discord.Embed(
        title=f"📊 Marketplace — {data['name']}",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="💰 Price", value=f"{data['price']:,} silver", inline=True)
    embed.add_field(name="📦 Stock", value=f"{data['stock']:,}", inline=True)
    embed.set_footer(text="Marketplace data")
    await interaction.response.send_message(embed=embed)

# ─── PROFIT ───────────────────────────────────────────────
@market_group.command(name="profit", description="Calculate flipping profit with or without a value pack.")
@app_commands.describe(
    item="Item name",
    buy_price="Price you plan to buy at",
    sell_price="Price you plan to sell at",
    value_pack="Do you have a Value Pack active?"
)
async def profit(interaction: discord.Interaction, item: str, buy_price: int, sell_price: int, value_pack: bool):
    tax_rate = TAX_RATE_VP if value_pack else TAX_RATE_NORMAL
    tax = int(sell_price * tax_rate)
    net_profit = (sell_price - tax) - buy_price

    emoji = "✅" if net_profit > 0 else "⚠️"
    embed = discord.Embed(
        title=f"💹 Flip Calculator — {item}",
        color=discord.Color.green() if net_profit > 0 else discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="🛒 Buy Price", value=f"{buy_price:,} silver", inline=True)
    embed.add_field(name="🏷 Sell Price", value=f"{sell_price:,} silver", inline=True)
    embed.add_field(name="📜 Tax Rate", value=f"{int(tax_rate*100)}%", inline=True)
    embed.add_field(name="💸 Tax", value=f"{tax:,} silver", inline=True)
    embed.add_field(name=f"{emoji} Net Profit", value=f"{net_profit:,} silver", inline=False)
    await interaction.response.send_message(embed=embed)

# ─── STOCK ────────────────────────────────────────────────
@market_group.command(name="stock", description="Check stock trend for an item.")
@app_commands.describe(item="Item name")
async def stock(interaction: discord.Interaction, item: str):
    data = get_item_data(item)
    if not data:
        await interaction.response.send_message(f"❌ Item `{item}` not found.")
        return

    trend = data.get("trend", "📈 No trend data yet")
    embed = discord.Embed(
        title=f"📦 Stock — {data['name']}",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Current Stock", value=f"{data['stock']:,}", inline=True)
    embed.add_field(name="Trend", value=trend, inline=True)
    await interaction.response.send_message(embed=embed)

# ─── WATCH ────────────────────────────────────────────────
@market_group.command(name="watch", description="Set a price alert for an item.")
@app_commands.describe(item="Item name", target_price="Alert price")
async def watch(interaction: discord.Interaction, item: str, target_price: int):
    # You’d persist this alert in a file or database
    await interaction.response.send_message(
        f"👀 Alert created for `{item}` — I'll notify you when it drops below **{target_price:,} silver**."
    )

# ─── SEARCH ───────────────────────────────────────────────
@market_group.command(name="search", description="Search marketplace for items by keyword.")
@app_commands.describe(keyword="Keyword to search for")
async def search(interaction: discord.Interaction, keyword: str):
    results = fake_search_items(keyword)
    if not results:
        await interaction.response.send_message(f"🔍 No results for `{keyword}`.")
        return

    embed = discord.Embed(
        title=f"🔍 Search Results for '{keyword}'",
        description="\n".join([f"{i+1}. {name}" for i, name in enumerate(results)]),
        color=discord.Color.teal()
    )
    await interaction.response.send_message(embed=embed)

def fake_search_items(keyword: str):
    sample_items = ["Ogre Ring", "Black Stone (Weapon)", "Caphras Stone", "Memory Fragment"]
    return [x for x in sample_items if keyword.lower() in x.lower()]

# ─── SILVERCALC ───────────────────────────────────────────
@market_group.command(name="silvercalc", description="Calculate total silver cost with or without value pack.")
@app_commands.describe(
    item="Item name",
    quantity="How many you want",
    value_pack="Do you have a Value Pack active?"
)
async def silvercalc(interaction: discord.Interaction, item: str, quantity: int, value_pack: bool):
    data = get_item_data(item)
    if not data:
        await interaction.response.send_message(f"❌ Item `{item}` not found.")
        return

    tax_rate = TAX_RATE_VP if value_pack else TAX_RATE_NORMAL
    total_cost = data['price'] * quantity
    tax_cost = int(total_cost * tax_rate)
    total_after_tax = total_cost - tax_cost

    embed = discord.Embed(
        title=f"🧾 Silver Calculator — {data['name']}",
        color=discord.Color.purple(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Unit Price", value=f"{data['price']:,} silver", inline=True)
    embed.add_field(name="Quantity", value=f"{quantity:,}", inline=True)
    embed.add_field(name="Total Cost", value=f"{total_cost:,} silver", inline=False)
    embed.add_field(name="Tax Rate", value=f"{int(tax_rate*100)}%", inline=True)
    embed.add_field(name="After Tax", value=f"{total_after_tax:,} silver", inline=True)
    await interaction.response.send_message(embed=embed)

        
# Map abbreviated weekdays to integers
DAYS_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
PST = pytz.timezone("US/Pacific")

def parse_time_str_to_utc(time_str):
    """
    Convert MMOTimer 'Tue 18:15' to UTC datetime.
    """
    day_abbr, hm = time_str.split()
    hour, minute = map(int, hm.split(":"))

    now_pst = datetime.now(PST)
    weekday_today = now_pst.weekday()
    target_weekday = DAYS_MAP[day_abbr]

    # Days until target day
    delta_days = (target_weekday - weekday_today) % 7
    target_date = now_pst + timedelta(days=delta_days)
    target_dt = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If the time already passed today, move to next week
    if target_dt < now_pst:
        target_dt += timedelta(days=7)

    return target_dt.astimezone(pytz.utc)

# ─── Refresh Boss Data Loop ─────────────────────────────────────────────
@tasks.loop(hours=1)
async def refresh_boss_data():
    try:
        bot.boss_data = await fetch_bosses("NA")  # fetch fresh data
        now = datetime.now(timezone.utc)
        if bot.boss_data:
            print(f"✅ Refreshed boss data at {now}")
        else:
            print(f"⚠️ No boss data available at {now}")
    except Exception as e:
        print(f"❌ Failed to refresh boss data: {e}")


# ─── Poll and Alert Loop ───────────────────────────────────────────────
@tasks.loop(seconds=POLL_INTERVAL)
async def poll_and_alert():
    await bot.wait_until_ready()
    bot.cleanup_old_alerts(hours=1)

    bosses = getattr(bot, "boss_data", None)
    if not bosses:
        return  # no data yet

    now = datetime.now(timezone.utc)

    for guild_id, config in bot.guild_config.items():
        channel_id = config.get("channel_id")
        channel = bot.get_channel(channel_id)
        if not channel:
            continue

        messages_to_send = []

        for boss in bosses:
            name = boss["name"]
            spawn = parse_time_str_to_utc(boss["time_str"])
            minutes_until = int((spawn - now).total_seconds() // 60)

            if minutes_until in ALERT_LEAD_MINUTES:
                alert_id = f"{guild_id}_{name}_{minutes_until}"
                if alert_id in bot.sent_alerts:
                    continue  # skip duplicate

                role = bot.boss_roles.get(name)
                mention = role.mention if role else name
                messages_to_send.append(f"⚠️ {mention} spawns in {minutes_until} minutes!")

                # Mark alert as sent
                bot.sent_alerts[alert_id] = now.timestamp()

        if messages_to_send:
            bot._save_json(DATA_FILE, bot.sent_alerts)

            # Delete previous alert if it exists
            last_msg_id = bot.sent_alert_msg.get(guild_id)
            if last_msg_id:
                try:
                    last_msg = await channel.fetch_message(last_msg_id)
                    await last_msg.delete()
                except Exception:
                    pass  # message may have been deleted manually

            # Send new alert and store its message ID
            try:
                new_msg = await channel.send("\n".join(messages_to_send))
                bot.sent_alert_msg[guild_id] = new_msg.id
                bot._save_json(ALERT_MSG_FILE, bot.sent_alert_msg)
            except Exception as e:
                print(f"❌ Failed to send alert in guild {guild_id}: {e}")

# ─── On Ready ─────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print("UTC now:", datetime.now(timezone.utc))
    print("Local now:", datetime.now())  # local time

    # Ensure all boss roles exist
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"[⚡] Synced commands to {guild.name}")
        try:
            await bot.ensure_boss_roles(guild)
        except Exception as e:
            print(f"⚠️ Failed to ensure boss roles for {guild.name}: {e}")

    # Fetch next boss timers
    try:
        bot.boss_data = await fetch_bosses("NA")
        now = datetime.now(timezone.utc)

        if bot.boss_data:
            # Create a dict to track next spawn per boss
            next_spawns = {}
            for boss in bot.boss_data:
                try:
                    spawn_utc = parse_time_str_to_utc(boss["time_str"])
                    if spawn_utc < now:
                        continue  # skip past spawns

                    # Only keep the earliest spawn per boss
                    if boss["name"] not in next_spawns or spawn_utc < next_spawns[boss["name"]]:
                        next_spawns[boss["name"]] = spawn_utc
                except Exception as e:
                    print(f"⚠️ Failed to parse spawn for boss {boss.get('name')}: {e}")

            if next_spawns:
                print("🕒 Upcoming boss spawns:")
                for name, spawn in sorted(next_spawns.items(), key=lambda x: x[1]):
                    minutes_left = max(int((spawn - now).total_seconds() // 60), 0)
                    hours, mins = divmod(minutes_left, 60)
                    print(f" - {name}: spawns at {spawn} UTC (in {hours}h {mins}m)")
            else:
                print("⚠️ No upcoming boss spawns found.")
        else:
            print("⚠️ No boss data available.")

    except Exception as e:
        print("⚠️ Failed to fetch boss timers:", e)

    # Start the polling loop only if it's not already running
    if not poll_and_alert.is_running():
        poll_and_alert.start()
        print(f"⏱️ Started poll_and_alert loop (every {POLL_INTERVAL} seconds)")




# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
        print("⚠️ Please set your bot token before running.")
    else:
        bot.run(DISCORD_TOKEN)
