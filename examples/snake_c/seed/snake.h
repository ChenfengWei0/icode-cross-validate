/* A minimal snake game: the main task's code for the C evaluation (logic only, no UI). */
#ifndef SNAKE_H
#define SNAKE_H

#define SNAKE_MAX_W 16
#define SNAKE_MAX_H 16
#define SNAKE_MAX_CELLS (SNAKE_MAX_W * SNAKE_MAX_H)

typedef enum { DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT } Direction;
typedef enum { STEP_MOVED, STEP_ATE, STEP_OVER } StepResult;

typedef struct {
  int x, y;
} Cell;

typedef struct {
  int width, height;
  Cell body[SNAKE_MAX_CELLS]; /* body[0] is the head */
  int length;
  Direction direction;
  int over;
  int has_food;
  Cell food;
  unsigned int rng;
} Game;

/* Starts a game on a width x height board; returns 0, or -1 when the size is not supported. */
int game_init(Game *g, int width, int height, unsigned int seed);
/* Changes the direction for the next step; turning back onto the snake itself is ignored. */
void game_turn(Game *g, Direction d);
/* Advances one tick. */
StepResult game_step(Game *g);

#endif
