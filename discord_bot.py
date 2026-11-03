"""Discord bot: chat with the fantasy agent from Discord.

Message it directly (DM), @mention it, or post in its channel (channel_id in the config) and it
replies like a chat, remembering the conversation so follow-ups work. "new" starts a fresh
conversation. /ask still works, with replies only you can see. It only answers its owner.

Claude runs through Claude Code on your subscription (the same as agent.py), so questions
count against your Claude plan's usage limits rather than API billing.

Config in discord_bot.json (git-ignored), or the matching environment variables:
    {"token": "...", "user_id": "...", "guild_id": "...", "channel_id": "..."}
    DISCORD_BOT_TOKEN, DISCORD_USER_ID, DISCORD_GUILD_ID, DISCORD_CHANNEL_ID
guild_id is your private server's ID; with it, /ask shows up there right away. channel_id
(optional) is a channel where it answers every message without being @mentioned. Chatting
needs the bot's Message Content Intent on (Developer Portal -> Bot).

It logs to discord_bot.log (git-ignored), so it can run without a console window.

Usage:
    python discord_bot.py
"""
import asyncio
import json
import logging
import logging.handlers
import os
import re
import sys
import time
from collections import deque
from pathlib import Path

import discord
from discord import app_commands

import agent
import fantasy
import yahoo_api

CONFIG_FILE = Path(__file__).parent / 'discord_bot.json'
LOG_FILE = Path(__file__).parent / 'discord_bot.log'
MAX_MESSAGE = 2000
# Discord accepts follow-up replies for 15 minutes after a command
ANSWER_TIMEOUT = 14 * 60
# A conversation (one per DM or channel) remembers this many exchanges and ends after this
# long without a message. Kept in memory only, so it's gone when the bot restarts.
MEMORY_EXCHANGES = 12
MEMORY_SECONDS = 3 * 60 * 60
# Messages that start a fresh conversation
RESET_WORDS = {'new', 'reset', 'new chat', 'start over', 'clear'}


def load_config():
    config = {}
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    for key, env in (('token', 'DISCORD_BOT_TOKEN'), ('user_id', 'DISCORD_USER_ID'),
                     ('guild_id', 'DISCORD_GUILD_ID'), ('channel_id', 'DISCORD_CHANNEL_ID')):
        config[key] = config.get(key) or os.environ.get(env)
    missing = [k for k in ('token', 'user_id') if not config[k]]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} - put them in {CONFIG_FILE.name} (see the README)")
    return config


def chunks(text, size=MAX_MESSAGE):
    """Split text into Discord-sized messages, at line breaks where possible."""
    parts = []
    while len(text) > size:
        cut = text.rfind('\n', 0, size)
        cut = cut if cut > 0 else size
        parts.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return parts + [text] if text else parts


def league_choices():
    choices = []
    for league in fantasy.available_leagues():
        with open(fantasy.LEAGUES_DIR / f"{league}.json", encoding='utf-8') as f:
            name = json.load(f).get('league', {}).get('name', league)
        choices.append(app_commands.Choice(name=name, value=league))
    return choices


class FantasyBot(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.default()
        intents.message_content = True  # to read chat messages, not just /ask
        super().__init__(intents=intents)
        self.config = config
        self.owner_id = int(config['user_id'])
        self.channel_id = int(config['channel_id']) if config.get('channel_id') else None
        self.tree = app_commands.CommandTree(self)
        # The agent's tools read one shared league setting, so answer one message at a time
        self.busy = asyncio.Lock()
        self.conversations = {}  # channel id -> deque of (time, message, reply)

    def history(self, channel_id):
        """Earlier exchanges in this channel's conversation, or none if it went quiet."""
        exchanges = self.conversations.get(channel_id)
        if exchanges and time.time() - exchanges[-1][0] > MEMORY_SECONDS:
            exchanges.clear()
        return [(m, r) for _, m, r in exchanges or ()]

    def remember(self, channel_id, message, reply):
        exchanges = self.conversations.setdefault(channel_id, deque(maxlen=MEMORY_EXCHANGES))
        exchanges.append((time.time(), message, reply))

    async def reply_to(self, channel_id, message, league=None):
        """The agent's reply to `message`, in this channel's conversation."""
        try:
            async with self.busy:
                reply = await asyncio.wait_for(
                    asyncio.to_thread(answer, message, league, self.history(channel_id)),
                    ANSWER_TIMEOUT)
            self.remember(channel_id, message, reply)
        except asyncio.TimeoutError:
            reply = "That took too long - try a narrower question."
        except Exception as e:
            logging.exception("Agent failed")
            reply = f"Something went wrong: {type(e).__name__}: {e}"
        if yahoo_api.available():  # required wherever Yahoo data is shown
            reply += f"\n-# {yahoo_api.ATTRIBUTION}"
        return reply

    async def setup_hook(self):
        if self.config.get('guild_id'):
            # Registering to one server is instant; global commands can take a while to appear
            guild = discord.Object(id=int(self.config['guild_id']))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        logging.info("Ready as %s", self.user)
        if sys.stdout:
            print(f"Ready as {self.user} - message it in Discord. Ctrl+C to stop.")

    async def on_message(self, message):
        if message.author.id != self.owner_id:
            return
        direct = isinstance(message.channel, discord.DMChannel)
        mentioned = self.user in message.mentions
        if not (direct or mentioned or message.channel.id == self.channel_id):
            return
        text = re.sub(rf"<@!?{self.user.id}>", '', message.content).strip()
        if not text:
            return
        if text.lower().strip('!. ') in RESET_WORDS:
            self.conversations.pop(message.channel.id, None)
            await message.reply("Fresh start - what's up?", mention_author=False)
            return
        async with message.channel.typing():
            reply = await self.reply_to(message.channel.id, text)
        parts = chunks(reply)
        await message.reply(parts[0], mention_author=False)
        for part in parts[1:]:
            await message.channel.send(part)


def answer(question, league, history):
    """Run the agent in this thread's own event loop, away from Discord's."""
    return asyncio.run(agent.run(question, league, discord=True, history=history))


def setup_logging():
    """Log to discord_bot.log, and to the console when there is one."""
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2,
                                                   encoding='utf-8')
    handlers = [handler] + ([logging.StreamHandler(sys.stdout)] if sys.stdout else [])
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format='[%(asctime)s] [%(levelname)s] %(name)s: %(message)s')


def main():
    if sys.stdout:  # None under pythonw (no console window)
        sys.stdout.reconfigure(encoding='utf-8')
    setup_logging()
    config = load_config()
    bot = FantasyBot(config)

    @bot.tree.command(name='ask', description="Ask your fantasy hockey agent (only you see it)")
    @app_commands.describe(question="e.g. Who should I start tonight?",
                           league="Which league (default: worked out from your question)")
    @app_commands.choices(league=league_choices())
    async def ask(interaction: discord.Interaction, question: str,
                  league: app_commands.Choice[str] = None):
        if interaction.user.id != bot.owner_id:
            await interaction.response.send_message("This bot only answers its owner.",
                                                    ephemeral=True)
            return
        # Answers take a minute or two; Discord needs a response within 3 seconds
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await bot.reply_to(interaction.channel_id, question,
                                   league.value if league else None)
        text = (f"**{league.name}:** " if league else "**Q:** ") + f"{question}\n\n{reply}"
        for part in chunks(text):
            await interaction.followup.send(part, ephemeral=True)

    @bot.tree.command(name='new', description="Start a fresh conversation in this channel")
    async def new(interaction: discord.Interaction):
        if interaction.user.id == bot.owner_id:
            bot.conversations.pop(interaction.channel_id, None)
        await interaction.response.send_message("Fresh start.", ephemeral=True)

    # discord.py reconnects by itself after network drops; log_handler=None keeps its
    # logging on ours instead of adding a second console handler
    bot.run(config['token'], log_handler=None)


if __name__ == '__main__':
    main()
