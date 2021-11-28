from functools import partial
import asyncio
import json


async def run_sync_func(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    f = partial(func, *args, **kwargs)
    result = await loop.run_in_executor(None, f)
    return result


def load_conf(name):
    if name == "__main__":
        name = "jeeves"

    if "." in name:
        name = name.split(".")[-1]

    with open(f"conf/{name}.json") as f:
        try:
            return json.loads(f.read())
        except Exception as e:
            print(f"Failed to open conf file with name {name}: {e}")
            raise
