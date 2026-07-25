"""PCM16 audio helpers for the MeetStream audio bridge."""
import numpy as np


def resample(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Resample raw PCM16 LE mono between sample rates (linear interpolation)."""
    if src_hz == dst_hz or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(len(x) * dst_hz / src_hz)
    if n_out <= 0:
        return b""
    t_src = np.linspace(0, 1, len(x), endpoint=False)
    t_dst = np.linspace(0, 1, n_out, endpoint=False)
    y = np.interp(t_dst, t_src, x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


def chunks(pcm: bytes, samples_per_chunk: int):
    """Yield PCM byte chunks of `samples_per_chunk` samples each (2 bytes/sample)."""
    step = samples_per_chunk * 2
    for i in range(0, len(pcm), step):
        yield pcm[i : i + step]
