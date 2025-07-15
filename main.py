import os
import discord
import sqlite3
import json
from datetime import datetime
from discord.ext import commands, tasks
from discord import app_commands
from keep_alive import keep_alive
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

conn = sqlite3.connect('stats.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS user_stats (
    user_id TEXT,
    guild_id TEXT,
    messages INTEGER DEFAULT 0,
    voice_seconds INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, guild_id)
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
        with open(LEADERBOARD_FILE, "r") as f:
            leaderboard_data = json.load(f)

@bot.event
async def on_ready():
    await load_leaderboard_data()
    await tree.sync()
    update_leaderboards.start()
    await bot.change_presence(activity=discord.Game(name="your daily distraction"))
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    uid, gid = str(message.author.id), str(message.guild.id)
    c.execute("SELECT * FROM user_stats WHERE user_id = ? AND guild_id = ?", (uid, gid))
    if c.fetchone():
        c.execute("UPDATE user_stats SET messages = messages + 1 WHERE user_id = ? AND guild_id = ?", (uid, gid))
    else:
        c.execute("INSERT INTO user_stats (user_id, guild_id, messages, voice_seconds) VALUES (?, ?, 1, 0)", (uid, gid))
    conn.commit()
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if not member.guild or member.bot:
        return
    uid, gid = str(member.id), str(member.guild.id)
    key = f"{gid}-{uid}"
    if not hasattr(bot, "join_times"):
        bot.join_times = {}
    join_times = bot.join_times
    if not before.channel and after.channel:
        join_times[key] = discord.utils.utcnow()
    elif before.channel and not after.channel and key in join_times:
        seconds = (discord.utils.utcnow() - join_times[key]).total_seconds()
        del join_times[key]
        c.execute("SELECT * FROM user_stats WHERE user_id = ? AND guild_id = ?", (uid, gid))
        if c.fetchone():
            c.execute("UPDATE user_stats SET voice_seconds = voice_seconds + ? WHERE user_id = ? AND guild_id = ?", (int(seconds), uid, gid))
        else:
            c.execute("INSERT INTO user_stats (user_id, guild_id, messages, voice_seconds) VALUES (?, ?, 0, ?)", (uid, gid, int(seconds)))
        conn.commit()

def format_voice_time(seconds):
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if d > 0: parts.append(f"{d}d")
    if h > 0 or d > 0: parts.append(f"{h}h")
    if m > 0 or h > 0 or d > 0: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return ' '.join(parts)

async def format_leaderboard(users, is_voice, guild):
    medals = [
        "<:lb_1:1394342323944689724>", "<:lb_2:1394342387974668461>", "<:lb_3:1394342423232123091>",
        "<:lb_4:1394342457801703425>", "<:lb_5:1394342504895353106>", "<:lb_6:1394342517964669138>",
        "<:lb_7:1394342533567483925>", "<:lb_8:1394342550587965542>", "<:lb_9:1394342569877700658>",
        "<:lb_10:1394342586025513112>"
    ]
    lines = []
    for i, u in enumerate(users):
        user_id = int(u[0])
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except:
            continue
        if member and not member.bot:
            value = format_voice_time(u[3]) if is_voice else f"{u[2]} messages"
            lines.append(f"{medals[i]} {member.mention} - {value}")
    return "\n".join(lines) if lines else "No data yet!"

def build_embed(title, desc, guild):
    embed = discord.Embed(
        title=title,
        description=desc,
        color=discord.Color.from_rgb(255, 182, 193)
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.set_image(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed.set_footer(text=f"Updates every 10 mins | Today at {datetime.now().strftime('%I:%M %p')}")
    return embed

@tree.command(name="set_chat", description="Set the messages leaderboard channel.")
@app_commands.checks.has_permissions(administrator=True)
async def set_chat(interaction: discord.Interaction, channel: discord.TextChannel):
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, 'message_channel', ?)", (str(interaction.guild.id), channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Messages leaderboard will be posted in {channel.mention}", ephemeral=True)

@tree.command(name="set_vc", description="Set the voice leaderboard channel.")
@app_commands.checks.has_permissions(administrator=True)
async def set_vc(interaction: discord.Interaction, channel: discord.TextChannel):
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, 'voice_channel', ?)", (str(interaction.guild.id), channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Voice leaderboard will be posted in {channel.mention}", ephemeral=True)

@tree.command(name="postlbs", description="Post both leaderboards.")
@app_commands.checks.has_permissions(administrator=True)
async def postlbs(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    msg_id = c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'message_channel'", (gid,)).fetchone()
    vc_id = c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'voice_channel'", (gid,)).fetchone()
    if not msg_id or not vc_id:
        return await interaction.response.send_message("❌ Set both channels first with /set_chat and /set_vc", ephemeral=True)

    msg_channel = interaction.guild.get_channel(int(msg_id[0]))
    vc_channel = interaction.guild.get_channel(int(vc_id[0]))
    top_msg = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (gid,)).fetchall()
    top_vc = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (gid,)).fetchall()

    msg_embed = build_embed("Messages Leaderboard", await format_leaderboard(top_msg, False, interaction.guild), interaction.guild)
    vc_embed = build_embed("Voice Leaderboard", await format_leaderboard(top_vc, True, interaction.guild), interaction.guild)

    msg_msg = await msg_channel.send(embed=msg_embed)
    vc_msg = await vc_channel.send(embed=vc_embed)

    leaderboard_data[gid] = {
        "msg_id": msg_msg.id,
        "msg_channel": msg_channel.id,
        "vc_id": vc_msg.id,
        "vc_channel": vc_channel.id
    }
    save_leaderboard_data()
    await interaction.response.send_message("✅ Leaderboards posted and will auto-update.", ephemeral=True)

@tree.command(name="update", description="Manually refresh both leaderboards.")
@app_commands.checks.has_permissions(administrator=True)
async def update(interaction: discord.Interaction):
    await update_now_for_guild(str(interaction.guild.id))
    await interaction.response.send_message("✅ Leaderboards updated.", ephemeral=True)

@tasks.loop(minutes=10)
async def update_leaderboards():
    for gid in leaderboard_data:
        await update_now_for_guild(gid)

async def update_now_for_guild(gid):
    if gid not in leaderboard_data:
        return
    data = leaderboard_data[gid]
    guild = bot.get_guild(int(gid))
    if not guild:
        return
    msg_channel = bot.get_channel(data["msg_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])

    try:
        msg_msg = await msg_channel.fetch_message(data["msg_id"])
        vc_msg = await vc_channel.fetch_message(data["vc_id"])
    except:
        return

    top_msg = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (gid,)).fetchall()
    top_vc = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (gid,)).fetchall()

    msg_embed = build_embed("Messages Leaderboard", await format_leaderboard(top_msg, False, guild), guild)
    vc_embed = build_embed("Voice Leaderboard", await format_leaderboard(top_vc, True, guild), guild)

    await msg_msg.edit(embed=msg_embed)
    await vc_msg.edit(embed=vc_embed)

keep_alive()
bot.run(TOKEN)
