"""Cross-platform microphone -> guest COM1, 8 kHz mono G.711 mu-law."""
from array import array
import queue
import socket
import threading
import time


def open_input(sd, callback, on_status=print):
    devices = sd.query_devices()
    default = sd.default.device[0]
    candidates = [default] if default >= 0 else []
    candidates += [i for i, item in enumerate(devices) if item['max_input_channels'] > 0 and i not in candidates]
    on_status(f'Microphone default device: {default}; input candidates: {candidates}')
    for index in candidates:
        item = devices[index]
        if item['max_input_channels'] < 1:
            continue
        for rate in dict.fromkeys([int(item['default_samplerate']), 48000, 44100, 16000, 8000]):
            if rate < 8000:
                continue
            try:
                sd.check_input_settings(device=index, channels=1, dtype='int16', samplerate=rate)
                stream = sd.RawInputStream(device=index, samplerate=rate, channels=1, dtype='int16',
                    blocksize=max(1, rate // 50), callback=callback)
                on_status(f'Selected microphone: {index}: {item["name"]}; {rate} Hz')
                return stream, rate
            except Exception as error:
                on_status(f'Microphone device {index}, {rate} Hz rejected: {error}')
    raise RuntimeError('No usable microphone. Connect an input device and allow desktop microphone access in host privacy settings.')


def test_input():
    import sounddevice as sd
    peaks = []
    def callback(raw, *args):
        samples = array('h')
        samples.frombytes(bytes(raw))
        peaks.append(max((abs(value) for value in samples), default=0))
    stream, rate = open_input(sd, callback)
    print('Speak into the microphone for five seconds.', flush=True)
    with stream:
        time.sleep(5)
    print(f'Microphone test: blocks={len(peaks)}, peak={max(peaks, default=0)}/32768.', flush=True)
    if not peaks:
        raise RuntimeError('The input device did not deliver audio')
    if max(peaks) == 0:
        print('Only silence was received. Check microphone mute and host privacy settings.', flush=True)
    return 0


def mu_law(sample):
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), 32635) + 0x84
    exponent = max(0, magnitude.bit_length() - 8)
    mantissa = (magnitude >> (exponent + 3)) & 15
    return (~(sign | (exponent << 4) | mantissa)) & 255


class Resampler:
    """Average host samples into an 8 kHz stream, retaining fractional phase."""
    def __init__(self, rate):
        self.rate = int(rate)
        if self.rate < 8000:
            raise ValueError('Microphone must support a sample rate of at least 8000 Hz')
        self.phase = self.total = self.count = 0

    def feed(self, raw):
        values = array('h')
        values.frombytes(bytes(raw))  # PortAudio supplies native-endian int16.
        encoded = bytearray()
        for value in values:
            self.total += value
            self.count += 1
            self.phase += 8000
            if self.phase >= self.rate:
                encoded.append(mu_law(int(self.total / self.count)))
                self.phase -= self.rate
                self.total = self.count = 0
        return bytes(encoded)


class Microphone:
    def __init__(self, port, on_status=print):
        self.port = port
        self.on_status = on_status
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.sock = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(timeout=3)

    def run(self):
        stream = None
        try:
            import sounddevice as sd
            audio = queue.Queue(maxsize=16)
            def callback(indata, frames, timing, status):
                # Audio callback never waits for TCP or does speech recognition.
                raw = bytes(indata)
                try:
                    audio.put_nowait(raw)
                except queue.Full:
                    try:
                        audio.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        audio.put_nowait(raw)
                    except queue.Full:
                        pass
            stream, rate = open_input(sd, callback, self.on_status)
            resampler = Resampler(rate)
            while not self.stop_event.is_set():
                try:
                    self.sock = socket.create_connection(('127.0.0.1', self.port), .5)
                    self.sock.settimeout(2)
                    self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    break
                except OSError:
                    self.stop_event.wait(.2)
            if self.stop_event.is_set():
                return
            with self.sock, stream:
                self.on_status('Hossein microphone connected (recognition remains inside NamakiOS).')
                while not self.stop_event.is_set():
                    try:
                        raw = audio.get(timeout=.2)
                    except queue.Empty:
                        continue
                    encoded = resampler.feed(raw)
                    if encoded:
                        self.sock.sendall(encoded)
        except Exception as error:
            if not self.stop_event.is_set():
                self.on_status(f'Microphone unavailable: {error}. Connect/enable a microphone and restart.')
        finally:
            if stream is not None:
                stream.close()
