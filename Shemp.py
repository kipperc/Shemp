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

# ─── CONFIG ──────────────────────────────────────────────────────────────
DISCORD_TOKEN = "DISCORD_TOKEN"
POLL_INTERVAL = 30
ALERT_LEAD_MINUTES = [60, 30, 5]

DATA_FILE = "alerts_sent.json"
CONFIG_FILE = "guild_config.json"
ALERT_MSG_FILE = "last_alerts.json"


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
    return await loop.run_in_executor(None, scraper.scrape


# ─── Discord Bot ──────────────────────────────────────────────────────────
class BossBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.sent_alerts = self._load_json(DATA_FILE)
        self.guild_config = self._load_json(CONFIG_FILE)
        self.boss_roles = {}
        self.sent_alert_msg = self._load_json(ALERT_MSG_FILE)


    async def setup_hook(self):
        # Sync all guild commands first (instant)
        for guild in self.guilds:
            try:
                await self.tree.sync(guild=discord.Object(id=guild.id))
                print(f"✅ Synced commands to guild: {guild.name} ({guild.id})")
            except Exception as e:
                print(f"⚠️ Failed to sync commands for guild {guild.name}: {e}")

        # Optionally, sync global commands (propagates slowly)
        try:
            await self.tree.sync()
            print("🌐 Synced global commands")
        except Exception as e:
            print(f"⚠️ Failed to sync global commands: {e}")
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

bot = BossBot()

# ─── Polling + Alerts ────────────────────────────────────────────────────
@tasks.loop(seconds=POLL_INTERVAL)
async def poll_and_alert():
    bot.cleanup_old_alerts(hours=1)

    try:
        bosses = await fetch_bosses("NA")  # NA / EU / SEA
    except Exception as e:
        print("Failed to fetch boss timers:", e)
        return

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
            new_msg = await channel.send("\n".join(messages_to_send))
            bot.sent_alert_msg[guild_id] = new_msg.id
            bot._save_json(ALERT_MSG_FILE, bot.sent_alert_msg)

@tasks.loop(hours=24)
async def refresh_schedule():
    # Re-fetch or reload boss data from file or URL
    bot.bosses = load_boss_data()


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

@bot.tree.command(name="testalert", description="Send a test alert for a boss.")
@discord.app_commands.describe(boss="Name of the boss to test", lead="Lead time in minutes (0, 5, 30)")
async def testalert(interaction: discord.Interaction, boss: str, lead: int):
    await interaction.response.defer(ephemeral=True)

    boss = boss.title()
    if boss not in BOSS_NAMES:
        await interaction.followup.send("Invalid boss name.", ephemeral=True)
        return
    if lead not in [0, 5, 30]:
        await interaction.followup.send("Lead time must be 0, 5, or 30.", ephemeral=True)
        return

    # Get the alert channel for this guild
    guild_id = str(interaction.guild_id)
    channel_id = bot.guild_config.get(guild_id, {}).get("channel_id")
    channel = bot.get_channel(channel_id)
    if not channel:
        await interaction.followup.send("Alert channel not set! Use /setupalerts first.", ephemeral=True)
        return

    # Create the message
    role = bot.boss_roles.get(boss)
    mention = role.mention if role else f"**{boss}**"
    if lead == 30:
        message = f"⚠️ {mention} spawns in 30 minutes! [TEST]"
    elif lead == 5:
        message = f"🔥 {mention} spawns in 5 minutes! [TEST]"
    else:
        message = f"🚨 {mention} has spawned! [TEST]"

    await channel.send(message)
    await interaction.followup.send(f"Test alert sent for {boss} ({lead} min).", ephemeral=True)


@bot.tree.command(
    name="nextboss",
    description="Show the next boss(es) that will spawn."
)
async def nextboss(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        bosses = await fetch_bosses("NA")  # adjust server if needed
        now = datetime.now(timezone.utc)

        if not bosses:
            await interaction.followup.send("⚠️ No boss data available.", ephemeral=True)
            return

        # Filter out past spawns
        upcoming = []
        for boss in bosses:
            spawn_utc = parse_time_str_to_utc(boss["time_str"])
            if spawn_utc >= now:
                upcoming.append((boss["name"], spawn_utc))

        if not upcoming:
            await interaction.followup.send("⚠️ No upcoming boss spawns found.", ephemeral=True)
            return

        # Find the earliest spawn time
        next_time = min(upcoming, key=lambda x: x[1])[1]

        # Collect all bosses spawning at that time
        next_bosses = [name for name, spawn in upcoming if spawn == next_time]

        minutes_left = max(int((next_time - now).total_seconds() // 60), 0)
        message = f"⚠️ Next boss spawn in {minutes_left} minutes at {next_time.strftime('%Y-%m-%d %H:%M UTC')}:\n"
        message += "\n".join(f"**{boss}**" for boss in next_bosses)

        await interaction.followup.send(message, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ Failed to fetch boss timers: {e}", ephemeral=True)

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


# ─── Event ───────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print("UTC now:", datetime.now(timezone.utc))
    print("Local now:", datetime.now())  # local time

    # Ensure all boss roles exist
    for guild in bot.guilds:
        await bot.ensure_boss_roles(guild)

    # Fetch next boss timers
    try:
        bosses = await fetch_bosses("NA")  # NA / EU / SEA
        now = datetime.now(timezone.utc)

        if bosses:
            # Create a dict to track next spawn per boss
            next_spawns = {}
            for boss in bosses:
                spawn_utc = parse_time_str_to_utc(boss["time_str"])
                if spawn_utc < now:
                    continue  # skip past spawns

                # Only keep the earliest spawn per boss
                if boss["name"] not in next_spawns or spawn_utc < next_spawns[boss["name"]]:
                    next_spawns[boss["name"]] = spawn_utc

            if next_spawns:
                print("🕒 Next boss timers:")
                for name, spawn in sorted(next_spawns.items(), key=lambda x: x[1]):
                    minutes_left = max(int((spawn - now).total_seconds() // 60), 0)
                    print(f" - {name}: spawns at {spawn} ({minutes_left} min left)")
            else:
                print("⚠️ No upcoming boss spawns found.")
        else:
            print("⚠️ No boss data available.")
    except Exception as e:
        print("⚠️ Failed to fetch boss timers:", e)

    # Start the polling loop only if it's not already running
    if not poll_and_alert.is_running():
        poll_and_alert.start()


# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
        print("⚠️ Please set your bot token before running.")
    else:
        bot.run(DISCORD_TOKEN)
