#ifndef ACCOUNT_H
#define ACCOUNT_H

/* 账户。余额以分为单位。 */
typedef struct {
    long long balance;
} account_t;

/* 以 initial 为初始余额初始化账户。 */
void account_init(account_t *acc, long long initial);

#endif
