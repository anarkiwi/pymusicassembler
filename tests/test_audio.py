"""Audio rendering via a fake SID device."""

import numpy as np
import pytest

from pymusicassembler import audio, reader
from pymusicassembler.errors import MusicAssemblerError


class _FakeSid:
    """Minimal device: one sample per run, counts register writes."""

    sampling_hz = 44100

    def __init__(self):
        self.writes = 0

    def run(self, writes, until):  # pylint: disable=unused-argument
        self.writes += len(writes)
        return np.zeros(1, np.int16)


def test_render_samples_with_fake_device(tune_path):
    device = _FakeSid()
    samples, freq = audio.render_samples(
        reader.read(tune_path), seconds=0.1, device=device
    )
    assert freq == 44100
    assert len(samples) > 0
    assert device.writes > 0


def test_render_wav_writes_file(tune_path, tmp_path):
    out = audio.render_wav(
        reader.read(tune_path),
        tmp_path / "out.wav",
        seconds=0.05,
        device=_FakeSid(),
    )
    assert out.exists()
    assert out.read_bytes()[:4] == b"RIFF"


def test_write_wav_accepts_iterables(tmp_path):
    audio.write_wav(tmp_path / "x.wav", [0, 1, 2, 3], 8000)
    assert (tmp_path / "x.wav").exists()


def test_bad_model(tune_path):
    with pytest.raises(MusicAssemblerError):
        audio.render_samples(reader.read(tune_path), model="nope", device=_FakeSid())
