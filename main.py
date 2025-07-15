from keep_alive import keep_alive
keep_alive()

import discord
from discord.ext import commands, tasks
import sqlite3
import os
import json
from datetime import datetime

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

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
        try:
            with open(LEADERBOARD_FILE, "r") as f:
                leaderboard_data = json.load(f)
        except:
            pass

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    await bot.change_presence(activity=discord.Game(name="your daily distraction"))
    await load_leaderboard_data()
    update_leaderboards.start()

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    uid = str(message.author.id)
    gid = str(message.guild.id)
    c.execute("SELECT * FROM user_stats WHERE user_id = ? AND guild_id = ?", (uid, gid))
    if c.fetchone():
        c.execute("UPDATE user_stats SET messages = messages + 1 WHERE user_id = ? AND guild_id = ?", (uid, gid))
    else:
        c.execute("INSERT INTO user_stats (user_id, guild_id, messages, voice_seconds) VALUES (?, ?, 1, 0)", (uid, gid))
    conn.commit()
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    uid = str(member.id)
    gid = str(member.guild.id)
    key = f"{gid}-{uid}"
    if not hasattr(bot, 'join_times'):
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

@bot.command()
@commands.has_permissions(administrator=True)
async def set(ctx, mode: str, channel: discord.TextChannel):
    gid = str(ctx.guild.id)
    key = 'message_channel' if mode == "messages" else 'voice_channel' if mode == "voice" else None
    if key is None:
        return await ctx.send("❌ Usage: `!set messages #channel` or `!set voice #channel`")
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)", (gid, key, channel.id))
    conn.commit()
    await ctx.send(f"✅ Set {mode} leaderboard to {channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def postlbs(ctx):
    gid = str(ctx.guild.id)
    msg_id = c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'message_channel'", (gid,)).fetchone()
    vc_id = c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'voice_channel'", (gid,)).fetchone()
    if not msg_id or not vc_id:
        return await ctx.send("❌ Run `!set messages #channel` and `!set voice #channel` first.")

    msg_channel = bot.get_channel(int(msg_id[0]))
    vc_channel = bot.get_channel(int(vc_id[0]))

    top_msg = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (gid,)).fetchall()
    top_vc = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (gid,)).fetchall()

    msg_embed = await build_embed("Messages Leaderboard", top_msg, False, ctx.guild)
    vc_embed = await build_embed("Voice Leaderboard", top_vc, True, ctx.guild)

    msg_msg = await msg_channel.send(embed=msg_embed)
    vc_msg = await vc_channel.send(embed=vc_embed)

    leaderboard_data[gid] = {
        "msg_id": msg_msg.id,
        "msg_channel": msg_channel.id,
        "vc_id": vc_msg.id,
        "vc_channel": vc_channel.id
    }
    save_leaderboard_data()
    await ctx.send("✅ Leaderboards posted and will update every 10 mins.")

@bot.command()
@commands.has_permissions(administrator=True)
async def update(ctx):
    await update_now_for_guild(str(ctx.guild.id))
    await ctx.send("✅ Leaderboards updated manually.")

@tasks.loop(minutes=10)
async def update_leaderboards():
    for gid in leaderboard_data:
        await update_now_for_guild(gid)

async def update_now_for_guild(gid):
    data = leaderboard_data.get(gid)
    if not data:
        return
    guild = bot.get_guild(int(gid))
    msg_channel = bot.get_channel(data["msg_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])

    top_msg = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (gid,)).fetchall()
    top_vc = c.execute("SELECT * FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (gid,)).fetchall()

    try:
        msg_msg = await msg_channel.fetch_message(data["msg_id"])
        vc_msg = await vc_channel.fetch_message(data["vc_id"])
        await msg_msg.edit(embed=await build_embed("Messages Leaderboard", top_msg, False, guild))
        await vc_msg.edit(embed=await build_embed("Voice Leaderboard", top_vc, True, guild))
    except:
        pass

def format_voice(seconds):
    m = int(seconds // 60)
    h = m // 60
    m = m % 60
    return f"{h}h {m}m"

async def build_embed(title, data, is_voice, guild):
    emojis = [
        "<:lb_1:1394342323944689724>", "<:lb_2:1394342387974668461>", "<:lb_3:1394342423232123091>",
        "<:lb_4:1394342457801703425>", "<:lb_5:1394342504895353106>", "<:lb_6:1394342517964669138>",
        "<:lb_7:1394342533567483925>", "<:lb_8:1394342550587965542>", "<:lb_9:1394342569877700658>",
        "<:lb_10:1394342586025513112>"
    ]
    lines = []
    for i, u in enumerate(data):
        member = guild.get_member(int(u[0])) or await guild.fetch_member(int(u[0]), default=None)
        if member and not member.bot:
            val = format_voice(u[3]) if is_voice else f"{u[2]} messages"
            lines.append(f"{emojis[i]} {member.mention} - {val}")
    description = "\n".join(lines) if lines else "No data yet!"
    embed = discord.Embed(title=title, description=description, color=discord.Color.from_rgb(255, 182, 193))
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.set_image(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed.set_footer(text=f"Updates every 10 mins | Today at {datetime.now().strftime('%I:%M %p')}")
    return embed

bot.run(os.getenv("TOKEN"))
