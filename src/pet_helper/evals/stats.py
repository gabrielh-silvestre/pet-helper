from math import sqrt


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if successes < 0 or n < 0 or successes > n:
        raise ValueError(f"{successes} successes out of {n} items is not a count")
    if n == 0:
        return 0.0, 1.0
    rate = successes / n
    scale = 1 + z**2 / n
    centre = (rate + z**2 / (2 * n)) / scale
    spread = z * sqrt(rate * (1 - rate) / n + z**2 / (4 * n**2)) / scale
    return max(0.0, centre - spread), min(1.0, centre + spread)
