import yaml
from os.path import exists

config = None


def load_config(path=None):
    if path is None:
        for p in ["conf.yaml", "conf.yml"]:
            if exists(p):
                path = p
                break

    with open(path) as f:
        return yaml.load(f.read())
