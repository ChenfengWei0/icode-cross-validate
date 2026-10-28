#include "refund.h"
#include "payment.h"

void refund(account_t *acc, int amount) { payment_credit(acc, amount); }
