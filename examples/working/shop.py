"""Tiny example program, standing in for code an AI wrote (the frozen copy)."""
from pricing import apply_discount


def subtotal(items):
    return sum(price * qty for price, qty in items)


def total(items, code=None):
    amount = subtotal(items)
    if code:
        amount = apply_discount(amount, code)
    amount += shipping(amount)
    return round(amount, 2)


def shipping(amount):
    return 0 if amount > 25 else 4.99


if __name__ == "__main__":
    print(total([(9.99, 2), (4.50, 1)], "SAVE10"))
