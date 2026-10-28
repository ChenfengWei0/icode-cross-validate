#include "discount.h"

/* 两种读法都不符合：满 100 减 30。 */
long discount(long total) { return total >= 100 ? total - 30 : total; }
