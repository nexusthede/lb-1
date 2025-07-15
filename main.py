from keep_alive import keep_alive
keep_alive()

import discord
from discord.ext import tasks
import sqlite3
import os
import json
from datetime import datetime

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

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
        except Exception:
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
    if member.bot or not member.guild:
        return
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

def is_admin(interaction):
    return interaction.user.guild_permissions.administrator

@bot.slash_command(name="set", description="Set leaderboard channels (vc or chat)")
@discord.default_permissions(administrator=True)
async def set_channel(
    interaction: discord.Interaction,
    mode: discord.Option(str, "Choose 'vc' or 'chat'"),
    channel: discord.Option(discord.TextChannel, "Channel to post leaderboard")
):
    if mode not in ["vc", "chat"]:
        return await interaction.response.send_message("❌ Mode must be 'vc' or 'chat'", ephemeral=True)
    guild_id = str(interaction.guild.id)
    c.execute("INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)", (guild_id, f"{mode}_channel", str(channel.id)))
    conn.commit()
    await interaction.response.send_message(f"✅ Leaderboard channel for **{mode}** set to {channel.mention}", ephemeral=True)

@bot.slash_command(name="show", description="Post the message and voice leaderboards")
@discord.default_permissions(administrator=True)
async def show_lbs(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'chat_channel'", (guild_id,))
    chat = c.fetchone()
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'vc_channel'", (guild_id,))
    vc = c.fetchone()
    if not chat or not vc:
        return await interaction.response.send_message("❌ Please set both chat and vc leaderboard channels first using /set", ephemeral=True)
    chat_channel = bot.get_channel(int(chat[0]))
    vc_channel = bot.get_channel(int(vc[0]))
    if not chat_channel or not vc_channel:
        return await interaction.response.send_message("❌ One or both leaderboard channels not found or inaccessible", ephemeral=True)

    # Fetch top 10 users per leaderboard for this guild only
    top_msgs = c.execute("SELECT user_id, messages FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (guild_id,)).fetchall()
    top_voice = c.execute("SELECT user_id, voice_seconds FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (guild_id,)).fetchall()

    now = datetime.now()
    footer_text = f"Updates every 10 mins | Today at {now.strftime('%I:%M %p')}"

    # Custom emojis for ranks
    emojis = [
        "<:lb_1:1394342323944689724>",
        "<:lb_2:1394342387974668461>",
        "<:lb_3:1394342423232123091>",
        "<:lb_4:1394342457801703425>",
        "<:lb_5:1394342504895353106>",
        "<:lb_6:1394342517964669138>",
        "<:lb_7:1394342533567483925>",
        "<:lb_8:1394342550587965542>",
        "<:lb_9:1394342569877700658>",
        "<:lb_10:1394342586025513112>",
    ]

    def format_voice(seconds):
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"

    async def format_leaderboard(data, is_voice):
        lines = []
        for i, (user_id, val) in enumerate(data):
            user = interaction.guild.get_member(int(user_id))
            if not user or user.bot:
                continue
            val_str = format_voice(val) if is_voice else f"{val} msgs"
            lines.append(f"{emojis[i]} - {user.mention} - {val_str}")
        return "\n".join(lines) if lines else "No data yet!"

    msg_desc = await format_leaderboard(top_msgs, False)
    vc_desc = await format_leaderboard(top_voice, True)

    embed_msgs = discord.Embed(title="Messages Leaderboard", description=msg_desc, color=0xFFB6C1)
    embed_vc = discord.Embed(title="Voice Leaderboard", description=vc_desc, color=0xFFB6C1)
    embed_msgs.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    embed_vc.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    embed_msgs.set_thumbnail(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed_vc.set_thumbnail(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed_msgs.set_footer(text=footer_text)
    embed_vc.set_footer(text=footer_text)

    msg_sent = await chat_channel.send(embed=embed_msgs)
    vc_sent = await vc_channel.send(embed=embed_vc)

    leaderboard_data[guild_id] = {
        "chat_channel": chat_channel.id,
        "chat_msg": msg_sent.id,
        "vc_channel": vc_channel.id,
        "vc_msg": vc_sent.id
    }
    save_leaderboard_data()
    await interaction.response.send_message("✅ Leaderboards posted and will update every 10 minutes", ephemeral=True)

@bot.slash_command(name="update", description="Manually update leaderboards")
@discord.default_permissions(administrator=True)
async def update_lbs(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    if guild_id not in leaderboard_data:
        return await interaction.response.send_message("❌ No leaderboards posted yet. Use /show first.", ephemeral=True)
    await update_now_for_guild(guild_id)
    await interaction.response.send_message("✅ Leaderboards updated", ephemeral=True)

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
    if not guild:
        return

    chat_channel = bot.get_channel(data["chat_channel"])
    vc_channel = bot.get_channel(data["vc_channel"])

    if not chat_channel or not vc_channel:
        return

    try:
        chat_msg = await chat_channel.fetch_message(data["chat_msg"])
        vc_msg = await vc_channel.fetch_message(data["vc_msg"])
    except discord.NotFound:
        # If messages deleted, repost
        await show_lbs_for_guild(guild)
        return

    c.execute("SELECT user_id, messages FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (guild_id,))
    top_msgs = c.fetchall()
    c.execute("SELECT user_id, voice_seconds FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (guild_id,))
    top_voice = c.fetchall()

    now = datetime.now()
    footer_text = f"Updates every 10 mins | Today at {now.strftime('%I:%M %p')}"

    emojis = [
        "<:lb_1:1394342323944689724>",
        "<:lb_2:1394342387974668461>",
        "<:lb_3:1394342423232123091>",
        "<:lb_4:1394342457801703425>",
        "<:lb_5:1394342504895353106>",
        "<:lb_6:1394342517964669138>",
        "<:lb_7:1394342533567483925>",
        "<:lb_8:1394342550587965542>",
        "<:lb_9:1394342569877700658>",
        "<:lb_10:1394342586025513112>",
    ]

    def format_voice(seconds):
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"

    async def format_leaderboard(data, is_voice):
        lines = []
        for i, (user_id, val) in enumerate(data):
            user = guild.get_member(int(user_id))
            if not user or user.bot:
                continue
            val_str = format_voice(val) if is_voice else f"{val} msgs"
            lines.append(f"{emojis[i]} - {user.mention} - {val_str}")
        return "\n".join(lines) if lines else "No data yet!"

    msg_desc = await format_leaderboard(top_msgs, False)
    vc_desc = await format_leaderboard(top_voice, True)

    embed_msgs = discord.Embed(title="Messages Leaderboard", description=msg_desc, color=0xFFB6C1)
    embed_vc = discord.Embed(title="Voice Leaderboard", description=vc_desc, color=0xFFB6C1)
    embed_msgs.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed_vc.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed_msgs.set_thumbnail(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed_vc.set_thumbnail(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed_msgs.set_footer(text=footer_text)
    embed_vc.set_footer(text=footer_text)

    await chat_msg.edit(embed=embed_msgs)
    await vc_msg.edit(embed=embed_vc)

async def show_lbs_for_guild(guild):
    guild_id = str(guild.id)
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'chat_channel'", (guild_id,))
    chat = c.fetchone()
    c.execute("SELECT value FROM settings WHERE guild_id = ? AND key = 'vc_channel'", (guild_id,))
    vc = c.fetchone()
    if not chat or not vc:
        return
    chat_channel = bot.get_channel(int(chat[0]))
    vc_channel = bot.get_channel(int(vc[0]))
    if not chat_channel or not vc_channel:
        return

    c.execute("SELECT user_id, messages FROM user_stats WHERE guild_id = ? ORDER BY messages DESC LIMIT 10", (guild_id,))
    top_msgs = c.fetchall()
    c.execute("SELECT user_id, voice_seconds FROM user_stats WHERE guild_id = ? ORDER BY voice_seconds DESC LIMIT 10", (guild_id,))
    top_voice = c.fetchall()

    now = datetime.now()
    footer_text = f"Updates every 10 mins | Today at {now.strftime('%I:%M %p')}"

    emojis = [
        "<:lb_1:1394342323944689724>",
        "<:lb_2:1394342387974668461>",
        "<:lb_3:1394342423232123091>",
        "<:lb_4:1394342457801703425>",
        "<:lb_5:1394342504895353106>",
        "<:lb_6:1394342517964669138>",
        "<:lb_7:1394342533567483925>",
        "<:lb_8:1394342550587965542>",
        "<:lb_9:1394342569877700658>",
        "<:lb_10:1394342586025513112>",
    ]

    def format_voice(seconds):
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"

    async def format_leaderboard(data, is_voice):
        lines = []
        for i, (user_id, val) in enumerate(data):
            user = guild.get_member(int(user_id))
            if not user or user.bot:
                continue
            val_str = format_voice(val) if is_voice else f"{val} msgs"
            lines.append(f"{emojis[i]} - {user.mention} - {val_str}")
        return "\n".join(lines) if lines else "No data yet!"

    msg_desc = await format_leaderboard(top_msgs, False)
    vc_desc = await format_leaderboard(top_voice, True)

    embed_msgs = discord.Embed(title="Messages Leaderboard", description=msg_desc, color=0xFFB6C1)
    embed_vc = discord.Embed(title="Voice Leaderboard", description=vc_desc, color=0xFFB6C1)
    embed_msgs.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed_vc.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed_msgs.set_thumbnail(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed_vc.set_thumbnail(url="https://cdn.discordapp.com/attachments/1394342054552801352/1394586696418590770/27.gif")
    embed_msgs.set_footer(text=footer_text)
    embed_vc.set_footer(text=footer_text)

    msg_sent = await chat_channel.send(embed=embed_msgs)
    vc_sent = await vc_channel.send(embed=embed_vc)

    leaderboard_data[guild_id] = {
        "chat_channel": chat_channel.id,
        "chat_msg": msg_sent.id,
        "vc_channel": vc_channel.id,
        "vc_msg": vc_sent.id
    }
    save_leaderboard_data()

bot.run(os.getenv("TOKEN"))
