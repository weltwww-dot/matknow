"""Provider capacities and conservative text-only token estimates."""
import math
from urllib.parse import urlsplit


def valid_capacity(value):
    return value if type(value) is int and 2048<=value<=10000000 else None


def official_capacity(url,model):
    # Reviewed 2026-10-16: https://api-docs.deepseek.com/quick_start/pricing
    if urlsplit(url).hostname=='api.deepseek.com' and model in ('deepseek-flash','deepseek-v4-pro','deepseek-v4-flash','deepseek-v4-flash-vision-exp'):
        return 1000000
    return None


def estimate_tokens(messages):
    # shortcut: tokenizer differs by vendor; replace with provider token counting when offered.
    total=3
    for message in messages:
        text=message['content'];ascii_count=sum(ord(c)<128 for c in text)
        total+=8+math.ceil(ascii_count/3)+(len(text)-ascii_count)
    return total


def output_reserve(capacity):
    return min(8192,max(128,capacity//4)) if capacity else 8192


def input_budget(capacity):
    return capacity-output_reserve(capacity)


def fits_context(messages,capacity):
    capacity=valid_capacity(capacity)
    return capacity is None or estimate_tokens(messages)<=input_budget(capacity)


def usage_snapshot(messages,capacity,*,actual=None,compacted=False):
    used=estimate_tokens(messages) if actual is None else actual
    return {'used':used,'capacity':capacity,'estimated':actual is None,'compacted':compacted}


class ModelDirectory(list):
    def __init__(self,names,context_windows):
        super().__init__(names);self.context_windows=context_windows
