DISCOUNTS = {"SAVE10": 0.10, "HALF": 0.50}


def apply_discount(amount, code):
    rate = DISCOUNTS.get(code, 0.0)
    return amount * (1 - rate)
