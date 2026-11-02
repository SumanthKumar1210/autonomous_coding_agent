def celsius_to_fahrenheit(c: float) -> float:
    if c < -273.15:
        raise ValueError("Temperature cannot be below absolute zero.")
    return c * 9 / 5 + 32
