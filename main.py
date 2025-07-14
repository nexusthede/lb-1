import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import json
from datetime import datetime

# If you use keep_alive.py to keep your bot alive on some hosting, else remove these two lines
from keep_alive import keep_alive
keep_alive()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# SQLite database connection and setup
conn = sqlite3.connect("stats.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS user_stats (
    user_id TEXT PRIMARY KEY,
    messages INTEGER DEFAULT 0,
    voice_seconds INTEGER DEFAULT 0
)''')
c.execute('''CREATE TABLE IF NOT EXISTS settings (
    guild_id TEXT,
    key TEXT,
    value TEXT,
    PRIMARY KEY (guild_id, key)
)''')
conn.commit()

LEADERBOARD_FILE = "leaderboard_ids.json"
leaderboard_data = {}

def save_leaderboard_data():
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(leaderboard_data, f)

async def load_leaderboard_data():
    global leaderboard_data
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r") as f:
                leaderboard_data = json.load(f)
            print("✅ Loaded leaderboard message data.")
        except Exception as e:
            print(f"Failed to load leaderboard data: {e}")

# Admin-only check decorator for slash commands
def is_guild_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        member = interaction.user
        return member.guild_permissions.administrator
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Streaming(name="I love nexus so much", url="https://twitch.tv/nexus"))
    await load_leaderboard_data()
    update_leaderboards.start()
    try:
        await bot.tree.sync()
        print("Slash commands synced!")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    user_id = str(message.author.id)
    c.execute("SELECT * FROM user_stats WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE user_stats SET messages = messages + 1 WHERE user_id = ?", (user_id,))
    else:
        c.execute("INSERT INTO user_stats (user_id, messages, voice_seconds) VALUES (?, 1, 0)", (user_id,))
    conn.commit()
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    uid = str(member.id)
    key = f"{member.guild.id}-{uid}"
    if not hasattr(bot, 'join_times'):
        bot.join_times = {}
    join_times = bot.join_times

    if not before.channel and after.channel:
        join_times[key] = discord.utils.utcnow()
    elif before.channel and not after.channel and key in join_times:
        seconds = (discord.utils.utcnow() - join_times[key]).total_seconds()
        del join_times[key]
        row = c.execute("SELECT * FROM user_stats WHERE user_id = ?", (uid,)).fetchone()
        if row:
            c.execute("UPDATE user_stats SET voice_seconds = voice_seconds + ? WHERE user_id = ?", (int(seconds), uid))
        else:
            c.execute("INSERT INTO user_stats (user_id, messages, voice_seconds) VALUES (?, 0, ?)", (uid, int(seconds)))
        conn.commit()

# /set group for setting leaderboard channels
class SetCommands(app_commands.Group):
    def __init__(self):
        super().__init__(name="set", description="Set leaderboard channels")

    @app_commands.command(name="vc", description="Set the voice leaderboard channel")
    @app_commands.describe(channel="The text channel to post voice leaderboard")
    @is_guild_admin()
    async def vc(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild.id)
        c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, 'voice_channel', ?)", (guild_id, channel.id))
        conn.commit()
        await interaction.response.send_message(f"✅ Voice leaderboard will be posted in {channel.mention}", ephemeral=True)

    @app_commands.command(name="chat", description="Set the chat leaderboard channel")
    @app_commands.describe(channel="The text channel to post chat leaderboard")
    @is_guild_admin()
    async def chat(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild.id)
        c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, 'message_channel', ?)", (guild_id, channel.id))
        conn.commit()
        await interaction.response.send_message(f"✅ Chat leaderboard will be posted in {channel.mention}", ephemeral=True)

bot.tree.add_command(SetCommands())

# /show group for showing leaderboards
class ShowCommands(app_commands.Group):
    def __init__(self):
        super().__init__(name="show", description="Show commands")

    @app_commands.command(name="lbs", description="Post the leaderboards")
    @is_guild_admin()
    async def lbs(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'message_channel'", (guild_id,))
        msg = c.fetchone()
        c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'voice_channel'", (guild_id,))
        vc = c.fetchone()

        if not msg or not vc:
            await interaction.response.send_message("❌ Please run `/set chat` and `/set vc` first in this server.", ephemeral=True)
            return

        try:
            message_channel_id = int(msg[0])
            voice_channel_id = int(vc[0])
            msg_channel = bot.get_channel(message_channel_id)
            vc_channel = bot.get_channel(voice_channel_id)
            if msg_channel is None or vc_channel is None:
                await interaction.response.send_message("❌ One or both leaderboard channels not found or inaccessible.", ephemeral=True)
                return
        except Exception:
            await interaction.response.send_message("❌ Invalid channel IDs. Please reset them.", ephemeral=True)
            return

        top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 30").fetchall()
        top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 30").fetchall()

        guild = interaction.guild

        now = datetime.now()
        footer_time = now.strftime("Today at %-I:%M %p")
        footer_text = f"Updates every 10 minutes • {footer_time}"

        msg_embed = discord.Embed(
            title="Messages Leaderboard",
            description=await format_leaderboard(top_msg, False, guild),
            color=0xFFB6C1
        )
        msg_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        msg_embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        msg_embed.set_footer(text=footer_text)

        vc_embed = discord.Embed(
            title="Voice Leaderboard",
            description=await format_leaderboard(top_vc, True, guild),
            color=0xFFB6C1
        )
        vc_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        vc_embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        vc_embed.set_footer(text=footer_text)

        msg_msg = await msg_channel.send(embed=msg_embed)
        vc_msg = await vc_channel.send(embed=vc_embed)

        leaderboard_data[guild_id] = {
            "msg_id": msg_msg.id,
            "msg_channel": msg_channel.id,
            "vc_id": vc_msg.id,
            "vc_channel": vc_channel.id
        }
        save_leaderboard_data()

        await interaction.response.send_message("✅ Leaderboards posted and will auto-update every 10 minutes.", ephemeral=True)

bot.tree.add_command(ShowCommands())

# /update command to manually update leaderboards
@bot.tree.command(name="update", description="Manually update the leaderboards (Admin only)")
@is_guild_admin()
async def update(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    if guild_id not in leaderboard_data:
        await interaction.response.send_message("❌ No leaderboard messages found for this server. Use `/show lbs` first.", ephemeral=True)
        return
    try:
        await update_now_for_guild(guild_id)
        await interaction.response.send_message("✅ Leaderboards updated manually.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to update leaderboards: {e}", ephemeral=True)

# Background task for auto updating leaderboards every 10 minutes
@tasks.loop(minutes=10)
async def update_leaderboards():
    for guild_id in list(leaderboard_data.keys()):
        try:
            await update_now_for_guild(guild_id)
        except Exception as e:
            print(f"Failed to update leaderboard for guild {guild_id}: {e}")

async def update_now_for_guild(guild_id):
    if guild_id not in leaderboard_data:
        return
    data = leaderboard_data[guild_id]

    guild = bot.get_guild(int(guild_id))
    if guild is None:
        print(f"Guild {guild_id} not found.")
        return

    msg_channel = bot.get_channel(data["msg_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])

    if msg_channel is None or vc_channel is None:
        print(f"Channels for guild {guild_id} not found.")
        return

    try:
        msg_msg = await msg_channel.fetch_message(data["msg_id"])
        vc_msg = await vc_channel.fetch_message(data["vc_id"])
    except discord.NotFound:
        # Leaderboard messages deleted, repost embeds
        top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 30").fetchall()
        top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 30").fetchall()

        now = datetime.now()
        footer_time = now.strftime("Today at %-I:%M %p")
        footer_text = f"Updates every 10 minutes • {footer_time}"

        msg_embed = discord.Embed(
            title="Messages Leaderboard",
            description=await format_leaderboard(top_msg, False, guild),
            color=0xFFB6C1
        )
        msg_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        msg_embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        msg_embed.set_footer(text=footer_text)

        vc_embed = discord.Embed(
            title="Voice Leaderboard",
            description=await format_leaderboard(top_vc, True, guild),
            color=0xFFB6C1
        )
        vc_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        vc_embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        vc_embed.set_footer(text=footer_text)

        msg_msg = await msg_channel.send(embed=msg_embed)
        vc_msg = await vc_channel.send(embed=vc_embed)

        leaderboard_data[guild_id]["msg_id"] = msg_msg.id
        leaderboard_data[guild_id]["vc_id"] = vc_msg.id
        save_leaderboard_data()
        return

    # Normal update of existing leaderboard messages
    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 30").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 30").fetchall()

    now = datetime.now()
    footer_time = now.strftime("Today at %-I:%M %p")
    footer_text = f"Updates every 10 minutes • {footer_time}"

    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, guild),
        color=0xFFB6C1
    )
    msg_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    msg_embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    msg_embed.set_footer(text=footer_text)

    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, guild),
        color=0xFFB6C1
    )
    vc_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    vc_embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    vc_embed.set_footer(text=footer_text)

    await msg_msg.edit(embed=msg_embed)
    await vc_msg.edit(embed=vc_embed)

# Helper: Format voice time nicely (days, hours, minutes, seconds)
def format_voice_time(seconds):
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if d > 0:
        parts.append(f"{d}d")
    if h > 0:
        parts.append(f"{h}h")
    if m > 0:
        parts.append(f"{m}m")
    if s > 0 or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)

# Format leaderboard description skipping bots and left members, showing top 10 active users only
async def format_leaderboard(users, is_voice, guild):
    rank_emojis = [f":lb_{i}:" for i in range(1, 11)]  # Custom emojis :lb_1: to :lb_10:
    lines = []
    count = 0

    for u in users:
        if count >= 10:
            break
        user_id = int(u[0])
        try:
            member = guild.get_member(user_id)
            if member is None:
                member = await guild.fetch_member(user_id)
        except discord.NotFound:
            continue  # User left server, skip
        except Exception:
            continue

        if member.bot:
            continue  # Skip bots

        messages = u[1]
        voice_seconds = u[2]

        value = format_voice_time(voice_seconds) if is_voice else f"{messages} messages"

        rank = rank_emojis[count] if count < 10 else f"#{count + 1}"
        lines.append(f"{rank} {member.mention} • {value}")
        count += 1

    return "\n".join(lines) if lines else "No data yet!"

# Run the bot
bot.run(os.getenv("TOKEN"))
