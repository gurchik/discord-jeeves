import discord
from discord.ext import commands, tasks
from ..util import load_conf, run_sync_func
from mcstatus import MinecraftServer
import boto3
import CloudFlare
from time import sleep

conf = load_conf(__name__)
ec2 = boto3.resource("ec2")


async def get_instance():
    return await run_sync_func(ec2.Instance, conf["instance_id"])


class Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.host = conf["server"]
        self.cf = CloudFlare.CloudFlare(token=conf["cloudflare_token"])
        self.mcserver = MinecraftServer(self.host)

        self.check_server_status.start()

    @commands.command()
    async def mc(self, ctx, *args):
        if len(args) == 0:
            return
        sub_cmd, *sub_args = args
        f = getattr(self, sub_cmd)
        await f(ctx, sub_args)

    async def __poll_for_status(self):
        num_checks = 5
        time_between_checks = 5
        for i in range(num_checks):
            try:
                status = await run_sync_func(self.mcserver.query)
                return status
            except Exception as e:
                if i + 1 == num_checks:
                    raise
                await run_sync_func(sleep, time_between_checks)

    async def __update_record(self, new_ip):
        domain_parts = self.host.split(".")
        sub_domain = domain_parts[0]  # TODO: support sub-subdomains
        zone_name = ".".join(domain_parts[1:])

        zones = await run_sync_func(self.cf.zones.get)
        zones = [z for z in zones if z["name"] == zone_name]
        if len(zones) != 1:
            raise Exception(f"Couldn't get zone {zone_name}")
        zone_id = zones[0]["id"]

        records = await run_sync_func(self.cf.zones.dns_records.get, zone_id)
        records = [r for r in records if r["name"] == self.host]
        if len(records) != 1:
            raise Exception(f"Couldn't find DNS record {self.host} in zone {zone_id}")
        record = records[0]

        data = {
            "name": record["name"],
            "type": record["type"],
            "content": new_ip,
            "proxied": record["proxied"],
        }
        await run_sync_func(
            self.cf.zones.dns_records.put, zone_id, record["id"], data=data
        )
        print(f"mc.py: Updated record {self.host} to {new_ip}")

    async def __start_server(self):
        instance = await get_instance()
        try:
            print("mc.py: Sending start command to instance")
            await run_sync_func(instance.start)
            print("mc.py: waiting for start")
            await run_sync_func(instance.wait_until_running)
            print("mc.py: done")
            await self.__update_record(instance.public_ip_address)
            await run_sync_func(sleep, 10)
            try:
                status = await self.__poll_for_status()
            except Exception as e:
                return f"I started the server. The host is `{self.host}`. However, I wasn't able to connect to it. It might be taking a bit longer than usual to start. Try waiting a bit."
            return (
                f"The server is on and accepting connections. The host is `{self.host}`"
            )
        except Exception as e:
            print(f"Error starting instance: {e}")
            return "Sorry, when I instructed the server to start, I ran into an issue. It may or may not be running. Wait a few minutes and give it a check. If not, try starting again after waiting."

    async def __restart_server(self):
        instance = await get_instance()
        try:
            print("mc.py: Sending stop command to instance")
            await run_sync_func(instance.stop)
            print("mc.py: waiting for stop")
            await run_sync_func(instance.wait_until_stopped)
            print("mc.py: done, starting instance")
            resp = await self.__start_server()
            return resp
        except Exception as e:
            print(f"Issue restarting instance: {e}")
            return "Sorry, when I tried to restart the server I ran into some sort of issue. I'm not sure if it's running or not. Wait a few minutes and give it a check."

    async def status(self, ctx, args=()):
        instance = await get_instance()
        state = instance.state["Name"]

        simple_response_states = {
            "stopping": "Server is in the middle of powering off.",
            "pending": "Server is in the middle of powering on.",
            "stopped": "Server is off.",
        }

        if state in simple_response_states:
            await ctx.reply(simple_response_states[state])
            return

        if state == "running":
            try:
                status = await run_sync_func(self.mcserver.query)
            except Exception as e:
                print(
                    "mc.py: When getting the status, the instance was up but the server query failed."
                )
                await ctx.reply(
                    "The server is powered on, but not accepting connections. Maybe it's in the middle of turning on or restarting?"
                )
            else:
                online = status.players.online
                reply = f"The server is on and accepting connections. There are {online} people logged on"
                if online:
                    players = (f"`{player}`" for player in status.players.names)
                    players_string = ", ".join(players)
                    reply += f" ({players_string})."
                await ctx.reply(reply)
        else:
            print(f"Unexpected instance state {state}")

    async def start(self, ctx, args=()):
        instance = await get_instance()
        state = instance.state["Name"]

        failure_states = {
            "running": "The server appears to already be running. Try `mc status` for more info.",
            "stopping": "Sorry, the server is currently shutting down. Please wait a minute for it to finish.",
            "pending": "The server is already being started, give it a minute.",
        }

        if state in failure_states:
            await ctx.message.add_reaction("❌")
            await ctx.reply(failure_states[state])
            return

        if state == "stopped":
            await ctx.message.add_reaction("⏳")
            resp = await self.__start_server()
            await ctx.reply(resp)
        else:
            print(f"Unexpected instance state {state}")

    async def restart(self, ctx, args=()):
        instance = await get_instance()
        state = instance.state["Name"]

        failure_states = {
            "stopping": "I can't restart the server because it's currently powering off.",
            "pending": "I can't restart the server because it's currently powering on.",
        }

        if state in failure_states:
            await ctx.message.add_reaction("❌")
            await ctx.reply(failure_states[state])
            return

        if state == "running":
            await ctx.message.add_reaction("⏳")
            resp = await self.__restart_server()
            await ctx.reply(resp)
        elif state == "stopped":
            await ctx.reply(
                "The server is off, so I'm instead redirecting to `mc start`"
            )
            await self.start(ctx)
        else:
            print(f"Unexpected instance state {state}")

    @tasks.loop(seconds=5)
    async def check_server_status(self):
        status = self.mcserver.query()
        """
        print(f"{status.motd=}")
        print(f"{status.map=}")
        print(f"{status.players.online=}")
        print(f"{status.players.max=}")
        print(f"{status.players.names=}")
        print(f"{status.software.version=}")
        print(f"{status.software.brand=}")
        print(f"{status.software.plugins=}")
        """
