#!/usr/bin/env python3

from jeeves.util import load_conf
import discord
from discord.ext import commands
import logging
import sys
import pkgutil
import importlib
import logging
import sentry_sdk

logging.basicConfig(
    format="%(asctime)s - %(name)s:%(levelname)s:%(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

conf = load_conf(__name__)

if "sentry_dsn" in conf:
    sentry_sdk.init(conf["sentry_dsn"], traces_sample_rate=1.0)

intents = discord.Intents.default()
# needed for yt - on_voice_state_update
# intents.voice_states = True
intents.members = True

bot = commands.Bot(
    command_prefix="-",
    intents=intents,
    description="""Description goes here.""",
)


def get_cog_modules(names):
    from jeeves import cogs

    long_names = [f"jeeves.cogs.{x}" for x in names]
    logger.debug(f"Importing modules: {long_names}")
    pkgs = pkgutil.iter_modules(cogs.__path__, cogs.__name__ + ".")
    plugins_to_load = [x for x in pkgs if x.name in long_names]
    return {
        name: importlib.import_module(name) for finder, name, ispkg in plugins_to_load
    }


def load_cogs(bot):
    logger.debug(f"Conf says to load cogs {conf['cogs']}")
    cog_names = [x for x in conf["cogs"]]
    cogs = get_cog_modules(cog_names)
    for cog_name, module in cogs.items():
        logger.info(f"Loading cog {cog_name} into discord.py")
        cog = module.Cog(bot)
        bot.add_cog(cog)


@bot.event
async def on_ready():
    logger.info(f"We have logged in as {bot.user}")


@bot.event
async def on_command(ctx):
    args = ctx.args[
        2:
    ]  # the first 2 args are 'self' and 'ctx' which aren't useful here
    logger.info(
        f"Guild '{ctx.channel.guild.name}' Channel '{ctx.channel.name}' - Dispatching command: {ctx.command.qualified_name}, args: {args}"
    )


if __name__ == "__main__":
    logger.info("Loading cogs")
    load_cogs(bot)
    logger.info("Done loading cogs")

    logger.info("Loading opus")
    discord.opus.load_opus("libopus.so.0")

    logger.info("Starting bot")
    bot.run(conf["discord_token"])
