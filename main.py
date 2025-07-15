import os
import discord
from discord.ext import commands, tasks
from flask import Flask
import sqlite3
import json
from datetime import datetime, timezone

# Flask keep_alive for Render
app = Flask('')
@app.route('/')
def home():
    return "Leaderboard bot is online!"
def keep_alive():
    from threading import Thread
    Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 8080}).start()

# Bot setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

conn = sqlite3.connect('stats.db')
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
    try:
        with open(LEADERBOARD_FILE, "w") as f:
            json.dump(leaderboard_data, f)
    except Exception as e:
        print(f"Error saving leaderboard data: {e}")

async def load_leaderboard_data():
    global leaderboard_data
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r") as f:
                leaderboard_data = json.load(f)
            print("✅ Loaded leaderboard message data.")
        except Exception as e:
            print(f"Failed to load leaderboard data: {e}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Tracking messages & voice"))
    await load_leaderboard_data()
    update_leaderboards.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    uid = str(message.author.id)
    c.execute("SELECT * FROM user_stats WHERE user_id = ?", (uid,))
    if c.fetchone():
        c.execute("UPDATE user_stats SET messages = messages + 1 WHERE user_id = ?", (uid,))
    else:
        c.execute("INSERT INTO user_stats (user_id, messages, voice_seconds) VALUES (?, 1, 0)", (uid,))
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

# Admin check
def is_admin():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return discord.app_commands.check(predicate)

# Slash command: /set
@bot.tree.command(name="set")
@discord.app_commands.describe(mode="Choose leaderboard type", channel="Channel to post leaderboard in")
@is_admin()
async def set_cmd(interaction: discord.Interaction, mode: str, channel: discord.TextChannel):
    mode = mode.lower()
    if mode not in ["vc", "chat"]:
        await interaction.response.send_message("❌ Use 'vc' or 'chat' only.", ephemeral=True)
        return
    key = "voice_channel" if mode == "vc" else "message_channel"
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)", (str(interaction.guild.id), key, str(channel.id)))
    conn.commit()
    await interaction.response.send_message(f"✅ {mode.upper()} leaderboard set to {channel.mention}", ephemeral=True)

# Slash command: /show
@bot.tree.command(name="show")
@is_admin()
async def show_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    gid = str(guild.id)

    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'message_channel'", (gid,))
    msg = c.fetchone()
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'voice_channel'", (gid,))
    vc = c.fetchone()

    if not msg or not vc:
        await interaction.response.send_message("❌ Set both chat and VC leaderboard channels first using `/set`.", ephemeral=True)
        return

    msg_channel = bot.get_channel(int(msg[0]))
    vc_channel = bot.get_channel(int(vc[0]))

    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 20").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 20").fetchall()

    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, guild),
        color=discord.Color.default()
    )
    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, guild),
        color=discord.Color.default()
    )
    img = "https://cdn.discordapp.com/attachments/860528686403158046/1108384769147932682/ezgif-2-f41b6758ff.gif"
    msg_embed.set_image(url=img)
    vc_embed.set_image(url=img)
    if guild.icon:
        msg_embed.set_author(name=guild.name, icon_url=guild.icon.url)
        vc_embed.set_author(name=guild.name, icon_url=guild.icon.url)
        msg_embed.set_thumbnail(url=guild.icon.url)
        vc_embed.set_thumbnail(url=guild.icon.url)
    now = datetime.now(timezone.utc)
    msg_embed.timestamp = now
    vc_embed.timestamp = now
    msg_embed.set_footer(text="Updates every 10 minutes")
    vc_embed.set_footer(text="Updates every 10 minutes")

    msg_msg = await msg_channel.send(embed=msg_embed)
    vc_msg = await vc_channel.send(embed=vc_embed)

    leaderboard_data[gid] = {
        "msg_id": msg_msg.id,
        "msg_channel": msg_channel.id,
        "vc_id": vc_msg.id,
        "vc_channel": vc_channel.id
    }
    save_leaderboard_data()
    await interaction.response.send_message("✅ Leaderboards posted and will auto-update every 10 minutes.", ephemeral=True)

# Slash command: /update
@bot.tree.command(name="update")
@is_admin()
async def update_cmd(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    if gid not in leaderboard_data:
        await interaction.response.send_message("❌ No leaderboard found. Use `/show` first.", ephemeral=True)
        return
    await update_now_for_guild(gid)
    await interaction.response.send_message("✅ Leaderboards updated manually.", ephemeral=True)

# Loop for auto update
@tasks.loop(minutes=10)
async def update_leaderboards():
    for gid in list(leaderboard_data.keys()):
        try:
            await update_now_for_guild(gid)
        except Exception as e:
            print(f"Update failed for {gid}: {e}")

async def update_now_for_guild(gid):
    if gid not in leaderboard_data:
        return
    data = leaderboard_data[gid]
    guild = bot.get_guild(int(gid))
    if not guild:
        return

    msg_channel = bot.get_channel(data["msg_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])

    msg_msg = None
    vc_msg = None
    try:
        msg_msg = await msg_channel.fetch_message(data["msg_id"])
    except:
        pass
    try:
        vc_msg = await vc_channel.fetch_message(data["vc_id"])
    except:
        pass

    # Get fresh Top 20, filter and fill later to ensure 10 valid entries
    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 20").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 20").fetchall()

    # Format embeds
    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, guild),
        color=discord.Color.default()
    )
    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, guild),
        color=discord.Color.default()
    )
    img = "https://cdn.discordapp.com/attachments/860528686403158046/1108384769147932682/ezgif-2-f41b6758ff.gif"
    msg_embed.set_image(url=img)
    vc_embed.set_image(url=img)
    if guild.icon:
        msg_embed.set_author(name=guild.name, icon_url=guild.icon.url)
        vc_embed.set_author(name=guild.name, icon_url=guild.icon.url)
        msg_embed.set_thumbnail(url=guild.icon.url)
        vc_embed.set_thumbnail(url=guild.icon.url)
    now = datetime.now(timezone.utc)
    msg_embed.timestamp = now
    vc_embed.timestamp = now
    msg_embed.set_footer(text="Updates every 10 minutes")
    vc_embed.set_footer(text="Updates every 10 minutes")

    # Edit or repost messages if missing
    if msg_msg:
        await msg_msg.edit(embed=msg_embed)
    else:
        m = await msg_channel.send(embed=msg_embed)
        leaderboard_data[gid]["msg_id"] = m.id
        save_leaderboard_data()

    if vc_msg:
        await vc_msg.edit(embed=vc_embed)
    else:
        v = await vc_channel.send(embed=vc_embed)
        leaderboard_data[gid]["vc_id"] = v.id
        save_leaderboard_data()

def format_voice_time(seconds):
    h = seconds // 3600
    if h >= 1:
        return f"**{h} hour(s)**"
    m = seconds // 60
    return f"**{m} min(s)**"

async def format_leaderboard(users, is_voice, guild):
    medals = [
        '<:lb_1:1394342323944689724>', '<:lb_2:1394342387974668461>',
        '<:lb_3:1394342423232123091>', '<:lb_4:1394342457801703425>',
        '<:lb_5:1394342504895353106>', '<:lb_6:1394342517964669138>',
        '<:lb_7:1394342533567483925>', '<:lb_8:1394342550587965542>',
        '<:lb_9:1394342569877700658>', '<:lb_10:1394342586025513112>'
    ]
    lines = []
    rank = 0

    for u in users:
        if rank >= 10:
            break
        user_id = int(u[0])
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except:
                continue
        if not member or member.bot:
            continue
        value = format_voice_time(u[2]) if is_voice else f"**{u[1]} message(s)**"
        lines.append(f"{medals[rank]} {member.mention} - {value}")
        rank += 1

    return "\n".join(lines) if lines else "No data yet!"

keep_alive()
bot.run(os.getenv("TOKEN"))
