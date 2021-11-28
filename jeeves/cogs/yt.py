import discord
from ..util import run_sync_func, load_conf
import youtube_dl
from youtube_dl.utils import DownloadError
import os
import os.path
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import logging
import re
import time
from collections import defaultdict

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

conf = load_conf(__name__)

CACHE_PATH = conf["cache_path"] if "cache_path" in conf else "."
logger.info(f"Using path `{CACHE_PATH}` for audio cache")

ytdl_options = {
    "format": "bestaudio/best",  # try bestaudio, and fallback to best if not available
    "referer": "https://www.reddit.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36",
    "default_search": "ytsearch",
    "outtmpl": f"{CACHE_PATH}/%(id)s.%(ext)s",
    "noplaylist": True,
    #'quiet': True,
    "keepvideo": False,
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "vorbis",  # fairly space efficient and Discord can load it natively with libopus installed
        }
    ],
    "prefer_ffmpeg": True,
    #'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', # supposedly this makes it less likely to get "Read Error" or "The specified session has been invalidated"?
}
logger.debug(f"ytdl options: {ytdl_options}")

class UnknownVoiceChannel(BaseException):
    pass


def is_url(s):
    return "https" in s  # Obviously not perfect but


def get_url_from_id(yt_id):
    return f"https://youtube.com/watch?v={yt_id}"


def get_filename_from_id(yt_id):
    return os.path.join(CACHE_PATH, f"{yt_id}.ogg")


def on_song_end(self, vc, error=None):
    if error:
        raise Exception(
            f"Guild {vc.guild} VC {vc.name} - The song ended because of an error: {error}"
        )


class Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.queues = defaultdict(list)
        self.yt_pattern_short = re.compile("https:\/\/youtu.be\/([\w-]+)")
        self.yt_pattern_long = re.compile(
            "https:\/\/(www.)?youtube.com\/watch\?v=([\w-]+)"
        )

        self.ytdl = youtube_dl.YoutubeDL(ytdl_options)

        self.cleanup_queues.start()
        self.disconnect_from_empty_vcs.start()
        self.process_queues.start()

    def get_voice_client_by_voice_channel_id(self, voice_channel_id):
        for voice_client in self.bot.voice_clients:
            if voice_client.channel.id == voice_channel_id:
                return voice_client
        return None

    @tasks.loop(seconds=15)
    async def cleanup_queues(self):
        """When the bot disconnects from a channel due to inactivity, it'll wipe the queue
        associated with that channel. However, sometimes the bot will disconnect from the
        channel for other reasons. So we want to occasionally check if that's happened.
        But this shouldn't happen too frequently so we'll want to log this as a warning."""
        for voice_channel_id, queue in self.queues.items():
            if len(queue) != 0:
                voice_client = self.get_voice_client_by_voice_channel_id(
                    voice_channel_id
                )
                if voice_client is None:
                    logger.warning(
                        f"Deleting queue for voice channel {voice_channel_id}"
                    )
                    self.queues[voice_channel_id] = []

    @tasks.loop(seconds=15)
    async def disconnect_from_empty_vcs(self):
        """If the bot is sitting in a voice channel by itself, disconnect."""
        for voice_client in self.bot.voice_clients:
            me = voice_client.user
            others = [
                x.id for x in voice_client.channel.members if x.id != me.id
            ]  # This requires the Members privileged intent
            if not others:
                logger.info(
                    f"Disconnecting from voice channel '{voice_client.channel.name}' in guild {voice_client.guild.name}"
                )
                await voice_client.disconnect()
                self.queues[voice_client.channel.id] = []

    @tasks.loop(seconds=1)
    async def process_queues(self):
        """Frequently check if we are currently connected to any voice clients that aren't
        playing anything, and dequeue a song if there is one to dequeue. I think there is
        a better way of doing this? Instead of frequently checking all voice clients, maybe
        perform a loop when a song is queued in a voice channel?"""
        inactive_voice_clients = [
            vc for vc in self.bot.voice_clients if not vc.is_playing()
        ]
        for voice_client in inactive_voice_clients:
            queue = self.queues[voice_client.channel.id]
            if queue:
                logger.info(
                    f"Playing next song in queue for voice channel '{voice_client.channel.name}' in guild '{voice_client.guild.name}': '{queue[0]}' (queue length: {len(queue)})"
                )
                await self.play_next_song_in_queue(voice_client, queue)

    async def extract_info(self, query, download=False):
        await run_sync_func(self.ytdl.cache.remove)
        data = await run_sync_func(self.ytdl.extract_info, query, download=download)
        return data

    async def resolve_search_query(self, query):
        # Sometimes ytdl gives no search results... try again if so.
        # TODO: has this been fixed by changing DNS settings?
        max_tries = 3
        tries = 0
        while tries <= max_tries:
            data = await self.extract_info(
                f'"{query}"'
            )  # Putting the search in quotes prevents issues with colons and other special characters
            if "entries" in data:
                if len(data["entries"]) == 0:
                    logger.debug(
                        f"Received no search results from query {query}, trying again..."
                    )
                    await run_sync_func(time.sleep, 1)
                    continue

                result = data["entries"][0]
                logger.debug(f"Resolved query '{query}' to {result['id']}")
                return result["id"]

        raise Exception(
            f"Despite trying {max_tries} times, Youtube gave no search results."
        )

    async def play_next_song_in_queue(self, voice_client, queue):
        def check_audio_error(err):
            if err:
                raise Exception(
                    f"The song ended in voice channel '{voice_client.channel.name}' in guild '{voice_client.guild.name}' due to an error: {err}"
                )

        if len(queue) == 0:
            raise ValueError("Can't play next song in queue, the queue given is empty.")

        yt_id = queue.pop(0)
        filename = get_filename_from_id(yt_id)
        if not os.path.exists(filename):
            raise RuntimeError(f"Expected filename {filename} to exist, but it didn't")

        audio = discord.FFmpegPCMAudio(filename)
        voice_client.play(audio, after=check_audio_error)

        if not queue:
            logger.debug(f"The queue is now empty in voice channel '{voice_client.channel.name}' in guild '{voice_client.guild.name}'")

    async def get_id_from_query(self, query):
        if is_url(query):
            if m := self.yt_pattern_short.match(query):
                return m.group(1)
            elif m := self.yt_pattern_long.match(query):
                return m.group(2)
            else:
                raise ValueError("Query is a url but not an expected kind")
        else:
            return await self.resolve_search_query(query)
    
    async def join_voice_channel_for_context(self, ctx):
        if ctx.voice_client is not None:
            logger.debug(
                f"Already in VC {ctx.voice_client.channel.id} (Guild '{ctx.voice_client.channel.guild.name}', name '{ctx.voice_client.channel.name}')"
            )
            return True
    
        if ctx.author.voice:
            vc = ctx.author.voice.channel
            logger.info(
                f"Connecting to VC {vc.id} (Guild '{vc.guild.name}', name '{vc.name}')"
            )
            await vc.connect()
            return True
        
        raise UnknownVoiceChannel()
            

    @commands.command()
    async def play(self, ctx, *args):
        if len(args) == 0:
            await ctx.message.add_reaction("❓")
            return

        query = args[0] if is_url(args[0]) else " ".join(args)

        try:
            await self.join_voice_channel_for_context(ctx)
        except UnknownVoiceChannel as e:
            logger.debug(
                f"Guild '{ctx.guild.name}' Channel '{ctx.channel.name}' - Both me and Author were not in a voice channel, skipping"
            )
            await ctx.reply(
                "Since you aren't connected to a voice channel, I don't know which to join. I could look at all the voice channels of the servers I'm in and make a decision, but I'm not that smart yet."
            )
            return

        vc = ctx.voice_client.channel
        queue = self.queues[vc.id]
        playing_immediately = len(queue) == 0 and not ctx.voice_client.is_playing()

        reply = ""

        try:
            yt_id = await self.get_id_from_query(query)
        except DownloadError as e:
            logger.info(f"Got a DownloadError for '{query}': {e}")
            await ctx.reply(
                f"Youtube gave me an error when trying to search for that. The error message is:\n\n```\n{e}\n```"
            )
            return
        except Exception as e:
            logger.info(f"Failed to get id from query '{query}': {e.__class__} - {e}")
            await ctx.reply("I ran into an issue searching for that.")
            raise

        logger.info(
            f"Guild '{ctx.guild.name}' Channel '{ctx.channel.name}' - Asked to play YT ID {yt_id}"
        )

        filename = get_filename_from_id(yt_id)
        if not os.path.exists(filename):
            logger.info(f"Request not cached {filename}")
            await ctx.message.add_reaction("⏳")
            try:
                url = get_url_from_id(yt_id)
                await self.extract_info(url, download=True)
                logger.info(f"Download complete: {url}")
                await ctx.message.clear_reaction("⏳")
            except DownloadError as e:
                logger.warning("yt-dl DownloadError when downloading {yt_id}: {e}")
                await ctx.reply(
                    f"Youtube would not allow me to download that video right now: \n```\n{e}\n```\n\nThese errors are usually temporary. Try a different song then try again in a minute."
                )
                return
            except Exception as e:
                await ctx.reply(
                    "Sorry, I tried to download that and ran into an issue. Try again later or try a different song."
                )
                raise
        else:
            logger.info(f"Serving request from cached {filename}")

        await ctx.message.add_reaction("☑️")
        queue.append(yt_id)

        reply = ""
        if not is_url(query):
            reply += f"Found {get_url_from_id(yt_id)} . "
        reply += (
            "Playing immediately. "
            if playing_immediately
            else f"It has been queued ({len(queue)} in the queue). "
        )
        await ctx.reply(reply)

        if playing_immediately:
            logger.info(
                f"Guild '{ctx.guild.name}' Channel '{ctx.channel.name}' - Playing immediately"
            )
            await ctx.message.add_reaction("▶️")

    @commands.command()
    async def skip(self, ctx):
        if ctx.voice_client is None:
            logger.info(
                f"Guild '{ctx.guild.name}' Channel '{ctx.channel.name}' - Can't skip because I'm not playing anything in this channel"
            )
            return
        else:
            logger.debug(
                f"Guild '{ctx.guild.name}' Channel '{ctx.channel.name}' - Skipping"
            )
            ctx.voice_client.stop()

    @commands.command()
    async def queue(self, ctx):
        if ctx.voice_client is None:
            logger.info(
                f"Guild '{ctx.guild.name}' Channel '{ctx.channel.name}' - Can't get queue status because I'm not playing anything in this channel"
            )
            ctx.message.add_reaction("❌")
            return
        else:
            vc = ctx.voice_client.channel
            queue = self.queues[vc.id]
            logger.debug(
                f"Guild '{ctx.guild.name}' Voice Channel '{vc.name}' - Asked for queue status, response: {len(queue)}"
            )
            await ctx.reply(f"There are {len(queue)} songs in the queue.")
