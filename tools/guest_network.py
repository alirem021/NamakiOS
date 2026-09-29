"""Small local Ethernet gateway for the guest's single browser TCP connection.

This deliberately exposes only 10.0.2.2:8765. Chromium handles Internet access.
It works with QEMU's socket backend and needs no host networking libraries.
"""
from collections import deque
import secrets
import socket
import struct
import threading
import time

HOST_IP = socket.inet_aton('10.0.2.2')
HOST_MAC = bytes.fromhex('525500123456')


def checksum(data):
    if len(data) & 1:
        data += b'\0'
    total = sum(struct.unpack('!' + 'H' * (len(data) // 2), data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return ~total & 65535


def exact(sock, size):
    output = bytearray()
    while len(output) < size:
        chunk = sock.recv(size - len(output))
        if not chunk:
            raise EOFError('Network connection closed')
        output.extend(chunk)
    return bytes(output)


class Gateway:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wire = self.bridge = None
        self.inflight = deque()
        self.pending = bytearray()
        self.rx = self.tx = self.port_guest = 0
        self.window = 0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def emit(self, payload, protocol):
        packet = self.mac_guest + HOST_MAC + struct.pack('!H', protocol) + payload
        self.wire.sendall(struct.pack('!I', len(packet)) + packet)

    def tcp(self, seq, flags, payload=b''):
        header = struct.pack('!HHIIBBHHH', 8765, self.port_guest, seq & 0xffffffff,
            self.rx & 0xffffffff, 0x50, flags, 65535, 0, 0)
        pseudo = HOST_IP + self.ip_guest + struct.pack('!BBH', 0, 6, len(header) + len(payload))
        header = header[:16] + struct.pack('!H', checksum(pseudo + header + payload)) + header[18:]
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 40 + len(payload), 0, 0x4000,
            64, 6, 0, HOST_IP, self.ip_guest)
        ip = ip[:10] + struct.pack('!H', checksum(ip)) + ip[12:]
        self.emit(ip + header + payload, 0x800)

    def pump(self):
        occupied = sum(len(packet[1]) for packet in self.inflight)
        room = min(self.window, 5600) - occupied
        while room > 0 and self.pending:
            length = min(room, 1400, len(self.pending))
            data = bytes(self.pending[:length])
            del self.pending[:length]
            self.tcp(self.tx, 0x18, data)
            self.inflight.append((self.tx, data, time.monotonic()))
            self.tx = (self.tx + length) & 0xffffffff
            room -= length

    def reset(self):
        if self.bridge:
            try:
                self.bridge.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.bridge.close()
            self.bridge = None
        self.inflight.clear()
        self.pending.clear()
        self.port_guest = 0

    def receive_browser(self, bridge):
        try:
            while not self.stop.is_set():
                with self.lock:
                    if self.bridge is not bridge:
                        return
                    full = len(self.pending) > 128 * 1024
                if full:
                    self.stop.wait(.01)
                    continue
                data = bridge.recv(16384)
                if not data:
                    return
                with self.lock:
                    if self.bridge is not bridge:
                        return
                    self.pending.extend(data)
                    self.pump()
        except (OSError, EOFError):
            pass

    def packet(self, frame):
        if len(frame) < 14:
            return
        protocol = struct.unpack('!H', frame[12:14])[0]
        if protocol == 0x806 and len(frame) >= 42:
            arp = frame[14:42]
            if arp[6:8] == b'\0\1' and arp[24:28] == HOST_IP:
                self.mac_guest = arp[8:14]
                reply = struct.pack('!HHBBH', 1, 0x800, 6, 4, 2) + HOST_MAC + HOST_IP + arp[8:18]
                self.emit(reply, 0x806)
            return
        if protocol != 0x800 or len(frame) < 54:
            return
        ip = frame[14:]
        ihl = (ip[0] & 15) * 4
        if ip[9] != 6 or ip[16:20] != HOST_IP:
            return
        tcp = ip[ihl:struct.unpack('!H', ip[2:4])[0]]
        if len(tcp) < 20:
            return
        source, destination, seq, ack, offset, flags, window = struct.unpack('!HHIIBBH', tcp[:16])
        if destination != 8765:
            return
        data = tcp[(offset >> 4) * 4:]
        if flags & 2:
            self.reset()
            self.mac_guest, self.ip_guest = frame[6:12], ip[12:16]
            self.port_guest, self.rx = source, (seq + 1) & 0xffffffff
            self.tx = secrets.randbelow(0x10000000)
            self.window = window
            self.tcp(self.tx, 0x12)
            self.tx += 1
            self.bridge = socket.create_connection(('127.0.0.1', 8765), 3)
            threading.Thread(target=self.receive_browser, args=(self.bridge,), daemon=True).start()
            return
        if source != self.port_guest:
            return
        if flags & 4:
            self.reset()
            return
        self.window = window
        if flags & 16:
            while self.inflight and len(self.inflight[0][1]) <= ((ack - self.inflight[0][0]) & 0xffffffff) < 0x80000000:
                self.inflight.popleft()
        if data:
            if seq == self.rx:
                self.rx = (self.rx + len(data)) & 0xffffffff
                self.bridge.sendall(data)
            self.tcp(self.tx, 0x10)
        if flags & 1:
            self.rx = (self.rx + 1) & 0xffffffff
            self.tcp(self.tx, 0x11)
            self.reset()
            return
        self.pump()

    def run(self):
        try:
            self.wire, _ = self.listener.accept()
            self.wire.settimeout(.1)
            buffer = bytearray()
            while not self.stop.is_set():
                try:
                    chunk = self.wire.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                except socket.timeout:
                    pass
                with self.lock:
                    while len(buffer) >= 4:
                        size = struct.unpack('!I', buffer[:4])[0]
                        if size > 65536:
                            raise ValueError('Invalid Ethernet frame')
                        if len(buffer) < size + 4:
                            break
                        self.packet(bytes(buffer[4:4 + size]))
                        del buffer[:4 + size]
                    if self.inflight and time.monotonic() - self.inflight[0][2] > .8:
                        seq, data, _ = self.inflight[0]
                        self.tcp(seq, 0x18, data)
                        self.inflight[0] = (seq, data, time.monotonic())
        except (OSError, EOFError, ValueError):
            pass
        finally:
            with self.lock:
                self.reset()

    def close(self):
        self.stop.set()
        self.listener.close()
        if self.wire:
            try:
                self.wire.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.wire.close()
        self.thread.join(timeout=3)
