"""Stdlib-only identity check: sum of squares closed form for small n."""

for n in range(50):
    assert sum(i * i for i in range(n)) == (n - 1) * n * (2 * n - 1) // 6, n
print("ok")
