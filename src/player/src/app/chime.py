"""An original, short bell tone generated once at the output sample rate."""
import numpy as np


def vote_chime(sample_rate):
    t = np.arange(round(sample_rate * 0.45), dtype=np.float64) / sample_rate
    attack = np.minimum(t / 0.006, 1.0)
    release = np.minimum((0.45 - t) / 0.04, 1.0)
    tone = (
        np.sin(2 * np.pi * 880 * t) * np.exp(-9 * t)
        + 0.35 * np.sin(2 * np.pi * 1320 * t) * np.exp(-13 * t)
    )
    return (0.12 * tone * attack * release).astype(np.float32)
