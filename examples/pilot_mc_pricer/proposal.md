# Variance-reduced Monte Carlo pricing of European options

Under geometric Brownian motion, implement a baseline Monte Carlo pricer and the
Black–Scholes closed form, add antithetic and control variates, then make pricing with
10⁶ paths fast.

## Acceptance

- With fixed seeds, Monte Carlo prices fall within 4 standard errors of Black–Scholes
  across a grid of strikes and maturities.
- Put–call parity holds within tolerance.
- At equal path count, for an at-the-money call, antithetic variates cut the standard
  error by at least 20%, and a control variate on the terminal stock price cuts it by
  at least 40%.
- On a log-log plot, the slope of standard error against path count is within 0.1 of −0.5.
- Pricing with 10⁶ paths takes under 0.5 s in the sandbox.
