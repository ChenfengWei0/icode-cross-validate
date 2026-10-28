#include "discount.h"

/* 只减一次。 */
long discount(long total) { return total >= 100 ? total - 20 : total; }
