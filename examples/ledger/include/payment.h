#ifndef PAYMENT_H
#define PAYMENT_H

#include "account.h"

/* 支付模块：所有资金变动都必须经过本模块，以便对账。 */

/* 付款：从余额中扣除 amount。前置条件：0 < amount <= 10000，且余额不少于 amount。 */
void pay(account_t *acc, int amount);

/* 入账：向余额中加入 amount。前置条件：0 < amount <= 10000。 */
void payment_credit(account_t *acc, int amount);

#endif
