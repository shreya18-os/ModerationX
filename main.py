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
from discord import app_commands

# Bot setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="&", intents=intents)
tree = bot.tree  # Needed for app_commands

# Database setup
def db_connect():
    conn = sqlite3.connect('database.db')  # Connect to SQLite database
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS punishments (
                user_id INTEGER,
                punishment TEXT,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')  # Create punishments table if it doesn't exist
    c.execute('''CREATE TABLE IF NOT EXISTS warnings (
                user_id INTEGER,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')  # Create warnings table if it doesn't exist
    conn.commit()  # Commit changes
    return conn, c  # Return connection and cursor


# Initialize SQLite
conn, c = db_connect()
c.execute('''
    CREATE TABLE IF NOT EXISTS whitelist (
        guild_id INTEGER,
        bot_id INTEGER
    )
''')
conn.commit()
conn.close()


# Slash command version of help
@tree.command(name="help", description="View the help menu")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 ModerationX Help Menu", color=discord.Color.blue())
    
    # Correct fallback for thumbnail
    if interaction.client.user.avatar:
        embed.set_thumbnail(url=interaction.client.user.avatar.url)

    # Add help categories and commands
    embed.add_field(name="Moderation", value="`kick`, `ban`, `timeout`, `unmute`", inline=False)
    embed.add_field(name="Whitelist", value="`whitelistbot`, `removewl`", inline=False)

    embed.set_footer(text="Use /<command> to run a command.")
    await interaction.response.send_message(embed=embed, ephemeral=True)




# Logging and Punishments
def log_punishment(user_id, punishment, reason):
    try:
        conn, c = db_connect()
        c.execute("INSERT INTO punishments (user_id, punishment, reason) VALUES (?, ?, ?)", 
                  (user_id, punishment, reason))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        # Handle the error or log it for debugging

def log_warning(user_id, reason):
    conn, c = db_connect()  # Establish connection
    c.execute("INSERT INTO warnings (user_id, reason) VALUES (?, ?)", 
              (user_id, reason))  # Insert warning record
    conn.commit()  # Commit changes
    conn.close()  # Close connection


# Warning threshold check
def should_auto_punish(user_id):
    conn, c = db_connect()  # Establish connection
    c.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user_id,))  # Query the number of warnings
    count = c.fetchone()[0]  # Fetch the result
    conn.close()  # Close connection
    return count  # Return the count of warnings

def load_whitelist():
    conn, c = db_connect()
    c.execute("SELECT bot_id FROM whitelist")
    whitelisted_bots = [str(row[0]) for row in c.fetchall()]  # convert to str
    conn.close()
    return whitelisted_bots



async def check_for_auto_ban_or_kick(user, channel=None):
    count = should_auto_punish(user.id)  # Get the warning count
    print(f"[DEBUG] {user} has {count} warnings")  # ✅ Helps trace issues
    if count >= 5:  # If the user has 5 or more warnings
        try:
            await user.ban(reason="Exceeded warning limit")  # Ban the user
            log_punishment(user.id, "Ban", "Exceeded warning limit")  # Log the punishment
            if channel:
                await channel.send(f"{user.mention} was banned for exceeding warning limit.")  # Inform the channel
        except discord.Forbidden:
            pass
    elif count >= 3:  # If the user has 3 or more warnings but less than 5
        try:
            await user.kick(reason="Exceeded warning limit")  # Kick the user
            log_punishment(user.id, "Kick", "Exceeded warning limit")  # Log the punishment
            if channel:
                await channel.send(f"{user.mention} was kicked for exceeding warning limit.")  # Inform the channel
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


@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int):
    try:
        user = await bot.fetch_user(user_id)  # Fetch the user using their ID
        await ctx.guild.unban(user)  # Unban the user from the server
        await ctx.send(f"✅ {user} has been unbanned from the server.")
    except discord.NotFound:
        await ctx.send("❌ No user found with that ID.")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to unban members.")
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred while trying to unban the user: {e}")


# Bot Whitelist
# Add bot to server-specific whitelist
def add_to_whitelist(guild_id: int, bot_id: int):
    conn, c = db_connect()
    with conn:
        c.execute("INSERT OR IGNORE INTO whitelist (guild_id, bot_id) VALUES (?, ?)", (guild_id, bot_id))
    conn.close()

# Remove bot from whitelist
def remove_from_whitelist(guild_id: int, bot_id: int):
    conn, c = db_connect()
    with conn:
        c.execute("DELETE FROM whitelist WHERE guild_id = ? AND bot_id = ?", (guild_id, bot_id))
    conn.close()

# Check if bot is whitelisted in this guild
def is_bot_whitelisted(guild_id: int, bot_id: int) -> bool:
    conn, c = db_connect()
    c.execute("SELECT 1 FROM whitelist WHERE guild_id = ? AND bot_id = ?", (guild_id, bot_id))
    result = c.fetchone()
    conn.close()
    return result is not None



# Whitelist bot command
@bot.command()
@commands.has_permissions(administrator=True)
async def whitelistbot(ctx, bot_id: int):
    user = await bot.fetch_user(bot_id)
    if not user.bot:
        return await ctx.send("That user is not a bot.")
    if is_bot_whitelisted(ctx.guild.id, bot_id):
        return await ctx.send("This bot is already whitelisted in this server.")
    add_to_whitelist(ctx.guild.id, bot_id)
    await ctx.send(f"{user.name} has been whitelisted in **{ctx.guild.name}**.")

# Unwhitelist bot command
@bot.command()
@commands.has_permissions(administrator=True)
async def unwhitelistbot(ctx, bot_id: int):
    user = await bot.fetch_user(bot_id)
    if not user.bot:
        return await ctx.send("That user is not a bot.")
    if not is_bot_whitelisted(ctx.guild.id, bot_id):
        return await ctx.send("This bot is not whitelisted in this server.")
    remove_from_whitelist(ctx.guild.id, bot_id)
    await ctx.send(f"{user.name} has been removed from the whitelist in **{ctx.guild.name}**.")





# REMOVE DEFAULT HELP COMMAND
bot.remove_command('help')

# Custom help command
@bot.command(name='help')
async def help_command(ctx):
    embed = discord.Embed(
        title="🛡️ ModerationX Help Center",
        description="Welcome to **ModerationX**, your all-in-one smart moderation assistant.\n\nUse `&<command>` to run any of the following:",
        color=discord.Color.from_rgb(44, 47, 51)
    )

    # Logo URL
    logo_url = "https://media.discordapp.net/attachments/1368586215753781269/1385574575764672532/avatars.png?ex=68569061&is=68553ee1&hm=7bf72e11f4884054941162cc24bfca2f2808fbea4ff02f8694aadcd5ee501769&=&format=webp&quality=lossless"
    embed.set_thumbnail(url=logo_url)

    # Moderation Commands
    embed.add_field(
        name="🔨 Moderation Tools",
        value=(
            "`&kick <user> [reason]` — Kick a user\n"
            "`&ban <user> [reason]` — Ban a user\n"
            "`&timeout <user> <duration>` — Temporarily mute a user\n"
            "`&unmute <user>` — Remove timeout"
        ),
        inline=False
    )

    # Whitelist Commands
    embed.add_field(
        name="⚙️ Bot Whitelist",
        value=(
            "`&whitelistbot <bot_id>` — Whitelist a bot\n"
            "`&unwhitelistbot <bot_id>` — Remove a bot from whitelist"
        ),
        inline=False
    )

    # General Commands
    embed.add_field(
        name="🧰 General Commands",
        value=(
            "`&help` — Show this help menu\n"
            "`&emoji` — Send an emoji button"
        ),
        inline=False
    )

    # Footer
    embed.set_footer(
        text="ModerationX • Smart. Secure. Swift.",
        icon_url=logo_url
    )

    await ctx.send(embed=embed)


# Unified on_message
# Keep only one on_message function to prevent conflicts
@bot.event
async def on_message(message):
    if message.author.bot:
        wl = load_whitelist()
        if str(message.author.id) not in wl and ("@everyone" in message.content or "@here" in message.content):
            try:
                await message.guild.kick(message.author, reason="Unwhitelisted bot ping abuse.")
                await message.channel.send(f"🚨 `{message.author}` was kicked for mass ping.")
            except discord.Forbidden:
                pass
        await bot.process_commands(message)  # Make sure to process commands from messages
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
async def kick(ctx, user: discord.Member = None, *, reason="No reason provided"):
    if user is None:
        return await ctx.send("⚠️ Please mention a user to kick.")

    if user == ctx.author:
        return await ctx.send("❌ You cannot kick yourself.")

    if user.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        return await ctx.send("❌ You can't kick someone with an equal or higher role than you.")

    if user.top_role >= ctx.guild.me.top_role:
        return await ctx.send("❌ I can't kick someone with a higher or equal role than mine.")

    try:
        await user.send(f"You were kicked from **{ctx.guild.name}** for: **{reason}**")
    except discord.Forbidden:
        pass  # Cannot send DM

    await user.kick(reason=reason)
    log_punishment(user.id, "Kick", reason)
    await ctx.send(f"✅ {user.mention} has been kicked. Reason: **{reason}**")

@kick.error
async def kick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to kick members.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please mention a user to kick. Example: `.kick @user reason`")
    else:
        await ctx.send(f"⚠️ An error occurred: `{str(error)}`")


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


@bot.event
async def on_ready():
    await bot.tree.sync()  # Make sure to sync the slash commands
    print(f"✅ Logged in as {bot.user} and synced commands.")


    
    # Start the background task loop
    if not update_status.is_running():
        update_status.start()
    
    print("🔧 ModerationX is now monitoring servers.")

# Background task to update bot status every minute
@tasks.loop(minutes=1)
async def update_status():
    total_members = sum(g.member_count for g in bot.guilds)
    await bot.change_presence(activity=discord.Game(name=f"Protecting {total_members} members"), status=discord.Status.online)

# Run the bot
token = os.getenv('DISCORD_TOKEN')
if not token:
    raise RuntimeError("DISCORD_TOKEN not set in environment variables")
bot.run(token)

