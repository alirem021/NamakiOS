"""Play QEMU's growing PCM WAV stream through the host's PortAudio device."""
import struct
import threading


def output_settings():
    import sounddevice as sd
    devices = sd.query_devices()
    print(f'Audio default devices: {sd.default.device}', flush=True)
    default = sd.default.device[1]
    candidates = [default] if default >= 0 else []
    candidates += [i for i, item in enumerate(devices) if item['max_output_channels'] > 0 and i not in candidates]
    for index in candidates:
        item = devices[index]
        channels = min(2, item['max_output_channels'])
        if not channels:
            continue
        for rate in dict.fromkeys([int(item['default_samplerate']), 48000, 44100]):
            try:
                sd.check_output_settings(device=index, channels=channels, dtype='int16', samplerate=rate)
                # Opening also verifies that the device is actually available.
                with sd.RawOutputStream(device=index, channels=channels, dtype='int16', samplerate=rate):
                    pass
                print(f'Selected speaker: {index}: {item["name"]}; {rate} Hz; channels={channels}', flush=True)
                return index, rate, channels
            except Exception as error:
                print(f'Speaker device {index}, {rate} Hz rejected: {error}', flush=True)
    raise RuntimeError('No usable speaker device. Connect or enable an audio output in host settings.')


def test_output():
    import math
    from array import array
    import sounddevice as sd
    device, rate, channels = output_settings()
    pcm = array('h', (int(6000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate) for _ in range(channels)))
    with sd.RawOutputStream(device=device, samplerate=rate, channels=channels, dtype='int16') as output:
        output.write(pcm.tobytes())
    print('Speaker test tone sent. You should hear a one-second beep.', flush=True)
    return 0


def pcm_format(header):
    if len(header) < 44 or header[:4] != b'RIFF' or header[8:12] != b'WAVE' or header[12:16] != b'fmt ' or header[36:40] != b'data':
        raise ValueError('Unexpected QEMU WAV header')
    size, codec, channels, rate, _, align, bits = struct.unpack('<IHHIIHH', header[16:36])
    if size != 16 or codec != 1 or bits != 16 or align != channels * 2:
        raise ValueError('QEMU speaker stream must be PCM16')
    return rate, channels


class Speaker:
    def __init__(self, path, device=None):
        self.path = path
        self.device = device
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.play, daemon=True)
        self.thread.start()

    def play(self):
        try:
            import sounddevice
            while not self.stop_event.wait(.05):
                if self.path.exists() and self.path.stat().st_size >= 44:
                    break
            if self.stop_event.is_set():
                return
            with self.path.open('rb') as source:
                rate, channels = pcm_format(source.read(44))
                with sounddevice.RawOutputStream(device=self.device, samplerate=rate, channels=channels, dtype='int16') as output:
                    print(f'Speaker output ready: {rate} Hz, {channels} channels.', flush=True)
                    pending = b''
                    while not self.stop_event.is_set():
                        pending += source.read(rate * channels * 2 // 50)
                        count = len(pending) // (channels * 2) * (channels * 2)
                        if count:
                            output.write(pending[:count])
                            pending = pending[count:]
                        else:
                            self.stop_event.wait(.01)
        except Exception as error:
            print(f'Speaker playback failed: {type(error).__name__}: {error}', flush=True)

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
