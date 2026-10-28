#include "payment.h"

static void apply(account_t *acc, long long delta) { acc->balance += delta; }

void pay(account_t *acc, int amount) { apply(acc, -(long long)amount); }

void payment_credit(account_t *acc, int amount) { apply(acc, amount); }
