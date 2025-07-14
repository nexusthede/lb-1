from keep_alive import keep_alive
keep_alive()

import discord
from discord.ext import tasks
from discord import app_commands
import sqlite3
import os
import json
from datetime import datetime

intents = discord.Intents.all()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

conn = sqlite3.connect('stats.db')
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS user_stats (
    user_id TEXT PRIMARY KEY,
    messages INTEGER DEFAULT 0,
    voice_seconds INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS settings (
    guild_id TEXT,
    key TEXT,
    value TEXT,
    PRIMARY KEY (guild_id, key)
)""")
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
    await bot.change_presence(activity=discord.Game(name="ur daily distraction"))
    await load_leaderboard_data()
    for guild in bot.guilds:
        await tree.sync(guild=guild)
    update_leaderboards.start()
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    user_id = str(message.author.id)
    c.execute("SELECT * FROM user_stats WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute("UPDATE user_stats SET messages = messages + 1 WHERE user_id = ?", (user_id,))
    else:
        c.execute("INSERT INTO user_stats (user_id, messages, voice_seconds) VALUES (?, 1, 0)", (user_id,))
    conn.commit()

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

@tree.command(name="set", description="Set chat or vc leaderboard channel")
@app_commands.describe(type="Choose 'chat' or 'vc'", channel="Channel to post leaderboard in")
@app_commands.choices(type=[
    app_commands.Choice(name="chat", value="chat"),
    app_commands.Choice(name="vc", value="vc")
])
async def set_channel(interaction: discord.Interaction, type: app_commands.Choice[str], channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)", (
        str(interaction.guild.id),
        "message_channel" if type.value == "chat" else "voice_channel",
        str(channel.id)
    ))
    conn.commit()
    await interaction.response.send_message(f"✅ {type.name.title()} leaderboard set to {channel.mention}", ephemeral=True)

@tree.command(name="show", description="Post both leaderboards")
async def show_lbs(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)

    guild_id = str(interaction.guild.id)
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'message_channel'", (guild_id,))
    msg = c.fetchone()
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'voice_channel'", (guild_id,))
    vc = c.fetchone()

    if not msg or not vc:
        return await interaction.response.send_message("❌ Please set both channels with `/set chat` and `/set vc`.", ephemeral=True)

    msg_channel = bot.get_channel(int(msg[0]))
    vc_channel = bot.get_channel(int(vc[0]))
    if not msg_channel or not vc_channel:
        return await interaction.response.send_message("❌ One or both channels are invalid.", ephemeral=True)

    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC").fetchall()

    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, interaction.guild),
        color=discord.Color.from_rgb(255, 182, 193)
    )
    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, interaction.guild),
        color=discord.Color.from_rgb(255, 182, 193)
    )

    now = datetime.now().strftime("%b %d • %I:%M %p")
    msg_embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    vc_embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    msg_embed.set_footer(text=f"Updated • {now}")
    vc_embed.set_footer(text=f"Updated • {now}")

    msg_msg = await msg_channel.send(embed=msg_embed)
    vc_msg = await vc_channel.send(embed=vc_embed)

    leaderboard_data[guild_id] = {
        "msg_id": msg_msg.id,
        "msg_channel": msg_channel.id,
        "vc_id": vc_msg.id,
        "vc_channel": vc_channel.id
    }
    save_leaderboard_data()

    await interaction.response.send_message("✅ Leaderboards posted.", ephemeral=True)

@tree.command(name="update", description="Manually update leaderboards")
async def update_lb(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
    guild_id = str(interaction.guild.id)
    if guild_id not in leaderboard_data:
        return await interaction.response.send_message("❌ Use `/show` first to post the leaderboard.", ephemeral=True)
    await update_now_for_guild(guild_id)
    await interaction.response.send_message("✅ Leaderboards updated.", ephemeral=True)

@tasks.loop(minutes=10)
async def update_leaderboards():
    for gid in list(leaderboard_data.keys()):
        try:
            await update_now_for_guild(gid)
        except Exception as e:
            print(f"Failed to update guild {gid}: {e}")

async def update_now_for_guild(guild_id):
    if guild_id not in leaderboard_data:
        return
    data = leaderboard_data[guild_id]
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return
    msg_channel = bot.get_channel(data["msg_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])
    if not msg_channel or not vc_channel:
        return
    try:
        msg_msg = await msg_channel.fetch_message(data["msg_id"])
        vc_msg = await vc_channel.fetch_message(data["vc_id"])
    except discord.NotFound:
        return

    top_msg = c.execute("SELECT * FROM user_stats ORDER BY messages DESC").fetchall()
    top_vc = c.execute("SELECT * FROM user_stats ORDER BY voice_seconds DESC").fetchall()

    msg_embed = discord.Embed(
        title="Messages Leaderboard",
        description=await format_leaderboard(top_msg, False, guild),
        color=discord.Color.from_rgb(255, 182, 193)
    )
    vc_embed = discord.Embed(
        title="Voice Leaderboard",
        description=await format_leaderboard(top_vc, True, guild),
        color=discord.Color.from_rgb(255, 182, 193)
    )

    now = datetime.now().strftime("%b %d • %I:%M %p")
    msg_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    vc_embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    msg_embed.set_footer(text=f"Updated • {now}")
    vc_embed.set_footer(text=f"Updated • {now}")

    await msg_msg.edit(embed=msg_embed)
    await vc_msg.edit(embed=vc_embed)

async def format_leaderboard(users, is_voice, guild):
    rank_emojis = [
        "<:lb_1:1394342323944689724>",
        "<:lb_2:1394342387974668461>",
        "<:lb_3:1394342423232123091>",
        "<:lb_4:1394342457801703425>",
        "<:lb_5:1394342504895353106>",
        "<:lb_6:1394342517964669138>",
        "<:lb_7:1394342533567483925>",
        "<:lb_8:1394342550587965542>",
        "<:lb_9:1394342569877700658>",
        "<:lb_10:1394342586025513112>"
    ]
    lines = []
    count = 0
    for i, u in enumerate(users):
        user_id = int(u[0])
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        if member.bot:
            continue
        value = format_voice_time(u[2]) if is_voice else f"{u[1]} messages"
        emoji = rank_emojis[count] if count < len(rank_emojis) else f"#{count + 1}"
        lines.append(f"{emoji} {member.mention} • {value}")
        count += 1
        if count == 10:
            break
    return "\n".join(lines) if lines else "No data yet!"

def format_voice_time(seconds):
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{d}d {h}h {m}m {s}s"

bot.run(os.getenv("TOKEN"))
