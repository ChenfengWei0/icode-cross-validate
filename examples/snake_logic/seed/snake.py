"""A minimal snake game: the main task's code in the paper's example (/goal 实现贪吃蛇。)."""

import random

DIRECTIONS = {"上": (0, -1), "下": (0, 1), "左": (-1, 0), "右": (1, 0)}


class Snake:
    def __init__(self, body):
        self.body = list(body)  # head first

    @property
    def head(self):
        return self.body[0]


class Game:
    def __init__(self, width=6, height=6, seed=0):
        self.width, self.height = width, height
        self.rng = random.Random(seed)
        self.snake = Snake([(2, 2), (1, 2)])
        self.direction = "右"
        self.over = False
        self.food = self.spawn_food()

    def spawn_food(self):
        free = [(x, y) for x in range(self.width) for y in range(self.height) if (x, y) not in self.snake.body]
        return self.rng.choice(free) if free else None

    def turn(self, direction):
        dx, dy = DIRECTIONS[direction]
        cx, cy = DIRECTIONS[self.direction]
        if (dx, dy) != (-cx, -cy):
            self.direction = direction

    def step(self):
        """Advance one tick. Returns what happened: 'ate', 'moved' or 'over'."""
        if self.over:
            return "over"
        dx, dy = DIRECTIONS[self.direction]
        x, y = self.snake.head
        new = (x + dx, y + dy)
        eating = new == self.food
        body = self.snake.body if eating else self.snake.body[:-1]
        if not (0 <= new[0] < self.width and 0 <= new[1] < self.height) or new in body:
            self.over = True
            return "over"
        self.snake.body = [new, *body]
        if eating:
            self.food = self.spawn_food()
            if self.food is None:
                self.over = True
            return "ate"
        return "moved"
