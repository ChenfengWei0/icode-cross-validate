#include "refund.h"
#include "payment.h"

/* 金额算错：退了两倍。 */
void refund(account_t *acc, int amount) {
    payment_credit(acc, amount);
    payment_credit(acc, amount);
}
