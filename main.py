import os
import discord
from discord.ext import commands, tasks
import sqlite3
import json
from datetime import datetime, timezone
from threading import Thread
from flask import Flask

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
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(leaderboard_data, f)

async def load_leaderboard_data():
    global leaderboard_data
    if os.path.exists(LEADERBOARD_FILE):
        with open(LEADERBOARD_FILE, "r") as f:
            leaderboard_data = json.load(f)

# Flask to stay alive
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is online!"
def run_flask(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
Thread(target=run_flask).start()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Game(name="Your daily distraction"))
    await load_leaderboard_data()
    update_leaderboards.start()
    try: await bot.tree.sync()
    except: pass

@bot.event
async def on_message(message):
    if message.author.bot: return
    uid = str(message.author.id)
    c.execute("INSERT OR IGNORE INTO user_stats (user_id, messages, voice_seconds) VALUES (?, 0, 0)", (uid,))
    c.execute("UPDATE user_stats SET messages = messages + 1 WHERE user_id = ?", (uid,))
    conn.commit()
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    uid = str(member.id)
    key = f"{member.guild.id}-{uid}"
    if not hasattr(bot, 'join_times'): bot.join_times = {}
    if after.channel and not before.channel:
        bot.join_times[key] = discord.utils.utcnow()
    elif before.channel and not after.channel and key in bot.join_times:
        seconds = (discord.utils.utcnow() - bot.join_times[key]).total_seconds()
        del bot.join_times[key]
        c.execute("INSERT OR IGNORE INTO user_stats (user_id, messages, voice_seconds) VALUES (?, 0, 0)", (uid,))
        c.execute("UPDATE user_stats SET voice_seconds = voice_seconds + ? WHERE user_id = ?", (int(seconds), uid))
        conn.commit()

def is_admin():
    async def predicate(inter): return inter.user.guild_permissions.administrator
    return discord.app_commands.check(predicate)

@bot.tree.command(name="set")
@discord.app_commands.describe(mode="Choose leaderboard type", channel="Channel to post leaderboard in")
@is_admin()
async def set_cmd(inter, mode: str, channel: discord.TextChannel):
    if mode.lower() not in ["chat", "vc"]:
        await inter.response.send_message("❌ Use `chat` or `vc`.", ephemeral=True)
        return
    key = "message_channel" if mode == "chat" else "voice_channel"
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)",
              (str(inter.guild.id), key, str(channel.id)))
    conn.commit()
    await inter.response.send_message(f"✅ Set {mode.upper()} leaderboard to {channel.mention}", ephemeral=True)

@bot.tree.command(name="show")
@is_admin()
async def show_cmd(inter):
    gid = str(inter.guild.id)
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'message_channel'", (gid,))
    msg = c.fetchone()
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'voice_channel'", (gid,))
    vc = c.fetchone()
    if not msg or not vc:
        await inter.response.send_message("❌ Set both leaderboards using `/set`.", ephemeral=True)
        return

    msg_channel = bot.get_channel(int(msg[0]))
    vc_channel = bot.get_channel(int(vc[0]))
    guild = inter.guild
    banner = "https://cdn.discordapp.com/attachments/860528686403158046/1108384769147932682/ezgif-2-f41b6758ff.gif"

    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 10").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 10").fetchall()

    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, guild),
        color=0x000000,
        timestamp=datetime.now(timezone.utc)
    )
    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, guild),
        color=0x000000,
        timestamp=datetime.now(timezone.utc)
    )
    msg_embed.set_image(url=banner)
    vc_embed.set_image(url=banner)
    msg_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    vc_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    msg_embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    vc_embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    msg_embed.set_footer(text="Updates every 10 mins")
    vc_embed.set_footer(text="Updates every 10 mins")

    msg_msg = await msg_channel.send(embed=msg_embed)
    vc_msg = await vc_channel.send(embed=vc_embed)

    leaderboard_data[gid] = {
        "msg_id": msg_msg.id,
        "msg_channel": msg_channel.id,
        "vc_id": vc_msg.id,
        "vc_channel": vc_channel.id
    }
    save_leaderboard_data()
    await inter.response.send_message("✅ Leaderboards posted.", ephemeral=True)

@bot.tree.command(name="update")
@is_admin()
async def update_cmd(inter):
    gid = str(inter.guild.id)
    if gid not in leaderboard_data:
        await inter.response.send_message("❌ Run `/show` first.", ephemeral=True)
        return
    await update_now_for_guild(gid)
    await inter.response.send_message("✅ Leaderboards updated.", ephemeral=True)

@tasks.loop(minutes=10)
async def update_leaderboards():
    for gid in list(leaderboard_data.keys()):
        try: await update_now_for_guild(gid)
        except: pass

async def update_now_for_guild(gid):
    if gid not in leaderboard_data: return
    data = leaderboard_data[gid]
    guild = bot.get_guild(int(gid))
    if guild is None: return

    msg_channel = bot.get_channel(data["msg_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])
    banner = "https://cdn.discordapp.com/attachments/860528686403158046/1108384769147932682/ezgif-2-f41b6758ff.gif"

    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC LIMIT 10").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC LIMIT 10").fetchall()

    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, guild),
        color=0x000000,
        timestamp=datetime.now(timezone.utc)
    )
    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, guild),
        color=0x000000,
        timestamp=datetime.now(timezone.utc)
    )
    msg_embed.set_image(url=banner)
    vc_embed.set_image(url=banner)
    msg_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    vc_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    msg_embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    vc_embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    msg_embed.set_footer(text="Updates every 10 mins")
    vc_embed.set_footer(text="Updates every 10 mins")

    try:
        msg_msg = await msg_channel.fetch_message(data["msg_id"])
        vc_msg = await vc_channel.fetch_message(data["vc_id"])
        await msg_msg.edit(embed=msg_embed)
        await vc_msg.edit(embed=vc_embed)
    except discord.NotFound:
        msg_msg = await msg_channel.send(embed=msg_embed)
        vc_msg = await vc_channel.send(embed=vc_embed)
        leaderboard_data[gid]["msg_id"] = msg_msg.id
        leaderboard_data[gid]["vc_id"] = vc_msg.id
        save_leaderboard_data()

async def format_leaderboard(users, is_voice, guild):
    medals = [
        '<:lb_1:1394342323944689724>', '<:lb_2:1394342387974668461>',
        '<:lb_3:1394342423232123091>', '<:lb_4:1394342457801703425>',
        '<:lb_5:1394342504895353106>', '<:lb_6:1394342517964669138>',
        '<:lb_7:1394342533567483925>', '<:lb_8:1394342550587965542>',
        '<:lb_9:1394342569877700658>', '<:lb_10:1394342586025513112>'
    ]
    lines = []
    for i, u in enumerate(users):
        try:
            member = guild.get_member(int(u[0])) or await guild.fetch_member(int(u[0]))
            if member.bot: continue
            val = format_voice(u[2]) if is_voice else f"**{u[1]}** message(s)"
            lines.append(f"{medals[i]} {member.mention} - {val}")
        except: continue
    return "\n".join(lines) if lines else "No data yet."

def format_voice(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"**{h}h {m}m {s}s**"

bot.run(os.getenv("TOKEN"))
