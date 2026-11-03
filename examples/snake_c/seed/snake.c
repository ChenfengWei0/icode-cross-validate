#include "snake.h"

static const int DX[4] = {0, 0, -1, 1}; /* up, down, left, right */
static const int DY[4] = {-1, 1, 0, 0};

static unsigned int next_random(Game *g) {
  g->rng = g->rng * 1103515245u + 12345u;
  return g->rng >> 16;
}

static int in_body(const Game *g, Cell c, int length) {
  for (int i = 0; i < length; i++)
    if (g->body[i].x == c.x && g->body[i].y == c.y)
      return 1;
  return 0;
}

static void spawn_food(Game *g) {
  int free = 0;
  for (int x = 0; x < g->width; x++)
    for (int y = 0; y < g->height; y++) {
      Cell c = {x, y};
      if (!in_body(g, c, g->length))
        free++;
    }
  if (free == 0) {
    g->has_food = 0;
    return;
  }
  int pick = (int)(next_random(g) % (unsigned int)free);
  for (int x = 0; x < g->width; x++)
    for (int y = 0; y < g->height; y++) {
      Cell c = {x, y};
      if (in_body(g, c, g->length)) continue;
      if (pick == 0) {
        g->food = c;
        g->has_food = 1;
        return;
      }
      pick--;
    }
}

int game_init(Game *g, int width, int height, unsigned int seed) {
  if (width < 3 || height < 3 || width > SNAKE_MAX_W || height > SNAKE_MAX_H)
    return -1;
  g->width = width;
  g->height = height;
  g->rng = seed;
  g->body[0].x = 2;
  g->body[0].y = 2;
  g->body[1].x = 1;
  g->body[1].y = 2;
  g->length = 2;
  g->direction = DIR_RIGHT;
  g->over = 0;
  g->has_food = 0;
  spawn_food(g);
  return 0;
}

void game_turn(Game *g, Direction d) {
  if (DX[d] != -DX[g->direction] || DY[d] != -DY[g->direction])
    g->direction = d;
}

StepResult game_step(Game *g) {
  if (g->over)
    return STEP_OVER;
  Cell head = g->body[0];
  Cell next = {head.x + DX[g->direction], head.y + DY[g->direction]};
  int eating = g->has_food && next.x == g->food.x && next.y == g->food.y;
  int kept = eating ? g->length : g->length - 1; /* the tail moves away unless the snake grows */
  if (next.x < 0 || next.x >= g->width || next.y < 0 || next.y >= g->height || in_body(g, next, kept)) {
    g->over = 1;
    return STEP_OVER;
  }
  for (int i = kept; i > 0; i--)
    g->body[i] = g->body[i - 1];
  g->body[0] = next;
  g->length = kept + 1;
  if (eating) {
    spawn_food(g);
    if (!g->has_food)
      g->over = 1;
    return STEP_ATE;
  }
  return STEP_MOVED;
}
