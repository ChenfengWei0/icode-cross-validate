#include "discount.h"

/* 每满 100 元减一次。 */
long discount(long total) { return total - 20 * (total / 100); }
