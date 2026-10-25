"""Discord bot: ask the fantasy agent from Discord with /ask.

It only answers its owner, and its replies are ephemeral: visible only to you and gone once
you dismiss them or reload Discord, so answers don't pile up in channel history.

Claude runs through Claude Code on your subscription (the same as agent.py), so questions
count against your Claude plan's usage limits rather than API billing.

Config in discord_bot.json (git-ignored), or the matching environment variables:
    {"token": "...", "user_id": "...", "guild_id": "..."}
    DISCORD_BOT_TOKEN, DISCORD_USER_ID, DISCORD_GUILD_ID
guild_id is your private server's ID; with it, /ask shows up there right away.

Usage:
    python discord_bot.py
"""
import asyncio
import json
import os
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
MAX_MESSAGE = 2000
# Discord accepts follow-up replies for 15 minutes after a command
ANSWER_TIMEOUT = 14 * 60
# Follow-up questions see this many recent exchanges, if they're this recent. Kept in
# memory only, so it's gone when the bot restarts.
MEMORY_EXCHANGES = 4
MEMORY_SECONDS = 60 * 60


def load_config():
    config = {}
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    for key, env in (('token', 'DISCORD_BOT_TOKEN'), ('user_id', 'DISCORD_USER_ID'),
                     ('guild_id', 'DISCORD_GUILD_ID')):
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
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.owner_id = int(config['user_id'])
        self.tree = app_commands.CommandTree(self)
        # The agent's tools read one shared league setting, so answer one question at a time
        self.busy = asyncio.Lock()
        self.memory = deque(maxlen=MEMORY_EXCHANGES)  # (time, question, answer)

    def recent(self):
        cutoff = time.time() - MEMORY_SECONDS
        return [(q, a) for t, q, a in self.memory if t >= cutoff]

    async def setup_hook(self):
        if self.config.get('guild_id'):
            # Registering to one server is instant; global commands can take a while to appear
            guild = discord.Object(id=int(self.config['guild_id']))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        print(f"Ready as {self.user} - use /ask in Discord. Ctrl+C to stop.")


def answer(question, league, history):
    """Run the agent in this thread's own event loop, away from Discord's."""
    return asyncio.run(agent.run(question, league, discord=True, history=history))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    config = load_config()
    bot = FantasyBot(config)

    @bot.tree.command(name='ask', description="Ask your fantasy hockey agent")
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
        key = league.value if league else None
        try:
            async with bot.busy:
                reply = await asyncio.wait_for(
                    asyncio.to_thread(answer, question, key, bot.recent()), ANSWER_TIMEOUT)
            bot.memory.append((time.time(), question, reply))
        except asyncio.TimeoutError:
            reply = "That took too long - try a narrower question."
        except Exception as e:
            reply = f"Something went wrong: {type(e).__name__}: {e}"

        text = (f"**{league.name}:** " if league else "**Q:** ") + f"{question}\n\n{reply}"
        if yahoo_api.available():  # required wherever Yahoo data is shown
            text += f"\n-# {yahoo_api.ATTRIBUTION}"
        for part in chunks(text):
            await interaction.followup.send(part, ephemeral=True)

    bot.run(config['token'])


if __name__ == '__main__':
    main()
