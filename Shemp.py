import json
from datetime import datetime, timedelta, timezone
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests
import pytz
import threading
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum
import os

# ──────── Intents ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True  # 👈 Enable message content intent
intents.guilds = True
intents.members = True
intents.guild_messages = True


# ─── Initialize Bot ─────────────────────────────

bot = commands.Bot(command_prefix="!", intents=intents)

# ─── CONFIG ──────────────────────────────────────────────────────────────

DISCORD_TOKEN = "DISCORD_TOKEN"
POLL_INTERVAL = 15
ALERT_LEAD_MINUTES = [60, 30, 5]
DATA_FILE = "alerts_sent.json"
CONFIG_FILE = "guild_config.json"
ALERT_MSG_FILE = "last_alerts.json"
PATCH_CONFIG_FILE = "patch_config.json"
LAST_PATCH_FILE = "last_patch.json"
MARKET_FILE = "market_data.json"
PATCH_URL = "https://www.naeu.playblackdesert.com/en-US/News/Notice?boardType=2"  # Patch Notes board

# ─── Constants ───────────────────────────────────────────

BOSS_NAMES = [
    "Garmoth", "Karanda", "Kutum", "Kzarka", "Muraka",
    "Nouver", "Offin", "Quint", "Vell", "Golden Pig King",
    "Bulgasal", "Uturi", "Sangoon",
]



# ─── Load/Save JSON ───────────────────────────────────────────────
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# Attach the data to bot object
bot.sent_alerts = load_json(DATA_FILE)
bot.guild_config = load_json(CONFIG_FILE)
bot.sent_alert_msg = load_json(ALERT_MSG_FILE)
bot.boss_roles = {}


# ─── Scraper ──────────────────────────────────────────────────────

async def fetch_latest_patch():
    """
    Scrapes the latest patch note from the official site.
    Returns (title, url) or None if failed.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(PATCH_URL) as resp:
            if resp.status != 200:
                print(f"❌ Failed to fetch patch notes: status {resp.status}")
                return None
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")

    # Look inside the actual patch notes list
    first_post = soup.select_one("ul.thumb_nail_list li a")
    if not first_post:
        print("❌ No patch notes found in .thumb_nail_list — structure may have changed.")
        return None

    # Extract title text
    title_elem = first_post.select_one(".title .line_clamp")
    if not title_elem:
        print("❌ Could not find title element.")
        return None
    title = title_elem.get_text(strip=True)

    # Extract URL
    href = first_post.get("href")
    if not href:
        print("❌ No href found for patch notes.")
        return None
    if not href.startswith("http"):
        href = "https://www.naeu.playblackdesert.com" + href

    print(f"✅ Found latest patch: {title} -> {href}")
    return (title, href)


# ─── Boss Roles Enforcer ───────────────────────────────────────────────

async def ensure_boss_roles(guild: discord.Guild):
    """
    Make sure all boss roles exist in this guild and are mentionable.
    Stores roles in bot.boss_roles[guild.id][boss_name].
    """
    if not hasattr(bot, "boss_roles"):
        bot.boss_roles = {}

    if guild.id not in bot.boss_roles:
        bot.boss_roles[guild.id] = {}

    existing = {r.name: r for r in guild.roles}

    for boss in BOSS_NAMES:
        if boss in existing:
            role = existing[boss]
            # Ensure mentionable
            if not role.mentionable:
                await role.edit(mentionable=True)
        else:
            role = await guild.create_role(
                name=boss,
                mentionable=True,
                reason="Boss alert role"
            )
            print(f"[{guild.name}] Created role: {boss}")

        bot.boss_roles[guild.id][boss] = role

    print(f"[{guild.name}] Boss roles ready.")


# ─── Patch Notes Loop ─────────────────────────────────────────────
@tasks.loop(minutes=180)
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



def timestamp_to_datetime(ts: float) -> datetime:
    """Convert UNIX timestamp (in seconds) to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)



def cleanup_old_alerts(sent_alerts: dict, hours=1):
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - hours * 3600
    to_delete = [aid for aid, ts in sent_alerts.items() if ts < cutoff]
    for aid in to_delete:
        del sent_alerts[aid]
    if to_delete:
        with open(DATA_FILE, "w") as f:
            json.dump(sent_alerts, f, indent=2)
        print(f"🧹 Cleaned up {len(to_delete)} old alerts.")


# ─── Slash Commands ──────────────────────────────────────────────────────
patch_group = app_commands.Group(name="patch", description="Patch notes settings & tools.")
boss_group = app_commands.Group(name="boss", description="Boss alert subscriptions & management")

# --- /boss setupalerts ---
@boss_group.command(name="setupalerts", description="Set the channel for boss alerts (admin only).")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def setupalerts(interaction: discord.Interaction, channel: discord.TextChannel):
    bot.guild_config[str(interaction.guild_id)] = {"channel_id": channel.id}
    save_json(CONFIG_FILE, bot.guild_config)
    await interaction.response.send_message(f"✅ Alerts will now be sent in {channel.mention}.", ephemeral=True)

@setupalerts.error
async def setupalerts_error(interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need 'Manage Server' permission.", ephemeral=True)

# --- /boss subscribe ---
@boss_group.command(name="subscribe", description="Subscribe to alerts for a boss.")
@discord.app_commands.describe(boss="Name of the boss to subscribe to.")
async def subscribe(interaction: discord.Interaction, boss: str):
    boss = boss.title()
    if boss not in BOSS_NAMES:
        await interaction.response.send_message("❌ Invalid boss name.", ephemeral=True)
        return

    guild_roles = bot.boss_roles.get(interaction.guild.id, {})
    role = guild_roles.get(boss)
    if not role:
        await interaction.response.send_message("❌ Boss role not found.", ephemeral=True)
        return

    member = interaction.user
    if role in member.roles:
        await interaction.response.send_message(f"✅ Already subscribed to {boss}.", ephemeral=True)
        return

    await member.add_roles(role, reason="Boss subscription")
    await interaction.response.send_message(f"✅ Subscribed to {boss} alerts!", ephemeral=True)

# --- /boss unsubscribe ---
@boss_group.command(name="unsubscribe", description="Unsubscribe from alerts for a boss.")
@discord.app_commands.describe(boss="Name of the boss to unsubscribe from.")
async def unsubscribe(interaction: discord.Interaction, boss: str):
    boss = boss.title()
    if boss not in BOSS_NAMES:
        await interaction.response.send_message("❌ Invalid boss name.", ephemeral=True)
        return

    guild_roles = bot.boss_roles.get(interaction.guild.id, {})
    role = guild_roles.get(boss)
    if not role:
        await interaction.response.send_message("❌ Boss role not found.", ephemeral=True)
        return

    member = interaction.user
    if role not in member.roles:
        await interaction.response.send_message(f"❌ You're not subscribed to {boss}.", ephemeral=True)
        return

    await member.remove_roles(role, reason="Boss unsubscription")
    await interaction.response.send_message(f"❎ Unsubscribed from {boss}.", ephemeral=True)

# --- /boss subscribeall ---
@boss_group.command(name="subscribeall", description="Subscribe to all boss alerts.")
async def subscribeall(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    member = interaction.user
    added = 0

    await ensure_boss_roles(interaction.guild)
    guild_roles = bot.boss_roles.get(interaction.guild.id, {})

    for role in guild_roles.values():
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Subscribed to all bosses")
                added += 1
            except Exception as e:
                print(f"Failed to add {role.name} to {member}: {e}")

    msg = "✅ Already subscribed to all bosses." if added == 0 else f"✅ Subscribed to {added} bosses."
    await interaction.followup.send(msg, ephemeral=True)

# --- /boss unsubscribeall ---
@boss_group.command(name="unsubscribeall", description="Unsubscribe from all boss alerts.")
async def unsubscribeall(interaction: discord.Interaction):
    member = interaction.user
    removed = 0
    guild_roles = bot.boss_roles.get(interaction.guild.id, {})

    for role in guild_roles.values():
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Unsubscribed from all bosses")
                removed += 1
            except Exception as e:
                print(f"Failed to remove {role.name} from {member}: {e}")

    msg = "❌ Not subscribed to any bosses." if removed == 0 else f"❎ Unsubscribed from {removed} bosses."
    await interaction.response.send_message(msg, ephemeral=True)

# --- /boss createroles ---
@boss_group.command(name="createroles", description="Manually create missing boss roles.")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def createroles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    before = len(interaction.guild.roles)
    await ensure_boss_roles(interaction.guild)
    after = len(interaction.guild.roles)
    created_count = after - before
    msg = "✅ All boss roles already exist." if created_count == 0 else f"✅ Created {created_count} missing boss roles."
    await interaction.followup.send(msg, ephemeral=True)

# --- /boss testpoll ---
@boss_group.command(name="testpoll", description="Force post alerts for next bosses (testing).")
async def testpoll(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    channel_id = bot.guild_config.get(guild_id, {}).get("channel_id")
    channel = bot.get_channel(channel_id)
    if not channel:
        await interaction.followup.send("❌ Alert channel not set! Use /boss setupalerts.", ephemeral=True)
        return

    try:
        bosses = await fetch_bosses("NA")
        if not bosses:
            await interaction.followup.send("⚠️ No boss data available.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        next_spawns = {}
        for boss in bosses:
            spawn_utc = parse_time_str_to_utc(boss["time_str"])
            if spawn_utc < now:
                continue
            if boss["name"] not in next_spawns or spawn_utc < next_spawns[boss["name"]]:
                next_spawns[boss["name"]] = spawn_utc

        guild_roles = bot.boss_roles.get(interaction.guild.id, {})
        messages_to_send = []
        for name, spawn in next_spawns.items():
            minutes_until = max(int((spawn - now).total_seconds() // 60), 0)
            role = guild_roles.get(name)
            mention = role.mention if role else name
            messages_to_send.append(f"⚠️ {mention} spawns in {minutes_until} minutes! [TEST]")

        if messages_to_send:
            last_msg_id = bot.sent_alert_msg.get(guild_id)
            if last_msg_id:
                try:
                    last_msg = await channel.fetch_message(last_msg_id)
                    await last_msg.delete()
                except Exception:
                    pass
            new_msg = await channel.send("\n".join(messages_to_send))
            bot.sent_alert_msg[guild_id] = new_msg.id
            save_json(ALERT_MSG_FILE, bot.sent_alert_msg)
            await interaction.followup.send(f"✅ Test poll sent for {len(messages_to_send)} bosses.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ No bosses to alert.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch boss timers: {e}", ephemeral=True)


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

# ───  Map abbreviated weekdays to integers ───────────────────────────────────────────────

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

@tasks.loop(hours=24)
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
    cleanup_old_alerts(bot.sent_alerts, hours=1)

    bosses = getattr(bot, "boss_data", None)
    if not bosses:
        return  # no data yet

    now = datetime.now(timezone.utc)

    for guild_id_str, config in bot.guild_config.items():
        guild_id = int(guild_id_str)
        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        # Ensure roles exist for this guild
        await ensure_boss_roles(guild)
        roles = bot.boss_roles.get(guild_id, {})

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

                role = roles.get(name)
                mention = role.mention if role else name
                messages_to_send.append(f"⚠️ {mention} spawns in {minutes_until} minutes!")

                # Mark alert as sent
                bot.sent_alerts[alert_id] = now.timestamp()

        if messages_to_send:
            save_json(DATA_FILE, bot.sent_alerts)

            # Delete previous alert if it exists
            last_msg_id = bot.sent_alert_msg.get(str(guild_id))
            if last_msg_id:
                try:
                    last_msg = await channel.fetch_message(last_msg_id)
                    await last_msg.delete()
                except Exception:
                    pass  # message may have been deleted manually

            # Send new alert and store its message ID
            try:
                new_msg = await channel.send("\n".join(messages_to_send))
                bot.sent_alert_msg[str(guild_id)] = new_msg.id
                save_json(ALERT_MSG_FILE, bot.sent_alert_msg)
            except Exception as e:
                print(f"❌ Failed to send alert in guild {guild.name}: {e}")


# ─── Bot Startup ───────────────────────────────────────────────
                
@bot.event
async def setup_hook():
    # 1) Add groups to the tree (must be done after group's commands defined)
    try:
        bot.tree.add_command(boss_group)
        bot.tree.add_command(patch_group)
        print("✅ Added command groups to tree: market, patch")
    except Exception as e:
        print(f"⚠️ Failed to add command groups: {e}")

    # 2) Sync commands to each guild (preferred during development)
    #    This registers the commands for each guild the bot is in.
    for guild in bot.guilds:
        try:
            await bot.tree.sync(guild=discord.Object(id=guild.id))
            print(f"🔁 Synced commands to guild: {guild.name} ({guild.id})")
        except Exception as e:
            print(f"⚠️ Failed to sync commands to guild {guild.name}: {e}")

    # 3) Optionally sync global commands once (can be slow to propagate).
    try:
        await bot.tree.sync()
        print("🌐 Synced global commands")
    except Exception as e:
        print(f"⚠️ Failed to sync global commands: {e}")

    # 4) Ensure boss roles exist in all guilds
        try:
            await ensure_boss_roles(guild)   # ✅ call the function directly
        except Exception as e:
            print(f"⚠️ ensure_boss_roles error in {guild.name}: {e}")

    # 5) Fetch initial boss data (so poll loop has something immediately)
    try:
        bot.boss_data = await fetch_bosses("NA")
        if bot.boss_data:
            print(f"✅ Initial boss data loaded ({len(bot.boss_data)} entries)")
        else:
            print("⚠️ Initial boss data empty")
    except Exception as e:
        print(f"⚠️ Failed to fetch initial boss data: {e}")

    # 6) Start background loops (if not already running)
    try:
        if not refresh_boss_data.is_running():
            refresh_boss_data.start()
            print("⏱️ Started refresh_boss_data (hourly)")
    except Exception as e:
        print(f"⚠️ Failed to start refresh_boss_data: {e}")

    try:
        if not poll_and_alert.is_running():
            poll_and_alert.start()
            print(f"⏱️ Started poll_and_alert (every {POLL_INTERVAL} seconds)")
    except Exception as e:
        print(f"⚠️ Failed to start poll_and_alert: {e}")

    # patch_notes_check expects the bot instance as argument in its start()
    try:
        if not patch_notes_check.is_running():
            patch_notes_check.start(bot)
            print("⏱️ Started patch_notes_check (every 30 minutes)")
    except Exception as e:
        print(f"⚠️ Failed to start patch_notes_check: {e}")

    print("✅ setup_hook complete")

# ─── On Ready ──────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print("UTC now:", datetime.now(timezone.utc))
    print("Local now:", datetime.now())
            
    # Sync slash commands (market, patch, boss, etc.)
    try:
        synced = await bot.tree.sync()
        print(f"🌐 Synced {len(synced)} global slash commands.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

    # (Optional) Start any loops
    if not poll_and_alert.is_running():
        poll_and_alert.start()
    if not patch_notes_check.is_running():
        patch_notes_check.start()

# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
        print("⚠️ Please set your bot token before running.")
    else:
        bot.run(DISCORD_TOKEN)
