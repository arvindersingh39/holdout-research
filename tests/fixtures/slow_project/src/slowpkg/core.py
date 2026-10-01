"""Sum of squares, once slowly and once in closed form."""


def slow_sum(n: int) -> int:
    total = 0
    for i in range(n):
        total += i * i
    return total


def closed_form(n: int) -> int:
    return (n - 1) * n * (2 * n - 1) // 6


def compute(n: int = 200_000) -> int:
    return slow_sum(n) - closed_form(n)
