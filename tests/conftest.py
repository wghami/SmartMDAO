import matplotlib

# Force the non-interactive Agg backend so visualization tests never try to
# pop a window, whether run in CI or on a contributor's machine.
matplotlib.use("Agg")
