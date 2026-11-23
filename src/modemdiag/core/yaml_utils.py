"""Use PyYAML's safe C loader when available, with a safe Python fallback."""
import yaml

SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def safe_load(stream):
    return yaml.load(stream, Loader=SAFE_LOADER)
