"""Power operations provided by systemd-logind over the system D-Bus."""

import logging

from gi.repository import Gio, GLib


class PowerManager:
    BUS_NAME = "org.freedesktop.login1"
    OBJECT_PATH = "/org/freedesktop/login1"
    INTERFACE = "org.freedesktop.login1.Manager"

    def __init__(self) -> None:
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,
            self.BUS_NAME,
            self.OBJECT_PATH,
            self.INTERFACE,
            None,
        )

    def invoke(self, operation: str) -> None:
        methods = {
            "suspend": "Suspend",
            "reboot": "Reboot",
            "poweroff": "PowerOff",
        }
        method = methods.get(operation)
        if method is None:
            raise ValueError(f"Unknown power operation: {operation}")
        logging.info("Requesting logind operation %s", method)
        self._proxy.call(
            method,
            GLib.Variant("(b)", (True,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_finished,
            method,
        )

    def available(self, operation: str) -> bool:
        methods = {
            "suspend": "CanSuspend",
            "reboot": "CanReboot",
            "poweroff": "CanPowerOff",
        }
        method = methods.get(operation)
        if method is None:
            return False
        try:
            result = self._proxy.call_sync(
                method,
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except GLib.Error:
            return False
        return result.unpack()[0] in {"yes", "challenge"}

    @staticmethod
    def _on_finished(proxy, result, method: str) -> None:
        try:
            proxy.call_finish(result)
        except GLib.Error as error:
            logging.error("logind %s failed: %s", method, error.message)
