import discord
import logging
from discord.ext import commands
import requests
from ..util import run_sync_func

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


async def _translate(text):
    source = "auto"
    target = "en"
    # ported from https://github.com/matheuss/google-translate-api/blob/master/index.js
    payload = {
        "client": "t",
        "sl": source,
        "tl": target,
        "hl": target,
        "dt": ["at", "bd", "ex", "ld", "md", "qca", "rw", "rm", "ss", "t"],
        "ie": "UTF-8",
        "oe": "UTF-8",
        "otf": 1,
        "ssel": 0,
        "tsel": 0,
        "kc": 7,
        "q": text,
    }
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source}&tl={target}&dt=t&dt=dj&q={text}&sc=1"
    r = await run_sync_func(requests.get, url, params=payload)
    r.raise_for_status()
    r = r.json()
    logger.debug(r)
    ret = {
        "text": "",
        "from": {
            "language": {"didYouMean": False, "iso": ""},
            "text": {"autoCorrected": False, "value": "", "didYouMean": False},
        },
        "raw": "",
    }
    ret["text"] = " ".join(
        [t[0] for t in r[0] if t[0]]
    )  # first item in r is the translations. Somewhere in here is the transliteration as well
    ret["from"]["language"]["iso"] = r[2]
    if r[7] and r[7][0]:  # r[7] contains corrections, the first is the corrected text
        corrected = r[7][0].replace("<b>", "").replace("</b>", "")
        ret["from"]["text"]["value"] = corrected

        try:
            if r[7][5]:
                ret["from"]["text"]["autocorrected"] = True
            else:
                ret["from"]["text"]["didYouMean"] = True
        except IndexError:
            pass

    transliteration = r[0][-1][-1]
    if transliteration and isinstance(transliteration, str):
        ret["from"]["text"]["transliteration"] = transliteration

    return ret


class Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def __get_message_content_from_ref(self, ctx, ref):
        if ref.cached_message:
            return ref.cached_message.content
        message = await ctx.channel.fetch_message(ref.message_id)
        return message.content

    @commands.command()
    async def translate(self, ctx, *args):
        # If the user has replied to a message they want to use as an argument
        if ctx.message.reference:
            arg = await self.__get_message_content_from_ref(ctx, ctx.message.reference)
        elif len(args) == 0:
            await ctx.message.add_reaction("❓")
            return
        else:
            # discord.py will always split input into multiple arguments, even though in this case the
            # entire sentence should be considered to be one argument. if you put quotations around the
            # sentence it will work properly but I don't want to require users to need to do that
            arg = " ".join(args)

        logger.debug(f"translate.py: Asked to translate: `{arg}`")
        try:
            t = await _translate(arg)
        except requests.HTTPError as e:
            logger.info("HTTPError from Google Translate")
            logger.debug(e.response.status_code)
            reply = "I've experienced an issue talking to Google Translate."
            if e.response.status_code == 400:
                reply += " Google says I am making the request improperly."
            elif e.response.status_code == 429:
                reply += " Google says: 'the user has sent too many of the same request in a given amount of time'. So, maybe chill? Or at least change your request."
            elif e.response.status_code >= 500:
                reply += " This is a server issue with Google."
        except Exception as e:
            logger.info("Exception from Google Translate")
            logger.debug(e)
            reply = "I've experienced an issue talking to Google Translate."
        else:
            reply = f"Detected language: _{t['from']['language']['iso']}_\nTranslation: _{t['text']}_"
            if "transliteration" in t["from"]["text"]:
                reply += f"\n{t['from']['text']['transliteration']}"
            if t["from"]["text"]["didYouMean"]:
                reply += f"\nDid you mean: {t['from']['text']['value']}"
            elif t["from"]["text"]["didYouMean"]:
                reply += f"\nAssumed you meant: {t['from']['text']['value']}"
        await ctx.reply(reply)
