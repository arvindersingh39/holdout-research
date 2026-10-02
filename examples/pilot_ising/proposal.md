# Monte Carlo study of the two-dimensional Ising model

For the ferromagnetic Ising model on an L×L square lattice with periodic boundaries
(J = 1, k_B = 1, no external field), implement single-spin Metropolis and Wolff cluster
updates, measure energy, magnetization, specific heat and the Binder cumulant with error
bars that account for autocorrelation, locate the critical temperature by finite-size
scaling, and make the Metropolis sweep fast.

## Acceptance

- On a 4×4 lattice, exact enumeration of all 2¹⁶ states gives the energy per spin and the
  specific heat at T = 2.0, 2.5 and 3.0; with fixed seeds, Metropolis and Wolff estimates
  each agree with the exact values within 4 error bars.
- Below the critical point (T = 1.8, L = 32), the mean absolute magnetization per spin
  agrees with Onsager–Yang's exact spontaneous magnetization within 0.01.
- At the exact critical temperature, for L = 32, the integrated autocorrelation time of
  |m| measured in sweeps is at least 5 times smaller for Wolff than for Metropolis.
- The Binder cumulant curves for L = 8, 16 and 32 cross within 1% of the exact critical
  temperature 2/ln(1+√2); a plot of the curves near the crossing is saved.
- 1000 Metropolis sweeps of a 64×64 lattice take under 0.5 s in the sandbox.
