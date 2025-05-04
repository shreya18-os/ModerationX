import discord
from discord.ext import commands, tasks
import os
import sqlite3
import asyncio
import aiohttp
import requests
import time
import re
from discord.ui import Button, View
from datetime import datetime, timedelta

# Bot setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="&", intents=intents)

# Database setup
def db_connect():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS punishments (
                user_id INTEGER,
                punishment TEXT,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS warnings (
                user_id INTEGER,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    return conn, c

# Logging and Punishments
def log_punishment(user_id, punishment, reason):
    conn, c = db_connect()
    c.execute("INSERT INTO punishments (user_id, punishment, reason) VALUES (?, ?, ?)",
              (user_id, punishment, reason))
    conn.commit()
    conn.close()

def log_warning(user_id, reason):
    conn, c = db_connect()
    c.execute("INSERT INTO warnings (user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()

# Warning threshold check
def should_auto_punish(user_id):
    conn, c = db_connect()
    c.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

async def check_for_auto_ban_or_kick(user, channel=None):
    count = should_auto_punish(user.id)
    if count >= 5:
        try:
            await user.ban(reason="Exceeded warning limit")
            log_punishment(user.id, "Ban", "Exceeded warning limit")
            if channel:
                await channel.send(f"{user.mention} was banned for exceeding warning limit.")
        except discord.Forbidden:
            pass
    elif count >= 3:
        try:
            await user.kick(reason="Exceeded warning limit")
            log_punishment(user.id, "Kick", "Exceeded warning limit")
            if channel:
                await channel.send(f"{user.mention} was kicked for exceeding warning limit.")
        except discord.Forbidden:
            pass

# Message Filter & Spam
blacklist = ["badword1", "badword2", "badword3"]
user_message_times = {}

async def check_spam(message):
    now = time.time()
    times = user_message_times.get(message.author.id, [])
    times = [t for t in times if now - t < 10]  # last 10 seconds
    times.append(now)
    user_message_times[message.author.id] = times
    if len(times) > 5:
        await message.delete()
        await message.channel.send(f"{message.author.mention}, please stop spamming!")
        log_warning(message.author.id, "Spam")
        await check_for_auto_ban_or_kick(message.author, message.channel)

# Bot Whitelist
WHITELIST_FILE = "whitelist.txt"

def load_whitelist():
    if not os.path.exists(WHITELIST_FILE):
        return set()
    with open(WHITELIST_FILE) as f:
        return set(line.strip() for line in f)

def save_to_whitelist(bot_id):
    with open(WHITELIST_FILE, "a") as f:
        f.write(f"{bot_id}\n")

def remove_from_whitelist(bot_id):
    if not os.path.exists(WHITELIST_FILE):
        return
    lines = open(WHITELIST_FILE).read().splitlines()
    with open(WHITELIST_FILE, "w") as f:
        for line in lines:
            if line.strip() != str(bot_id):
                f.write(line + "\n")

# Unified on_message
@bot.event
async def on_message(message):
    # Unwhitelisted bot ping abuse
    if message.author.bot:
        wl = load_whitelist()
        if str(message.author.id) not in wl and ("@everyone" in message.content or "@here" in message.content):
            try:
                await message.guild.kick(message.author, reason="Unwhitelisted bot ping abuse.")
                await message.channel.send(f"🚨 `{message.author}` was kicked for mass ping.")
            except discord.Forbidden:
                pass
        return

    # Inappropriate language
    if any(word in message.content.lower() for word in blacklist):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, inappropriate language detected.")
        log_warning(message.author.id, "Inappropriate language")
        await check_for_auto_ban_or_kick(message.author, message.channel)
        return

    # Invite links
    if message.guild and "discord.gg/" in message.content.lower():
        await message.delete()
        await message.channel.send(f"{message.author.mention}, invite links are not allowed.")
        log_warning(message.author.id, "Invite link")
        await check_for_auto_ban_or_kick(message.author, message.channel)
        return

    # Spam check
    if message.guild and message.content:
        await check_spam(message)

    await bot.process_commands(message)

# Bot join: kick unwhitelisted bots
@bot.event
async def on_member_join(member):
    if member.bot:
        wl = load_whitelist()
        if str(member.id) not in wl:
            try:
                await member.kick(reason="Unwhitelisted bot.")
                await member.guild.system_channel.send(
                    f"{member} kicked; use `&whitelistbot {member.id}` to allow."
                )
                log_punishment(member.id, "Bot Kick", "Unwhitelisted bot")
            except discord.Forbidden:
                await member.guild.system_channel.send("⚠️ Cannot kick bot.")

# Whitelist commands
@bot.command(name="whitelistbot")
@commands.has_permissions(administrator=True)
async def whitelist_bot(ctx, bot_id: int):
    save_to_whitelist(bot_id)
    await ctx.send(f"✅ Whitelisted bot `{bot_id}`.")

@bot.command(name="removewl")
@commands.has_permissions(administrator=True)
async def remove_wl(ctx, bot_id: int):
    remove_from_whitelist(bot_id)
    await ctx.send(f"🗑️ Removed bot `{bot_id}` from whitelist.")

# Audit log checks
async def check_audit(guild, action):
    await asyncio.sleep(1)
    entry = None
    async for e in guild.audit_logs(limit=1, action=action):
        entry = e
        break
    if entry and entry.user.bot:
        wl = load_whitelist()
        if str(entry.user.id) not in wl:
            try:
                await guild.kick(entry.user, reason=f"Unauthorized {action.name}")
                if guild.system_channel:
                    await guild.system_channel.send(
                        f"🚫 `{entry.user}` kicked for unauthorized {action.name}."
                    )
            except discord.Forbidden:
                pass

@bot.event
async def on_guild_channel_create(channel):
    await check_audit(channel.guild, discord.AuditLogAction.channel_create)

@bot.event
async def on_guild_channel_delete(channel):
    await check_audit(channel.guild, discord.AuditLogAction.channel_delete)

@bot.event
async def on_guild_update(before, after):
    if before.name != after.name:
        await check_audit(after, discord.AuditLogAction.guild_update)

# Moderation commands
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick(ctx, user: discord.Member, *, reason="No reason provided"):
    try:
        await user.send(f"You were kicked from {ctx.guild.name} for: {reason}")
    except discord.Forbidden:
        pass
    await user.kick(reason=reason)
    log_punishment(user.id, "Kick", reason)
    await ctx.send(f"✅ {user.mention} kicked: {reason}")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, user: discord.Member, *, reason="No reason provided"):
    try:
        await user.send(f"You were banned from {ctx.guild.name} for: {reason}")
    except discord.Forbidden:
        pass
    await user.ban(reason=reason)
    log_punishment(user.id, "Ban", reason)
    await ctx.send(f"✅ {user.mention} banned: {reason}")

@bot.command(name="timeout")
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, user: discord.Member, duration: str, *, reason="No reason provided"):
    match = re.match(r"^(\d+)(s|sec|m|min|h|hr|d|day)$", duration.lower())
    if not match:
        return await ctx.send("❌ Invalid duration. Use e.g. `10s`, `5min`, `2h`, or `1d`.")
    val, unit = int(match[1]), match[2]
    delta = timedelta(seconds=val) if unit in ('s','sec') else \
            timedelta(minutes=val) if unit in ('m','min') else \
            timedelta(hours=val) if unit in ('h','hr') else \
            timedelta(days=val)
    until = discord.utils.utcnow() + delta
    try:
        await user.timeout(until, reason=reason)
        log_punishment(user.id, f"Timeout {duration}", reason)
        await ctx.send(f"✅ {user.mention} timed out for {duration}: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ Cannot timeout this member.")

@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, user: discord.Member, *, reason="No reason provided"):
    if user.is_timed_out():
        try:
            await user.timeout(None, reason=reason)
            log_punishment(user.id, "Unmute", reason)
            await ctx.send(f"✅ {user.mention} unmuted: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ Cannot unmute this member.")
    else:
        await ctx.send("❌ Member is not timed out.")

@bot.command(name="emoji")
async def emoji(ctx):
    button = Button(label="Click Me!", emoji="😊")
    async def cb(inter):
        await inter.response.send_message("You clicked the button!")
    button.callback = cb
    view = View()
    view.add_item(button)
    await ctx.send("Here’s a button!", view=view)

# Status updater\@tasks.loop(seconds=60)
async def update_status():
    total = sum(len(g.members) for g in bot.guilds)
    await bot.change_presence(activity=discord.Game(name=f"Protecting {total} members"), status=discord.Status.online)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Game(name="The Best Auto-Moderation Bot"), status=discord.Status.online)
    update_status.start()

# Run bot
token = os.getenv('DISCORD_TOKEN')
if not token:
    raise RuntimeError("DISCORD_TOKEN not set in environment variables")
bot.run(token)
