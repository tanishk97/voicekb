"""Bluetooth HID keyboard device.

Presents the Pi to a host (your Mac) as an ordinary Bluetooth keyboard. The host
sees a standard boot-protocol keyboard and has no idea anything generated the
keystrokes other than fingers.

Two pieces have to line up:

1. An SDP record advertising the HID service, registered through BlueZ's
   org.bluez.ProfileManager1 D-Bus API. This is what makes the Mac offer to pair
   with us *as a keyboard*.
2. Two L2CAP sockets -- PSM 17 (control) and PSM 19 (interrupt). Reports go out
   on the interrupt channel. BlueZ's built-in `input` plugin binds these same
   PSMs for the HID Host role, which is why scripts/setup_bluetooth.sh starts
   bluetoothd with --noplugin=input.

Must run as root: binding low L2CAP PSMs is privileged.
"""

from __future__ import annotations

import socket
import sys
import threading
import time

from .hid_keycodes import (
    MOD_NONE,
    Key,
    build_report,
    key_for_char,
    key_for_name,
    unmappable,
)

HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/org/bluez/voicekb_hid"
PSM_CONTROL = 17
PSM_INTERRUPT = 19

# Bluetooth HID transaction header: DATA (0xA0) | Input report type (0x01).
HIDP_DATA_INPUT = 0xA1
REPORT_ID = 0x01

# Boot-protocol keyboard report descriptor: 8-bit modifier bitmap, one reserved
# byte, a 5-bit LED output report, and six key slots.
HID_REPORT_DESCRIPTOR = bytes([
    0x05, 0x01,  # Usage Page (Generic Desktop)
    0x09, 0x06,  # Usage (Keyboard)
    0xA1, 0x01,  # Collection (Application)
    0x85, REPORT_ID,  # Report ID (1)
    0x05, 0x07,  # Usage Page (Keyboard/Keypad)
    0x19, 0xE0,  # Usage Minimum (Left Control)
    0x29, 0xE7,  # Usage Maximum (Right GUI)
    0x15, 0x00,  # Logical Minimum (0)
    0x25, 0x01,  # Logical Maximum (1)
    0x75, 0x01,  # Report Size (1)
    0x95, 0x08,  # Report Count (8)
    0x81, 0x02,  # Input (Data, Variable, Absolute) -- modifier byte
    0x95, 0x01,  # Report Count (1)
    0x75, 0x08,  # Report Size (8)
    0x81, 0x01,  # Input (Constant) -- reserved byte
    0x95, 0x05,  # Report Count (5)
    0x75, 0x01,  # Report Size (1)
    0x05, 0x08,  # Usage Page (LEDs)
    0x19, 0x01,  # Usage Minimum (Num Lock)
    0x29, 0x05,  # Usage Maximum (Kana)
    0x91, 0x02,  # Output (Data, Variable, Absolute) -- LED report
    0x95, 0x01,  # Report Count (1)
    0x75, 0x03,  # Report Size (3)
    0x91, 0x01,  # Output (Constant) -- LED padding
    0x95, 0x06,  # Report Count (6)
    0x75, 0x08,  # Report Size (8)
    0x15, 0x00,  # Logical Minimum (0)
    0x25, 0x65,  # Logical Maximum (101)
    0x05, 0x07,  # Usage Page (Keyboard/Keypad)
    0x19, 0x00,  # Usage Minimum (0)
    0x29, 0x65,  # Usage Maximum (101)
    0x81, 0x00,  # Input (Data, Array) -- six key slots
    0xC0,        # End Collection
])


def _descriptor_hex() -> str:
    return "".join(f"{b:02x}" for b in HID_REPORT_DESCRIPTOR)


def sdp_record() -> str:
    """SDP service record XML for a HID keyboard.

    Attribute 0x0203 is HIDCountryCode, set to 0x21 (33 = US). Left at 0x00,
    "not localized", macOS cannot tell what layout the keyboard has, so on every
    new host it runs the Keyboard Setup Assistant and asks for the key to the
    right of left Shift. That is a request this device cannot satisfy on its own
    -- it types only what it is told to -- so the dialog blocks until dismissed.
    Declaring the country is what stops the question being asked.

    (Do not put that explanation in an XML comment: `--` is illegal inside one,
    and BlueZ rejects the whole record with no useful error.)
    """
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1124" /></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100" /><uint16 value="0x0011" /></sequence>
      <sequence><uuid value="0x0011" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002" /></sequence>
  </attribute>
  <attribute id="0x0006">
    <sequence><uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" /></sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence><uuid value="0x1124" /><uint16 value="0x0100" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence>
      <sequence>
        <sequence><uuid value="0x0100" /><uint16 value="0x0013" /></sequence>
        <sequence><uuid value="0x0011" /></sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100"><text value="voicekb Keyboard" /></attribute>
  <attribute id="0x0101"><text value="Voice-driven HID keyboard" /></attribute>
  <attribute id="0x0102"><text value="voicekb" /></attribute>
  <attribute id="0x0200"><uint16 value="0x0100" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0x40" /></attribute>
  <attribute id="0x0203"><uint8 value="0x21" /></attribute>
  <attribute id="0x0204"><boolean value="false" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206">
    <sequence>
      <sequence>
        <uint8 value="0x22" />
        <text encoding="hex" value="{_descriptor_hex()}" />
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0207">
    <sequence>
      <sequence><uint16 value="0x0409" /><uint16 value="0x0100" /></sequence>
    </sequence>
  </attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="true" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
</record>"""


class BluetoothHIDKeyboard:
    """HID keyboard device: registers the SDP record and serves L2CAP channels."""

    def __init__(self, key_delay_s: float = 0.015) -> None:
        # ~15ms between reports. Applications drop characters when reports arrive
        # faster than their input loop polls; this is the documented safe range.
        self.key_delay_s = key_delay_s
        self._control_sock: socket.socket | None = None
        self._interrupt_sock: socket.socket | None = None
        self._control_conn: socket.socket | None = None
        self._interrupt_conn: socket.socket | None = None
        self._profile_registered = False
        self._lock = threading.Lock()

    # ---- SDP / BlueZ ----------------------------------------------------

    def register_profile(self) -> None:
        """Publish the HID SDP record so hosts see us as a keyboard."""
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()

        class _Profile(dbus.service.Object):
            """BlueZ requires a Profile1 object to exist at the registered path.

            We serve L2CAP ourselves rather than using the fd BlueZ would hand
            us, because HID needs two channels and Profile1 models only one.
            """

            @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
            def Release(self):  # noqa: N802
                pass

            @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
            def NewConnection(self, path, fd, properties):  # noqa: N802
                pass

            @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
            def RequestDisconnection(self, path):  # noqa: N802
                pass

        self._profile_obj = _Profile(bus, PROFILE_PATH)
        manager = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1"
        )
        manager.RegisterProfile(
            PROFILE_PATH,
            HID_UUID,
            {
                "ServiceRecord": sdp_record(),
                "Role": "server",
                "RequireAuthentication": dbus.Boolean(False),
                "RequireAuthorization": dbus.Boolean(False),
            },
        )
        self._profile_registered = True

        # BlueZ dispatches D-Bus calls only while a main loop runs.
        loop = GLib.MainLoop()
        threading.Thread(target=loop.run, daemon=True).start()

    # ---- L2CAP ----------------------------------------------------------

    def listen(self) -> None:
        """Bind and listen on both HID PSMs."""
        self._control_sock = self._bind(PSM_CONTROL)
        self._interrupt_sock = self._bind(PSM_INTERRUPT)

    @staticmethod
    def _bind(psm: int) -> socket.socket:
        sock = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP
        )
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((socket.BDADDR_ANY, psm))
        except OSError as exc:
            raise OSError(
                f"Could not bind L2CAP PSM {psm}: {exc}. "
                "If this is 'Address already in use', BlueZ's input plugin holds it -- "
                "run scripts/setup_bluetooth.sh. If it is 'Permission denied', run as root."
            ) from exc
        sock.listen(1)
        return sock

    def accept(self) -> str:
        """Block until a host connects both channels. Returns its address."""
        if self._control_sock is None or self._interrupt_sock is None:
            raise RuntimeError("listen() must be called before accept()")
        self._control_conn, cinfo = self._control_sock.accept()
        self._interrupt_conn, _ = self._interrupt_sock.accept()
        return cinfo[0]

    def connect_to(self, address: str, timeout_s: float = 10.0) -> bool:
        """Open both HID channels *outward* to a known host. True on success.

        Real Bluetooth keyboards initiate reconnection to their paired host
        rather than waiting to be called. Listening only is why a restarted
        daemon strands itself: macOS keeps the baseband ACL link open, believes
        it is still connected, and never reopens the L2CAP channels -- so the Pi
        sits in accept() indefinitely while the Mac shows a live connection.

        Connecting outward removes the dependency on the host noticing.
        """
        ctrl = intr = None
        try:
            ctrl = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP
            )
            ctrl.settimeout(timeout_s)
            ctrl.connect((address, PSM_CONTROL))

            intr = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP
            )
            intr.settimeout(timeout_s)
            intr.connect((address, PSM_INTERRUPT))
        except OSError:
            for s in (ctrl, intr):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
            return False

        # Back to blocking: send() must not time out mid-report.
        ctrl.settimeout(None)
        intr.settimeout(None)
        self._control_conn, self._interrupt_conn = ctrl, intr
        return True

    def connect_or_accept(self, known_host: str | None = None,
                          timeout_s: float = 10.0) -> str:
        """Try the host we know about first, then fall back to waiting."""
        if known_host and self.connect_to(known_host, timeout_s):
            return known_host
        return self.accept()

    @property
    def connected(self) -> bool:
        return self._interrupt_conn is not None

    # ---- Sending --------------------------------------------------------

    def send_report(self, report: bytes) -> None:
        if self._interrupt_conn is None:
            raise RuntimeError("no host connected")
        with self._lock:
            self._interrupt_conn.send(bytes([HIDP_DATA_INPUT, REPORT_ID]) + report)

    def tap(self, key: Key) -> None:
        """Press and release one key."""
        self.send_report(build_report(key))
        time.sleep(self.key_delay_s)
        self.send_report(build_report(None))
        time.sleep(self.key_delay_s)

    def type_text(self, text: str, skip_unmappable: bool = True) -> list[str]:
        """Type a string. Returns the characters that could not be sent."""
        skipped: list[str] = []
        for ch in text:
            key = key_for_char(ch)
            if key is None:
                skipped.append(ch)
                if skip_unmappable:
                    continue
                raise ValueError(f"cannot type character {ch!r}")
            self.tap(key)
        return skipped

    def press_named(self, name: str, modifiers: int = MOD_NONE, times: int = 1) -> bool:
        """Press a named key ("enter", "up") or a single character ("z").

        False if the name is unknown.
        """
        key = key_for_name(name, modifiers)
        if key is None and len(name) == 1:
            ch = key_for_char(name)
            if ch is not None:
                key = Key(ch.usage, ch.modifiers | modifiers)
        if key is None:
            return False
        for _ in range(times):
            self.tap(key)
        return True

    def close(self) -> None:
        for sock in (
            self._interrupt_conn, self._control_conn,
            self._interrupt_sock, self._control_sock,
        ):
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Bluetooth HID keyboard for the Pi")
    ap.add_argument("--serve", action="store_true",
                    help="register the profile, wait for a host, then type --text")
    ap.add_argument("--text", default="hello from voicekb\n",
                    help="text to type once a host connects")
    ap.add_argument("--delay-ms", type=float, default=15.0)
    ap.add_argument("--check", action="store_true",
                    help="validate the descriptor and SDP record without touching Bluetooth")
    args = ap.parse_args(argv)

    if args.check:
        print(f"report descriptor: {len(HID_REPORT_DESCRIPTOR)} bytes")
        print(f"descriptor hex   : {_descriptor_hex()[:64]}...")
        rec = sdp_record()
        print(f"sdp record       : {len(rec)} bytes")
        bad = unmappable(args.text)
        print(f"unmappable in --text: {bad if bad else 'none'}")
        return 0

    if not args.serve:
        ap.print_help()
        return 1

    kb = BluetoothHIDKeyboard(key_delay_s=args.delay_ms / 1000.0)
    try:
        print("registering HID profile ...")
        kb.register_profile()
        print("listening on L2CAP PSM 17 (control) and 19 (interrupt) ...")
        kb.listen()
        print("\nPair and connect from your Mac now.")
        print("  System Settings -> Bluetooth -> look for 'voicekb'\n")
        host = kb.accept()
        print(f"connected: {host}")
        time.sleep(1.0)  # let the host finish setting up its HID stack
        skipped = kb.type_text(args.text)
        print(f"typed {len(args.text)} chars"
              + (f", skipped {skipped}" if skipped else ""))
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        kb.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
