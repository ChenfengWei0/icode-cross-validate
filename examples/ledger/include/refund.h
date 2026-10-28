#ifndef REFUND_H
#define REFUND_H

#include "account.h"

/* 退款：把 amount 退回账户。前置条件：0 < amount <= 10000。 */
void refund(account_t *acc, int amount);

#endif
