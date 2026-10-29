"""Instrumentation and fuzzing written by the alignment agent: drive the game with random turns and
record one trace state per tick (README 细则 12.1, 12.2). The game is imported from W unchanged."""

import json
import os
import random
import sys

sys.path.insert(0, os.environ["CV_W"])
from snake import DIRECTIONS, Game

MAX_STEPS = 300
seed = int(os.environ.get("CV_RUN", "0"))
rng = random.Random(seed)
game = Game(seed=seed)
CELLS = game.width * game.height


def values(game, events):
    return {
        "长度(蛇)": len(game.snake.body),
        "头(蛇)": list(game.snake.head),
        "尾(蛇)": list(game.snake.body[-1]),
        "格子总数": CELLS,
        "结束(游戏)": game.over,
        "位于蛇身(食物)": game.food is not None and game.food in game.snake.body,
        **events,
    }


NO_EVENTS = {"吃到(蛇,食物)": False, "撞到自身(蛇)": False, "新生成(食物)": False, "移动(蛇)": False}
ENTITIES = {"蛇": ["蛇"], "食物": ["食物"], "游戏": ["游戏"]}


def choose(game):
    """Prefer turns that stay on the board, and head for the food half of the time."""
    x, y = game.snake.head
    cx, cy = DIRECTIONS[game.direction]
    safe = [
        d
        for d, (dx, dy) in DIRECTIONS.items()
        if (dx, dy) != (-cx, -cy) and 0 <= x + dx < game.width and 0 <= y + dy < game.height
    ]
    if game.food and rng.random() < 0.5:
        fx, fy = game.food
        toward = [d for d in safe if (DIRECTIONS[d][0] * (fx - x) > 0) or (DIRECTIONS[d][1] * (fy - y) > 0)]
        if toward:
            return rng.choice(toward)
    return rng.choice(safe or list(DIRECTIONS))


with open(os.environ["CV_TRACE"], "w", encoding="utf-8") as out:
    for _ in range(MAX_STEPS):
        if game.over:
            break
        action = choose(game)
        before_head, before_body, before_food = game.snake.head, list(game.snake.body), game.food
        game.turn(action)
        dx, dy = DIRECTIONS[game.direction]
        target = (before_head[0] + dx, before_head[1] + dy)
        eating = target == before_food
        bitten = target in (before_body if eating else before_body[:-1])
        state = {"实体": ENTITIES, "动作": action}
        frozen = {
            "长度(蛇)": len(before_body),
            "头(蛇)": list(before_head),
            "尾(蛇)": list(before_body[-1]),
            "格子总数": CELLS,
            "结束(游戏)": False,
            "位于蛇身(食物)": before_food is not None and before_food in before_body,
        }
        result = game.step()
        events = {
            "吃到(蛇,食物)": result == "ate",
            "撞到自身(蛇)": bitten,
            "新生成(食物)": result == "ate" and game.food is not None,
            "移动(蛇)": game.snake.head != before_head,
        }
        state["值"] = {**frozen, **events}
        out.write(json.dumps(state, ensure_ascii=False) + "\n")
    out.write(json.dumps({"实体": ENTITIES, "动作": None, "值": values(game, NO_EVENTS)}, ensure_ascii=False) + "\n")
