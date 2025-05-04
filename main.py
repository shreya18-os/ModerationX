import discord
from discord.ext import commands, tasks
import sqlite3
import os
from discord.ui import Button, View
from discord.ext import commands
import aiohttp
import requests
import time
import re
from datetime import timedelta

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.bans = True
intents.messages = True
intents.typing = False
intents.presences = False
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
    c.execute("INSERT INTO punishments (user_id, punishment, reason) VALUES (?, ?, ?)", (user_id, punishment, reason))
    conn.commit()
    conn.close()

def log_warning(user_id, reason):
    conn, c = db_connect()
    c.execute("INSERT INTO warnings (user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()

# Message Filter (Inappropriate Language)
blacklist = ["badword1", "badword2", "badword3"]  # Add more bad words

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    if any(word in message.content.lower() for word in blacklist):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, your message contains inappropriate language!")
        log_warning(message.author.id, "Inappropriate language")
        await check_for_auto_ban_or_kick(message.author)

    if message.guild:
        # Anti-Spam (Existing feature)
        if message.content and len(message.content) > 0:
            await check_spam(message)

        # Anti-Invite Links
        if "discord.gg/" in message.content.lower():
            await message.delete()
            await message.channel.send(f"{message.author.mention}, posting invites is not allowed.")
            log_warning(message.author.id, "Posting invite links")
            await check_for_auto_ban_or_kick(message.author)

    await bot.process_commands(message)

async def check_spam(message):
    user = message.author
    current_time = time.time()

    conn, c = db_connect()
    c.execute("SELECT timestamp FROM punishments WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5", (user.id,))
    recent_msgs = c.fetchall()

    if len(recent_msgs) > 4:
        first_time = time.mktime(time.strptime(recent_msgs[-1][0], '%Y-%m-%d %H:%M:%S'))
        if current_time - first_time < 30:  # 30 seconds
            await message.delete()
            await message.channel.send(f"{user.mention}, please slow down. You're spamming.")
            log_punishment(user.id, "Spam", "Spamming messages in a short period.")
    conn.close()


# Anti-Bot Protection (Existing feature)
WHITELIST_FILE = "whitelist.txt"

# Load whitelist from file
def load_whitelist():
    if not os.path.exists(WHITELIST_FILE):
        return set()
    with open(WHITELIST_FILE, "r") as f:
        return set(line.strip() for line in f.readlines())

# Save a new bot ID to the whitelist
def save_to_whitelist(bot_id):
    with open(WHITELIST_FILE, "a") as f:
        f.write(f"{bot_id}\n")

# Remove a bot ID from the whitelist
def remove_from_whitelist(bot_id):
    if not os.path.exists(WHITELIST_FILE):
        return
    with open(WHITELIST_FILE, "r") as f:
        lines = f.readlines()
    with open(WHITELIST_FILE, "w") as f:
        for line in lines:
            if line.strip() != str(bot_id):
                f.write(line)

# Kick unwhitelisted bot on join
@bot.event
async def on_member_join(member):
    if member.bot:
        whitelist = load_whitelist()
        if str(member.id) not in whitelist:
            try:
                await member.kick(reason="Unwhitelisted bot.")
                await member.guild.system_channel.send(
                    f"{member} was kicked for being a bot. To allow bots, use `&whitelistbot <bot_id>`."
                )
                log_punishment(member.id, "Bot Kick", "Unwhitelisted bot tried to join.")
            except discord.Forbidden:
                await member.guild.system_channel.send("⚠️ I don’t have permission to kick bots.")

# Command to add to whitelist
@bot.command(name="whitelistbot")
@commands.has_permissions(administrator=True)
async def whitelist_bot(ctx, bot_id: int):
    save_to_whitelist(str(bot_id))
    await ctx.send(f"✅ Bot with ID `{bot_id}` has been whitelisted.")

# Command to remove from whitelist
@bot.command(name="removewl")
@commands.has_permissions(administrator=True)
async def remove_whitelist(ctx, bot_id: int):
    remove_from_whitelist(bot_id)
    await ctx.send(f"🗑️ Bot with ID `{bot_id}` has been removed from the whitelist.")

# Security actions: check audit logs
async def check_audit(guild, action_type):
    async for entry in guild.audit_logs(limit=1, action=action_type):
        if entry.user.bot:
            whitelist = load_whitelist()
            if str(entry.user.id) not in whitelist:
                try:
                    await guild.kick(entry.user, reason=f"Unauthorized bot {action_type.name}.")
                    if guild.system_channel:
                        await guild.system_channel.send(
                            f"🚫 `{entry.user}` was kicked for unauthorized {action_type.name}."
                        )
                except discord.Forbidden:
                    pass

@bot.event
async def on_guild_channel_create(channel):
    await asyncio.sleep(1)
    await check_audit(channel.guild, discord.AuditLogAction.channel_create)

@bot.event
async def on_guild_channel_delete(channel):
    await asyncio.sleep(1)
    await check_audit(channel.guild, discord.AuditLogAction.channel_delete)

@bot.event
async def on_guild_update(before, after):
    if before.name != after.name:
        await asyncio.sleep(1)
        await check_audit(after, discord.AuditLogAction.guild_update)

# Detect @everyone/@here ping by unwhitelisted bots
@bot.event
async def on_message(message):
    if message.author.bot:
        whitelist = load_whitelist()
        if str(message.author.id) not in whitelist and ("@everyone" in message.content or "@here" in message.content):
            try:
                await message.guild.kick(message.author, reason="Unwhitelisted bot ping abuse.")
                await message.channel.send(f"🚨 `{message.author}` was kicked for using mass ping without whitelist.")
            except discord.Forbidden:
                pass
    await bot.process_commands(message)

# Anti-Nuke Protection (Existing feature)

import asyncio
import discord
from discord.ext import commands

# … your existing bot setup …

@bot.event
async def on_guild_channel_delete(channel):
    # give Discord a moment to populate the audit log
    await asyncio.sleep(1)

    guild = channel.guild
    # fetch the most recent channel-delete audit log entry
    entry = None
    async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        entry = e
        break

    if not entry:
        return  # couldn’t find who deleted it

    deleter = entry.user
    # don’t punish the bot itself or server owner
    if deleter == bot.user or deleter == guild.owner:
        return

    # optionally skip admins or whitelisted roles
    if deleter.guild_permissions.administrator:
        return

    # kick the user who deleted the channel
    try:
        await guild.kick(deleter, reason=f"Deleted channel #{channel.name}")
        log_punishment(deleter.id, "Kick", f"Deleted channel {channel.name}")
        # notify in the system channel (or specify your mod‑log channel)
        if guild.system_channel:
            await guild.system_channel.send(
                f"🔨 {deleter.mention} was kicked for deleting channel **#{channel.name}**."
            )
    except discord.Forbidden:
        # bot lacks permission to kick
        if guild.system_channel:
            await guild.system_channel.send(
                f"⚠️ Tried to kick {deleter.mention} for deleting a channel, but lacked permissions."
            )

@bot.event
async def on_guild_channel_create(channel):
    # If too many channels are created in a short time, consider it as a nuke attempt
    conn, c = db_connect()
    c.execute("SELECT timestamp FROM punishments WHERE user_id = ? ORDER BY timestamp DESC LIMIT 3", (channel.guild.owner.id,))
    recent_activities = c.fetchall()

    if len(recent_activities) > 2:
        first_time = time.mktime(time.strptime(recent_activities[-1][0], '%Y-%m-%d %H:%M:%S'))
        if time.time() - first_time < 600:  # 10 minutes
            await channel.guild.system_channel.send(f"{channel.guild.owner.mention} is attempting to nuke the server!")
            log_punishment(channel.guild.owner.id, "Nuke Attempt", "Possible nuke attempt detected.")
    conn.close()

# Check if a user has crossed a threshold for banning/kicking
async def check_for_auto_ban_or_kick(user):
    conn, c = db_connect()
    c.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user.id,))
    warning_count = c.fetchone()[0]

    if warning_count >= 3:
        # Auto-Kick or Auto-Ban
        if warning_count == 3:
            await user.kick(reason="Exceeded warning limit")
            log_punishment(user.id, "Kick", "Exceeded warning limit")
            await user.guild.system_channel.send(f"{user} was kicked for exceeding the warning limit.")
        elif warning_count >= 5:
            await user.ban(reason="Exceeded warning limit")
            log_punishment(user.id, "Ban", "Exceeded warning limit")
            await user.guild.system_channel.send(f"{user} was banned for exceeding the warning limit.")
    conn.close()

# Kick/Ban commands (Existing feature)

@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, user: discord.Member, *, reason: str = "No reason provided"):
    # Check if the member is timed out
    if user.is_timed_out():
        try:
            # Remove the timeout
            await user.timeout(None, reason=reason)
            log_punishment(user.id, "Unmute", reason)
            await ctx.send(f"✅ {user.mention} has been unmuted.\n📝 Reason: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ I lack permission to unmute that member.")
        except Exception as e:
            await ctx.send(f"⚠️ An error occurred: `{e}`")
    else:
        await ctx.send("❌ This user is not currently muted.")


@bot.command(name="timeout")
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, user: discord.Member, duration: str, *, reason: str = "No reason provided"):
    # 1) Parse the duration (e.g. "5min", "2h", "1d")
    match = re.match(r'^(\d+)(s|sec|m|min|h|hr|d|day)$', duration.lower())
    if not match:
        return await ctx.send("❌ Invalid duration. Use formats like `10s`, `5min`, `2h`, or `1d`.")
    
    value, unit = int(match[1]), match[2]
    if unit in ('s', 'sec'):
        delta = timedelta(seconds=value)
    elif unit in ('m', 'min'):
        delta = timedelta(minutes=value)
    elif unit in ('h', 'hr'):
        delta = timedelta(hours=value)
    else:  # 'd' or 'day'
        delta = timedelta(days=value)

    until = discord.utils.utcnow() + delta

    try:
        # Only one positional argument (until); reason as keyword-only
        await user.timeout(until, reason=reason)
        log_punishment(user.id, f"Timeout for {duration}", reason)
        await ctx.send(f"✅ {user.mention} has been timed out for **{duration}**.\n📝 Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I lack permission to timeout that member.")
    except Exception as e:
        await ctx.send(f"⚠️ An error occurred: `{e}`")


@bot.command(name="kick")
@commands.has_permissions(administrator=True)
async def kick(ctx, user: discord.Member, *, reason="No reason provided"):
    try:
        await user.send(f"You were kicked from **{ctx.guild.name}** for: {reason}")
    except discord.Forbidden:
        pass  # Can't send DM

    await user.kick(reason=reason)
    log_punishment(user.id, "Kick", reason)

    embed = discord.Embed(title="User Kicked", color=discord.Color.orange())
    embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="ban")
@commands.has_permissions(administrator=True)
async def ban(ctx, user: discord.Member, *, reason="No reason provided"):
    try:
        await user.send(f"You were banned from **{ctx.guild.name}** for: {reason}")
    except discord.Forbidden:
        pass  # Can't send DM

    await user.ban(reason=reason)
    log_punishment(user.id, "Ban", reason)

    embed = discord.Embed(title="User Banned", color=discord.Color.red())
    embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.avatar.url)
    await ctx.send(embed=embed)


# Custom Emoji Button for UI (Existing feature)
@bot.command(name="emoji")
async def emoji(ctx):
    button = Button(label="Click Me!", emoji="😊")

    async def button_callback(interaction):
        await interaction.response.send_message("You clicked the button!")
    
    button.callback = button_callback

    view = View()
    view.add_item(button)
    await ctx.send("Here’s a button for you!", view=view)

# Update bot status to show member protection count and custom playing status (Existing feature)
@tasks.loop(seconds=60)
async def update_status():
    total_members = 0
    for guild in bot.guilds:
        total_members += len(guild.members)
    
    await bot.change_presence(activity=discord.Game(name="The Best Auto-Moderation Bot"), status=discord.Status.online)
    await bot.change_presence(activity=discord.Game(name=f"Protecting {total_members} members"))

# Start the task when the bot is ready (Existing feature)
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    update_status.start()  # Start updating the bot's status

# Run the bot
bot.run(os.getenv('DISCORD_TOKEN'))
