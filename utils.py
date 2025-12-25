#———| Made By NT_Dev | plugins/utils.py |
import random
import time
from typing import Tuple, List

EMOJI_POOLS: List[List[str]] = [
    ["🍄", "🍁", "💯", "📦", "🍁"],
    ["💯", "🍁", "🍄", "🔥", "🤖"],
]

def gen_emoji_challenge() -> Tuple[str, List[str]]:
    pool = random.choice(EMOJI_POOLS)
    chosen = random.choice(pool)
    options = random.sample(pool, min(3, len(pool)))
    if chosen not in options:
        options[0] = chosen
    random.shuffle(options)
    return chosen, options

def now_ts() -> int:
    return int(time.time())

def escape_md_v2(text: str) -> str:
    escape_chars = set(r'\_*[]()~`>#+-=|{}.!')
    return "".join("\\" + c if c in escape_chars else c for c in text)