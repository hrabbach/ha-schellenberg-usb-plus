"""API for Schellenberg USB Stick."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from typing import Any

import serial_asyncio_fast as serial_asyncio
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CMD_ALLOW_PAIRING,
    CMD_DOWN,
    CMD_ECHO_OFF,
    CMD_ECHO_ON,
    CMD_ENTER_BOOTLOADER,
    CMD_ENTER_INITIAL,
    CMD_GET_DEVICE_ID,
    CMD_GET_PARAM_P,
    CMD_LED_BLINK_1,
    CMD_LED_BLINK_2,
    CMD_LED_BLINK_3,
    CMD_LED_BLINK_4,
    CMD_LED_BLINK_5,
    CMD_LED_BLINK_6,
    CMD_LED_BLINK_7,
    CMD_LED_BLINK_8,
    CMD_LED_BLINK_9,
    CMD_LED_OFF,
    CMD_LED_ON,
    CMD_MANUAL_DOWN,
    CMD_MANUAL_UP,
    CMD_PAIR,
    CMD_REBOOT,
    CMD_SET_LOWER_ENDPOINT,
    CMD_SET_UPPER_ENDPOINT,
    CMD_STOP,
    CMD_TRANSMIT,
    CMD_UP,
    CMD_VERIFY,
    DEVICE_ID_TIMEOUT,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_MISS_THRESHOLD,
    HEARTBEAT_TRAFFIC_WINDOW,
    LEARN_REMOTE_TIMEOUT,
    MAX_DEVICE_ENUM,
    PAIRING_DEVICE_ENUM_START,
    PAIRING_TIMEOUT,
    RECONNECT_BACKOFF_BASE,
    RECONNECT_BACKOFF_CAP,
    REMOTE_DEDUP_WINDOW,
    RETRY_DELAY,
    RETRY_QUEUE_CAP,
    SIGNAL_DEVICE_EVENT,
    SIGNAL_REMOTE_EVENT,
    SIGNAL_STICK_STATUS_UPDATED,
    VERIFY_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class DeviceLimitReached(Exception):
    """Raised when all device enum slots (0x10–0xFF) are occupied."""


class SchellenbergUsbApi:
    """Manages all communication with the Schellenberg USB stick."""

    def __init__(self, hass: HomeAssistant, port: str) -> None:
        """Initialize the Schellenberg USB API."""
        self.hass = hass
        self.port = port
        self._transport: asyncio.Transport | None = None
        self._protocol: SchellenbergProtocol | None = None
        self._registered_devices: dict[
            str, str
        ] = {}  # Dict[device_id, device_enum] for registered entities
        self._is_connecting = False
        self._pairing_future: asyncio.Future[str] | None = None
        self._stop_pairing_task: asyncio.Task[None] | None = (
            None  # Track task to stop pairing
        )

        # USB stick status
        self._is_connected = False
        self._device_version: str | None = None
        self._device_mode: str | None = None  # boot, initial, or listening
        self._verify_future: asyncio.Future[bool] | None = None
        self._device_id_future: asyncio.Future[str] | None = None
        self._hub_id: str | None = None

        # Retry queue for commands that failed with "stick busy"
        self._in_flight_command: str | None = None
        self._retry_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=RETRY_QUEUE_CAP)
        self._retry_worker_task: asyncio.Task[None] | None = None

        # Heartbeat for frozen-stick detection
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_traffic_time: float = 0.0  # 0.0 sentinel; set on connect

        # Reconnect backoff
        self._reconnect_attempts: int = 0
        # Stored TimerHandle so teardown can cancel a pending reconnect (CR-01).
        self._reconnect_handle: asyncio.TimerHandle | None = None
        # Teardown latch: set by disconnect(); connect() is a no-op when True (CR-01).
        self._closed: bool = False

        # Hub options (live-applied from entry.options by __init__.py)
        self._ignore_unknown: bool = False

        # Remote coexistence (v1.3)
        # reverse-lookup: remote_device_id → motor_device_id
        self._remote_to_motor: dict[str, str] = {}
        self._learn_remote_future: asyncio.Future[str] | None = None
        # Incrementor dedup cache: (device_id, incrementor) → loop.time() of last seen
        self._dedup_cache: dict[tuple[str, str], float] = {}
        # TimerHandles for dedup quiet-period resets (keyed by (device_id, incrementor))
        self._dedup_handles: dict[tuple[str, str], asyncio.TimerHandle] = {}

    def _compute_reconnect_delay(self) -> float:
        """Return next reconnect delay: truncated exponential backoff, equal jitter.

        Sequence (attempt 0..N): 5, 10, 20, 40, 80, 160, 300, 300, ...
        Each value is half fixed + half random (equal jitter).
        Caller increments _reconnect_attempts; reset to 0 on successful connect.
        """
        raw = min(
            RECONNECT_BACKOFF_BASE * (2**self._reconnect_attempts),
            RECONNECT_BACKOFF_CAP,
        )
        jitter = random.uniform(0, raw / 2)
        return raw / 2 + jitter

    def _reconnect_fire(self) -> None:
        """Fired by the TimerHandle callback; clear the handle and start connect()."""
        self._reconnect_handle = None
        self.hass.async_create_task(self.connect())

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt via the shared backoff helper.

        This is the single reconnect chokepoint — all reconnect sites
        (connect() failure, connection_lost, heartbeat frozen-stick branch)
        must route through here.

        No-op when:
        - _closed is True (teardown has latched; never reschedule after unload).
        - _is_connecting is True (a connect() is already in flight).
        - _reconnect_handle is not None (a reconnect is already armed; prevents
          _reconnect_attempts double-increment and fan-out — folds WR-04).

        Uses hass.loop (not asyncio.get_running_loop()) so it is safe to call
        from transport callbacks where no running loop is guaranteed.
        """
        if self._closed:
            return
        if self._is_connecting:
            return
        if self._reconnect_handle is not None:
            return
        delay = self._compute_reconnect_delay()
        self._reconnect_attempts += 1
        self._reconnect_handle = self.hass.loop.call_later(delay, self._reconnect_fire)

    async def connect(self) -> None:
        """Establish a connection to the serial port."""
        # Guard: once disconnect() has latched _closed, a reconnect callback that
        # fires in the same tick must not reopen the port (CR-01).
        if self._closed:
            return
        if self._is_connecting or (
            self._transport and not self._transport.is_closing()
        ):
            _LOGGER.debug("Connection attempt already in progress or established")
            return

        self._is_connecting = True
        _LOGGER.info("Connecting to Schellenberg USB stick at %s", self.port)
        try:
            (
                self._transport,
                self._protocol,
            ) = await serial_asyncio.create_serial_connection(  # type: ignore[assignment]
                asyncio.get_running_loop(),
                lambda: SchellenbergProtocol(self._handle_message, self),
                self.port,
                baudrate=112500,
            )
            _LOGGER.info("Successfully connected to Schellenberg USB stick")

            # Verify this is a Schellenberg device
            if not await self.verify_device():
                _LOGGER.error(
                    "Device verification failed - not a Schellenberg USB stick"
                )
                if self._transport:
                    self._transport.close()
                self._transport = None
                self._is_connected = False
                return

            self._is_connected = True
            self._update_status()

            # Enter listening mode if not already in it
            if self._device_mode != "listening":
                _LOGGER.info(
                    "Device is in %s mode, entering listening mode", self._device_mode
                )
                # Send any lowercase command to enter listening mode (B:2)
                await self.send_command("hello")
                # Give the device a moment to switch modes
                await asyncio.sleep(0.5)
                # Update the mode to listening after sending the command
                self._device_mode = "listening"
                self._update_status()
                _LOGGER.info("Device now in listening mode")
            else:
                _LOGGER.info("Device already in listening mode")

            # Get the hub device ID after listening mode
            hub_id = await self.get_device_id()
            if hub_id:
                self._hub_id = hub_id
                _LOGGER.info("Hub device ID retrieved: %s", self._hub_id)
            else:
                _LOGGER.warning("Failed to retrieve hub device ID")

            # Reset backoff on successful connect
            self._reconnect_attempts = 0
            self._reconnect_handle = (
                None  # clear any armed handle (belt-and-suspenders)
            )
            self._last_traffic_time = self.hass.loop.time()

            # Start the retry worker and heartbeat tasks
            self._retry_worker_task = self.hass.async_create_task(
                self._retry_worker(),
                name="schellenberg_retry_worker",
            )
            self._heartbeat_task = self.hass.async_create_task(
                self._heartbeat_worker(),
                name="schellenberg_heartbeat",
            )
        except (serial_asyncio.serial.SerialException, OSError) as err:
            _LOGGER.error(
                "Failed to connect to %s: %s. Retrying with backoff",
                self.port,
                err,
            )
            # Route through the shared single-flight helper: increments
            # _reconnect_attempts once and stores the TimerHandle (CR-01).
            self._schedule_reconnect()
        finally:
            self._is_connecting = False

    @callback
    def _handle_message(self, message: str) -> None:
        """Handle incoming messages from the protocol."""
        _LOGGER.debug("Received raw message: %s", message)

        # Update inbound traffic timestamp — all inbound frames are real traffic
        self._last_traffic_time = self.hass.loop.time()

        # Handle device verification response (format: RFTU_V20 F:20180510_DFBD B:1)
        # RFTU_V20 = device type and version
        # F: = firmware date
        # B: = boot mode (0 = bootloader, 1 = initial/normal)
        # Note: Listening mode (B:2) is entered by sending a lowercase command in B:1
        if message.startswith("RFTU_"):
            parts = message.split()
            if parts:
                self._device_version = parts[0]  # RFTU_V20
                # Extract boot mode if present
                for part in parts:
                    if part.startswith("B:"):
                        boot_mode = part[2:]
                        if boot_mode == "0":
                            self._device_mode = "bootloader"
                        elif boot_mode == "1":
                            self._device_mode = "initial"
                        else:
                            self._device_mode = "unknown"
                        break
                else:
                    self._device_mode = "initial"

                _LOGGER.info(
                    "Device verified: version=%s, mode=%s",
                    self._device_version,
                    self._device_mode,
                )
                self._safe_resolve_future(self._verify_future, True)
                self._update_status()
            return

        # Handle acknowledgments
        if message in ("t1", "t0"):
            _LOGGER.debug("Transmit ACK: %s", message)
            # Protocol is half-duplex; a duplicate tE is not expected. Clearing
            # the in-flight slot on ack is a cheap guard against enqueuing an
            # already-acked command if a spurious/late tE ever arrives
            # (review finding 4).
            self._in_flight_command = None
            return

        if message == "tE":
            cmd = self._in_flight_command
            self._in_flight_command = None
            if cmd is not None:
                try:
                    self._retry_queue.put_nowait(cmd)
                    _LOGGER.warning(
                        "Stick busy (tE) — command queued for retry: %s", cmd
                    )
                except asyncio.QueueFull:
                    _LOGGER.warning(
                        "Retry backlog full (cap=%d) — dropping command: %s",
                        RETRY_QUEUE_CAP,
                        cmd,
                    )
            return

        # Handle device ID response (format: sr5D3E7C where 5D3E7C is the device ID)
        if message.startswith("sr") and len(message) >= 8:
            device_id = message[2:8]
            _LOGGER.debug("Received device ID response: %s", device_id)
            self._safe_resolve_future(self._device_id_future, device_id)
            return

        # Handle pairing/list responses (format: sl00BEXXXXXX...)
        # sl = list/pairing response prefix
        # 00BE = 2 bytes to ignore (address prefix)
        # XXXXXX = 3 bytes device ID (the actual device ID we want)
        # Rest = can be ignored
        # Guard: slice [6:12] requires len >= 12 (end index = 12). The previous
        # >= 8 guard was a defect (Pattern 2 / T-05-04) — on an 8-11 char frame
        # the slice silently returns a truncated/empty device_id.
        if message.startswith("sl") and len(message) >= 12:
            # Extract the device ID: skip "sl" (2 chars) + "00BE" (4 chars) = 6 chars
            # Then take the next 6 characters (3 bytes as hex) = 6 chars
            device_id = message[6:12]
            _LOGGER.debug(
                "Received pairing/list response: %s, extracted device ID: %s",
                message,
                device_id,
            )
            _LOGGER.debug(
                "Pairing mode active: %s",
                self._pairing_future is not None and not self._pairing_future.done(),
            )

            # If we're in pairing mode, accept ANY device response
            # because the user is explicitly trying to pair RIGHT NOW
            if self._pairing_future and not self._pairing_future.done():
                _LOGGER.info("Pairing successful! New device ID: %s", device_id)
                self._safe_resolve_future(self._pairing_future, device_id)
                # Stop pairing mode after a 2 second delay to ensure device has fully paired
                self._stop_pairing_task = asyncio.create_task(
                    self._stop_pairing_mode(delay=True)
                )
                self._stop_pairing_task.add_done_callback(
                    lambda _: setattr(self, "_stop_pairing_task", None)
                )
                # Don't send dispatcher signal here - let the caller handle persistence
                return
            return

        # Handle Schellenberg device messages
        # Format: ssXXYYYYYYZZZZCCPPRR
        # ss = prefix (2 chars)
        # XX = device enum (2 chars)
        # YYYYYY = device ID (6 chars)
        # ZZZZ = message incrementor (4 chars, ignored)
        # CC = command (2 chars)
        # PP = padding (2 chars, ignored)
        # RR = signal strength (2 chars, ignored)
        if message.startswith("ss") and len(message) >= 18:
            try:
                device_enum = message[2:4]
                device_id = message[4:10]
                incrementor = message[10:14]
                command = message[14:16]
                # Capture frame-decode instant for the remote-event payload.
                # Uses time.monotonic() so the timestamp shares the same epoch
                # as PositionTracker.calculate, enabling accurate elapsed-time
                # computation in the cover entity (Plan 02, D-06).
                receive_timestamp = time.monotonic()

                _LOGGER.debug(
                    "Parsed: enum=%s, id=%s, incr=%s, cmd=%s",
                    device_enum, device_id, incrementor, command,
                )

                # [GATE 1] — pairing-future: unchanged from existing code
                # Gate 1 intentionally PRECEDES Gate 2 (dedup): the pairing
                # path captures a new device ID on the first frame and is
                # therefore NOT dedup-suppressed. This ordering is by design
                # (HA opens one pairing window at a time and the user is
                # deliberately pairing) and is not "corrected" later — do not
                # move dedup ahead of it (WR-04).
                if self._pairing_future and not self._pairing_future.done():
                    if device_id not in self._registered_devices:
                        _LOGGER.info(
                            "Pairing successful! New device ID: %s", device_id
                        )
                        self._safe_resolve_future(self._pairing_future, device_id)
                        # Stop pairing mode after a 2 second delay to ensure
                        # device has fully paired
                        self._stop_pairing_task = asyncio.create_task(
                            self._stop_pairing_mode(delay=True)
                        )
                        self._stop_pairing_task.add_done_callback(
                            lambda _: setattr(self, "_stop_pairing_task", None)
                        )
                        # Don't send dispatcher signal — let the caller handle
                        return

                # Compute-once: remote routing discriminator + dedup-scope flags
                motor_id = self._remote_to_motor.get(device_id)
                is_remote = motor_id is not None
                is_learning = (
                    self._learn_remote_future is not None
                    and not self._learn_remote_future.done()
                )

                # [GATE 2] — incrementor dedup (RMT-06)
                # Scoped to remote/learning frames only (review finding #1):
                # a registered MOTOR frame skips this gate entirely and is
                # never suppressed, even if it reuses an incrementor.
                # WR-12-02: STOP frames also bypass dedup. STOP is idempotent
                # and rare; suppressing a duplicate STOP is harmless, but
                # suppressing a real STOP that shares an incrementor with a
                # preceding UP/DOWN burst (same press window) would leave HA
                # tracking toward an endstop while the shutter has stopped — a
                # user-visible position divergence. Never gate STOP on dedup.
                if (is_remote or is_learning) and command != CMD_STOP:
                    dedup_key = (device_id, incrementor)
                    now = self.hass.loop.time()
                    cached_time = self._dedup_cache.get(dedup_key)
                    # The dedup window is intentionally anchored to the FIRST
                    # frame of a burst (NOT a sliding window): a suppressed
                    # repeat does NOT refresh the timestamp or reschedule the
                    # reset timer below. A same-incrementor repeat that arrives
                    # after REMOTE_DEDUP_WINDOW is treated as a NEW press by
                    # design (covers a normal ~0.5s 9-frame burst). Do not
                    # convert this to a sliding window (WR-02).
                    if (
                        cached_time is not None
                        and (now - cached_time) < REMOTE_DEDUP_WINDOW
                    ):
                        _LOGGER.debug(
                            "Dedup: suppressing RF repeat from %s"
                            " incr=%s (%.3fs ago)",
                            device_id, incrementor, now - cached_time,
                        )
                        return
                    old_handle = self._dedup_handles.pop(dedup_key, None)
                    if old_handle is not None:
                        old_handle.cancel()
                    self._dedup_cache[dedup_key] = now

                    def _reset_dedup_entry(
                        key: tuple[str, str] = dedup_key,
                    ) -> None:
                        self._dedup_cache.pop(key, None)
                        self._dedup_handles.pop(key, None)

                    self._dedup_handles[dedup_key] = self.hass.loop.call_later(
                        REMOTE_DEDUP_WINDOW, _reset_dedup_entry
                    )

                # [GATE 3] — remote routing: triple dispatch (RMT-07)
                # Reuses motor_id already computed above (no double lookup).
                if motor_id is not None:
                    _LOGGER.debug(
                        "Remote %s → motor %s (enum=%s): bridging cmd=%s",
                        device_id, motor_id, device_enum, command,
                    )
                    async_dispatcher_send(
                        self.hass,
                        f"{SIGNAL_DEVICE_EVENT}_{device_id}",
                        command,
                    )
                    async_dispatcher_send(
                        self.hass,
                        f"{SIGNAL_DEVICE_EVENT}_{motor_id}",
                        command,
                    )
                    async_dispatcher_send(
                        self.hass,
                        f"{SIGNAL_REMOTE_EVENT}_{motor_id}",
                        command,
                        receive_timestamp,
                    )
                    return  # MANDATORY — does not reach final dispatch

                # [GATE 4] — learn-window: resolve future on first unknown device
                if device_id not in self._registered_devices:
                    if (
                        self._learn_remote_future
                        and not self._learn_remote_future.done()
                    ):
                        _LOGGER.info(
                            "learn_remote_and_wait: captured unknown"
                            " device %s", device_id
                        )
                        self._safe_resolve_future(
                            self._learn_remote_future, device_id
                        )
                        return  # suppress warning while in learn window
                    if self._ignore_unknown:
                        # "Ignore unknown signals" hub option on — demote to DEBUG
                        _LOGGER.debug(
                            "Ignoring signal from unknown device"
                            " %s (enum=%s, cmd=%s)",
                            device_id, device_enum, command,
                        )
                    else:
                        _LOGGER.warning(
                            "Received message for device %s (enum=%s, cmd=%s)"
                            " but no corresponding entity found. The device"
                            " may need to be added to Home Assistant",
                            device_id, device_enum, command,
                        )
                else:
                    # The entity will handle the event via the dispatcher
                    _LOGGER.debug(
                        "Forwarding event to device %s (enum=%s): command=%s",
                        device_id, device_enum, command,
                    )

                # [EXISTING FINAL DISPATCH — unchanged; reached only for registered
                #  motors and unknown devices (non-remote, non-learning).
                #  RMT-07 compliance: remote frames never reach here.]
                async_dispatcher_send(
                    self.hass, f"{SIGNAL_DEVICE_EVENT}_{device_id}", command
                )
            except (IndexError, ValueError) as err:
                _LOGGER.debug("Failed to parse message %s: %s", message, err)

    async def send_command(self, command: str, *, track_traffic: bool = True) -> None:
        """Send a command to the USB stick."""
        if self._transport is None or self._transport.is_closing():
            _LOGGER.warning("Serial port not connected. Command dropped: %s", command)
            return

        # Capture in-flight command BEFORE write — no await between capture and
        # write (single event-loop tick guarantee for tE correlation).
        self._in_flight_command = command

        full_command = f"{command}\r\n".encode("ascii")
        _LOGGER.debug("Sending to serial device: %s", full_command.strip())
        self._transport.write(full_command)
        if track_traffic:
            # Stamp outbound traffic timestamp. Heartbeat probe passes
            # track_traffic=False so it does not feed its own skip window
            # (review finding 2).
            self._last_traffic_time = self.hass.loop.time()
        _LOGGER.debug("Command sent to serial device: %s", full_command.strip())

    async def _retry_worker(self) -> None:
        """Drain the retry queue, re-sending each command via the normal path."""
        try:
            while True:
                command = await self._retry_queue.get()
                await asyncio.sleep(RETRY_DELAY)
                _LOGGER.debug("Retry worker re-sending: %s", command)
                await self.send_command(command)
                self._retry_queue.task_done()
        except asyncio.CancelledError:
            _LOGGER.debug("Retry worker cancelled")
            raise  # always re-raise so the task exits cleanly

    async def _heartbeat_worker(self) -> None:
        """Periodic frozen-stick detection via CMD_VERIFY."""
        miss_count = 0
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self._is_connecting or not self._is_connected:
                    continue
                elapsed = self.hass.loop.time() - self._last_traffic_time
                if elapsed < HEARTBEAT_TRAFFIC_WINDOW:
                    _LOGGER.debug("Heartbeat skip — traffic %.1fs ago", elapsed)
                    miss_count = 0
                    continue
                _LOGGER.debug("Heartbeat probe — sending CMD_VERIFY")
                ok = await self.verify_device(heartbeat_probe=True)
                if ok:
                    miss_count = 0
                else:
                    miss_count += 1
                    _LOGGER.warning(
                        "Heartbeat miss %d/%d — stick unresponsive",
                        miss_count,
                        HEARTBEAT_MISS_THRESHOLD,
                    )
                    if miss_count >= HEARTBEAT_MISS_THRESHOLD:
                        _LOGGER.error(
                            "Frozen stick detected (%d consecutive misses)"
                            " — marking disconnected and scheduling reconnect",
                            HEARTBEAT_MISS_THRESHOLD,
                        )
                        self.update_connection_status(False)
                        # Close the frozen-but-open transport (CR-02): a frozen
                        # stick keeps the OS port open so connection_lost never
                        # fires — we must drive recovery ourselves.  Closing the
                        # transport is synchronous (no await) so the single-tick
                        # self-cancel safety window is preserved (WR-01).
                        if self._transport is not None:
                            self._transport.close()
                            self._transport = None
                        # Schedule reconnect through the shared single-flight
                        # chokepoint.  If connection_lost fires later on the
                        # just-closed transport, _schedule_reconnect no-ops
                        # because a handle is already pending (WR-04).
                        self._schedule_reconnect()
                        return  # exits before CancelledError can inject
        except asyncio.CancelledError:
            _LOGGER.debug("Heartbeat worker cancelled")
            raise

    async def pair_device_and_wait(self) -> tuple[str, str] | None:
        """Put the stick into pairing mode and wait for a device to pair.

        Returns a tuple of (device_id, device_enum) if successful, None if timeout.
        """
        if self._pairing_future and not self._pairing_future.done():
            # Architecturally impossible: HA runs one subentry flow at a time, so two
            # concurrent calls cannot happen in practice. The None return here would cause
            # config_flow to abort with "pairing_timeout", which is misleading. Document
            # rather than raise a distinct exception — the guard is a safety net only.
            _LOGGER.warning("Pairing already in progress")
            return None

        # Get the next available device enumerator
        device_enum = self.initialize_next_device_enum()

        # Raise before formatting pair_command (so "ssNone9..." is never built)
        # and before create_future() (so no dangling future is left) — D-02.
        if device_enum is None:
            _LOGGER.warning("Device enum limit reached - cannot pair new device")
            raise DeviceLimitReached

        # Format: ssXX9CCPPPP
        # ss = transmit prefix
        # XX = device enumerator (2 hex chars)
        # 9 = number of messages to send
        # CC = command (60 = pair)
        # PPPP = padding (4 chars)
        pair_command = f"{CMD_TRANSMIT}{device_enum}9{CMD_PAIR}0000"

        _LOGGER.info(
            "Initiating pairing with device enum %s. Command: %s",
            device_enum,
            pair_command,
        )

        # Create a future to wait for device ID first
        self._pairing_future = asyncio.get_running_loop().create_future()

        try:
            # Send sp command to enter pairing/listening mode (like C# does)
            _LOGGER.debug("Entering pairing mode with command: sp")
            await self.send_command(CMD_GET_PARAM_P)

            # Wait for device to send its ID first (with timeout)
            device_id = await asyncio.wait_for(
                self._pairing_future, timeout=PAIRING_TIMEOUT
            )

            # Once we have the device ID, send the pairing command
            _LOGGER.debug(
                "Received device ID %s, sending pairing command: %s",
                device_id,
                pair_command,
            )
            await self.send_command(pair_command)
        except TimeoutError:
            _LOGGER.warning("Pairing timeout - no device responded with ID")
            return None
        except ConnectionError:
            _LOGGER.warning("Pairing aborted - serial port disconnected")
            return None
        else:
            # Pairing successful - return the device ID and enum
            _LOGGER.info(
                "Pairing completed successfully: %s with device enum %s",
                device_id,
                device_enum,
            )
            return (device_id, device_enum)
        finally:
            self._pairing_future = None

    async def _stop_pairing_mode(self, delay: bool = False) -> None:
        """Stop pairing mode by sending a stop command to the stick.

        Args:
            delay: If True, wait 2 seconds before stopping to ensure device has fully paired.
        """
        try:
            if delay:
                # Wait 2 seconds before stopping pairing mode to ensure device has fully paired
                await asyncio.sleep(2)
            _LOGGER.debug("Stopping pairing mode with command: sp")
            await self.send_command(CMD_GET_PARAM_P)
            _LOGGER.info("Pairing mode stopped")
        except asyncio.CancelledError:
            _LOGGER.debug("Stop-pairing task cancelled during teardown")
            raise  # always re-raise CancelledError so the task terminates cleanly
        except OSError as err:
            # send_command may raise OSError if the transport was closed; this is
            # expected during teardown when disconnect() races with the delay sleep.
            _LOGGER.debug("Error stopping pairing mode (communication error): %s", err)

    async def control_blind(self, device_enum: str, action: str) -> None:
        """Send a control command to a specific blind.

        Args:
            device_enum: The device enumerator (hex string like "10")
            action: Command (CMD_UP, CMD_DOWN, CMD_STOP)

        """
        if action not in (CMD_UP, CMD_DOWN, CMD_STOP):
            _LOGGER.error("Invalid blind action: %s", action)
            return

        # Format: ssXX9AAZZZ
        # XX = device enum, 9 = number of messages, AA = command, ZZZ = padding
        command = f"{CMD_TRANSMIT}{device_enum}9{action}0000"
        _LOGGER.debug("Sending blind control: %s", command)
        await self.send_command(command)

    def initialize_next_device_enum(self) -> str | None:
        """Get the next available device enum based on registered devices.

        Returns the lowest free device enumerator as a hex string (e.g., "10"),
        or None when all slots 0x10–0xFF are occupied (no wraparound).

        Reclaims slots freed by removed devices instead of burning enums
        over add/remove cycles (D-01/D-02).
        """
        # Build the set of currently-used enum values (skip malformed entries)
        used: set[int] = set()
        for device_enum in self._registered_devices.values():
            try:
                used.add(int(device_enum, 16))
            except (ValueError, TypeError):
                pass  # malformed enum: skip silently

        # Scan for the lowest free slot in the valid range
        for slot in range(PAIRING_DEVICE_ENUM_START, MAX_DEVICE_ENUM + 1):
            if slot not in used:
                result = f"{slot:02X}"
                # If the chosen slot is below the current high-water mark,
                # it is a reclaimed gap from a previously-removed device.
                # Emit an operator hint so they can factory-reset any still-
                # powered motor on that slot (T-07-05 partial mitigation).
                if used and slot < max(used):
                    _LOGGER.info(
                        "Reclaiming previously-used device enum %s"
                        " - if the old motor on this slot is still powered,"
                        " factory-reset it to avoid stale status frames",
                        result,
                    )
                _LOGGER.debug("Next device enum: %s", result)
                return result

        # All 240 slots are occupied — surface the limit, do not wrap
        _LOGGER.warning(
            "Device enum limit reached: all slots %02X-%02X are occupied",
            PAIRING_DEVICE_ENUM_START,
            MAX_DEVICE_ENUM,
        )
        return None

    def register_existing_devices(self, devices: list[dict]) -> None:
        """Register existing devices from storage.

        Args:
            devices: List of device dicts with 'id' and 'enum' keys
        """
        for device in devices:
            device_id = device.get("id")
            device_enum = device.get("enum")
            if device_id and device_enum:
                self._registered_devices[device_id] = device_enum
                _LOGGER.debug(
                    "Registered existing device %s with enum %s", device_id, device_enum
                )

    def remove_known_device(self, device_id: str) -> None:
        """Remove a device from the registered entities.

        After removal, messages from this device will be treated as unknown.
        """
        self._registered_devices.pop(device_id, None)
        _LOGGER.debug("Removed device %s from registered entities", device_id)

    def register_entity(self, device_id: str, device_enum: str) -> None:
        """Register that an entity exists for this device ID with its enum."""
        self._registered_devices[device_id] = device_enum
        _LOGGER.debug(
            "Registered entity for device %s with enum %s", device_id, device_enum
        )

    def register_remote(
        self, remote_id: str, motor_id: str, motor_enum: str
    ) -> None:
        """Register a remote-to-motor binding.

        Called from cover_entity.async_added_to_hass (Phase 12) when subentry
        carries CONF_REMOTE_ID. Adds the remote to _registered_devices so
        inbound frames from it are recognized as known (suppressing the
        unknown-device warning), and populates the reverse-lookup for triple
        dispatch.

        The remote is stored in _registered_devices with the MOTOR's enum —
        this suppresses the "unknown device" warning for the remote's own
        frames without burning a new enum slot (the motor's enum is already
        present; de-duplication in initialize_next_device_enum's set
        eliminates the collision). _remote_to_motor is the authoritative
        discriminator between remote and motor frames; do NOT rely on
        _registered_devices to tell them apart.
        """
        # Observability only (WR-03): warn when an existing remote→motor
        # binding is overwritten with a DIFFERENT motor, so a misconfig
        # (re-bind, or a double async_added_to_hass) leaves a log trail.
        # This does NOT reject or alter the binding — binding policy is
        # deliberately deferred to Phase 15 (D-07).
        existing = self._remote_to_motor.get(remote_id)
        if existing is not None and existing != motor_id:
            _LOGGER.warning(
                "Remote %s re-bound from motor %s to %s",
                remote_id, existing, motor_id,
            )
        self._registered_devices[remote_id] = motor_enum
        self._remote_to_motor[remote_id] = motor_id
        _LOGGER.debug(
            "Registered remote %s → motor %s (enum=%s)",
            remote_id, motor_id, motor_enum,
        )

    def unregister_remote(self, remote_id: str) -> None:
        """Remove a remote binding.

        Called from cover_entity.async_will_remove_from_hass (Phase 12).
        """
        self._registered_devices.pop(remote_id, None)
        self._remote_to_motor.pop(remote_id, None)
        _LOGGER.debug("Unregistered remote %s", remote_id)

    async def learn_remote_and_wait(
        self, timeout: float = LEARN_REMOTE_TIMEOUT
    ) -> str | None:
        """Open a listening window; return the first unknown device_id seen, or None.

        "Unknown" means NOT in _registered_devices (not an enrolled motor,
        not an already-registered remote). All binding policy (reject
        already-a-motor, already-bound-elsewhere) lives in Phase 15's flow.

        Returns the raw 6-char hex device_id string, or None on timeout or
        disconnect.
        """
        if self._learn_remote_future and not self._learn_remote_future.done():
            _LOGGER.warning("learn_remote_and_wait already in progress")
            return None

        self._learn_remote_future = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(
                self._learn_remote_future, timeout=timeout
            )
        except TimeoutError:
            _LOGGER.debug("learn_remote_and_wait: timeout after %.1fs", timeout)
            return None
        except ConnectionError:
            _LOGGER.warning(
                "learn_remote_and_wait aborted — serial port disconnected"
            )
            return None
        finally:
            self._learn_remote_future = None

    async def verify_device(self, *, heartbeat_probe: bool = False) -> bool:
        """Verify this is a Schellenberg USB stick by sending !? command.

        Returns True if verification succeeds, False otherwise.
        """
        if self._verify_future and not self._verify_future.done():
            _LOGGER.warning("Device verification already in progress")
            return False

        _LOGGER.debug("Verifying Schellenberg USB device")
        self._verify_future = asyncio.get_running_loop().create_future()

        try:
            # Heartbeat probe is exempt from the outbound traffic stamp so it
            # does not feed its own skip window (review finding 2).
            await self.send_command(CMD_VERIFY, track_traffic=not heartbeat_probe)

            # Wait for verification response with timeout
            result = await asyncio.wait_for(self._verify_future, timeout=VERIFY_TIMEOUT)
        except TimeoutError:
            _LOGGER.error("Device verification timeout - device did not respond to !?")
            return False
        except ConnectionError:
            _LOGGER.warning("Device verification aborted - serial port disconnected")
            return False
        else:
            _LOGGER.info("Device verification successful")
            return result
        finally:
            self._verify_future = None

    def _safe_resolve_future(
        self,
        future: asyncio.Future[Any] | None,
        result: Any = None,
        *,
        exception: BaseException | None = None,
    ) -> None:
        """Resolve a future safely, ignoring already-done futures.

        Guards against asyncio.InvalidStateError from double-resolution
        (e.g. a late serial frame landing in the same tick as a disconnect).
        """
        if future is None or future.done():
            return
        if exception is not None:
            future.set_exception(exception)
        else:
            future.set_result(result)

    @callback
    def _update_status(self) -> None:
        """Update device status and notify listeners."""
        async_dispatcher_send(self.hass, SIGNAL_STICK_STATUS_UPDATED)

    def update_connection_status(self, connected: bool) -> None:
        """Update connection status (called from protocol)."""
        if not connected:
            # Teardown is naturally idempotent (review finding 1):
            # - cancel on None/done task is skipped by the guard
            # - drain on empty queue: while loop exits immediately
            # - _safe_resolve_future on None/done future: returns early
            # So heartbeat-timeout, connection_lost, and disconnect() can all
            # call this in any order without double-draining or double-cancelling.
            self._is_connected = False
            # Cancel tasks before draining queue (D-05 ordering)
            if self._retry_worker_task and not self._retry_worker_task.done():
                self._retry_worker_task.cancel()
            self._retry_worker_task = None
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            self._heartbeat_task = None
            # Cancel any pending reconnect callback so a mid-backoff disconnect
            # does not reopen the port after teardown (CR-01).
            if self._reconnect_handle is not None:
                self._reconnect_handle.cancel()
                self._reconnect_handle = None
            # Drain stale commands so they don't replay on reconnect (SC#2)
            while not self._retry_queue.empty():
                try:
                    self._retry_queue.get_nowait()
                    self._retry_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            # Fail all pending futures immediately so suspended flows return
            # within seconds instead of hanging for the full timeout (D-10).
            err = ConnectionError("Serial port disconnected")
            self._safe_resolve_future(self._pairing_future, exception=err)
            self._safe_resolve_future(self._verify_future, exception=err)
            self._safe_resolve_future(self._device_id_future, exception=err)
            self._safe_resolve_future(self._learn_remote_future, exception=err)
            # Teardown dedup timers (idempotent — clearing an empty dict is a no-op)
            for handle in self._dedup_handles.values():
                handle.cancel()
            self._dedup_handles.clear()
            self._dedup_cache.clear()
        else:
            self._is_connected = True
        self._update_status()

    @property
    def is_connected(self) -> bool:
        """Return whether the USB stick is connected."""
        return self._is_connected

    @property
    def device_version(self) -> str | None:
        """Return the device firmware version."""
        return self._device_version

    @property
    def device_mode(self) -> str | None:
        """Return the device mode (boot, initial, or listening)."""
        return self._device_mode

    @property
    def hub_id(self) -> str | None:
        """Return the hub device ID."""
        return self._hub_id

    @property
    def ignore_unknown(self) -> bool:
        """Return whether unknown-device signals are demoted to DEBUG."""
        return self._ignore_unknown

    @ignore_unknown.setter
    def ignore_unknown(self, value: bool) -> None:
        """Set whether unknown-device signals are demoted to DEBUG."""
        self._ignore_unknown = value

    # LED Control Methods
    async def led_on(self) -> None:
        """Turn the USB stick LED on."""
        _LOGGER.debug("Turning LED on")
        await self.send_command(CMD_LED_ON)

    async def led_off(self) -> None:
        """Turn the USB stick LED off."""
        _LOGGER.debug("Turning LED off")
        await self.send_command(CMD_LED_OFF)

    async def led_blink(self, count: int = 5) -> None:
        """Blink the USB stick LED a specific number of times.

        Args:
            count: Number of times to blink (1-9)

        """
        blink_commands = {
            1: CMD_LED_BLINK_1,
            2: CMD_LED_BLINK_2,
            3: CMD_LED_BLINK_3,
            4: CMD_LED_BLINK_4,
            5: CMD_LED_BLINK_5,
            6: CMD_LED_BLINK_6,
            7: CMD_LED_BLINK_7,
            8: CMD_LED_BLINK_8,
            9: CMD_LED_BLINK_9,
        }

        if count not in blink_commands:
            _LOGGER.error("Invalid blink count %d. Must be 1-9", count)
            return

        _LOGGER.debug("Blinking LED %d times", count)
        await self.send_command(blink_commands[count])

    # Device Calibration Methods
    async def set_upper_endpoint(self, device_enum: str) -> None:
        """Set the upper endpoint for a blind device.

        Args:
            device_enum: The device enumerator (hex string like "10")

        """
        # Format: ssXX9AAZZZ
        # XX = device enum, 9 = number of messages, AA = command, ZZZ = padding
        command = f"{CMD_TRANSMIT}{device_enum}9{CMD_SET_UPPER_ENDPOINT}0000"
        _LOGGER.debug("Setting upper endpoint for device %s: %s", device_enum, command)
        await self.send_command(command)

    async def set_lower_endpoint(self, device_enum: str) -> None:
        """Set the lower endpoint for a blind device.

        Args:
            device_enum: The device enumerator (hex string like "10")

        """
        # Format: ssXX9AAZZZ
        # XX = device enum, 9 = number of messages, AA = command, ZZZ = padding
        command = f"{CMD_TRANSMIT}{device_enum}9{CMD_SET_LOWER_ENDPOINT}0000"
        _LOGGER.debug("Setting lower endpoint for device %s: %s", device_enum, command)
        await self.send_command(command)

    async def allow_pairing_on_device(self, device_enum: str) -> None:
        """Make a device listen to a new remote's ID.

        Args:
            device_enum: The device enumerator (hex string like "10")

        """
        # Format: ssXX9AAZZZ
        # XX = device enum, 9 = number of messages, AA = command, ZZZ = padding
        command = f"{CMD_TRANSMIT}{device_enum}9{CMD_ALLOW_PAIRING}0000"
        _LOGGER.debug("Allowing pairing on device %s: %s", device_enum, command)
        await self.send_command(command)

    async def manual_up(self, device_enum: str) -> None:
        """Manually move blind up (simulates holding button).

        Args:
            device_enum: The device enumerator (hex string like "10")

        """
        # Format: ssXX9AAZZZ
        # XX = device enum, 9 = number of messages, AA = command, ZZZ = padding
        command = f"{CMD_TRANSMIT}{device_enum}9{CMD_MANUAL_UP}0000"
        _LOGGER.debug("Manual up for device %s: %s", device_enum, command)
        await self.send_command(command)

    async def manual_down(self, device_enum: str) -> None:
        """Manually move blind down (simulates holding button).

        Args:
            device_enum: The device enumerator (hex string like "10")

        """
        # Format: ssXX9AAZZZ
        # XX = device enum, 9 = number of messages, AA = command, ZZZ = padding
        command = f"{CMD_TRANSMIT}{device_enum}9{CMD_MANUAL_DOWN}0000"
        _LOGGER.debug("Manual down for device %s: %s", device_enum, command)
        await self.send_command(command)

    # USB Stick System Commands
    async def get_device_id(self) -> str | None:
        """Get the USB stick's unique device ID.

        Returns the device ID string or None if request fails.
        """
        if self._device_id_future and not self._device_id_future.done():
            _LOGGER.warning("Device ID request already in progress")
            return None

        _LOGGER.debug("Requesting device ID")
        self._device_id_future = asyncio.get_running_loop().create_future()

        try:
            # Send the request command
            await self.send_command(CMD_GET_DEVICE_ID)

            # Wait for device ID response with timeout
            device_id = await asyncio.wait_for(
                self._device_id_future, timeout=DEVICE_ID_TIMEOUT
            )
        except TimeoutError:
            _LOGGER.error("Device ID request timeout - device did not respond")
            return None
        except ConnectionError:
            _LOGGER.warning("Device ID request aborted - serial port disconnected")
            return None
        else:
            _LOGGER.info("Device ID retrieved successfully: %s", device_id)
            return device_id
        finally:
            self._device_id_future = None

    async def echo_on(self) -> None:
        """Enable local echo on the USB stick."""
        _LOGGER.debug("Enabling local echo")
        await self.send_command(CMD_ECHO_ON)

    async def echo_off(self) -> None:
        """Disable local echo on the USB stick."""
        _LOGGER.debug("Disabling local echo")
        await self.send_command(CMD_ECHO_OFF)

    async def enter_bootloader_mode(self) -> None:
        """Enter bootloader mode (B:0)."""
        _LOGGER.debug("Entering bootloader mode")
        await self.send_command(CMD_ENTER_BOOTLOADER)

    async def enter_initial_mode(self) -> None:
        """Enter initial mode (B:1)."""
        _LOGGER.debug("Entering initial mode")
        await self.send_command(CMD_ENTER_INITIAL)

    async def reboot_stick(self) -> None:
        """Reboot the USB stick (only available in bootloader mode)."""
        _LOGGER.debug("Rebooting USB stick")
        await self.send_command(CMD_REBOOT)

    async def disconnect(self) -> None:
        """Disconnect from the serial port."""
        # Latch _closed FIRST so any reconnect callback that fires in the same
        # tick is gated out by connect()'s early-return (CR-01).
        self._closed = True
        # Route teardown through the single chokepoint: cancels the retry worker +
        # heartbeat AND drains the queue, idempotently (review finding 1).
        # A transport that never fires connection_lost (e.g. a MagicMock in tests)
        # is handled correctly because the drain does not depend on connection_lost.
        self.update_connection_status(False)

        # _stop_pairing_task is not handled by the chokepoint — cancel it here.
        if self._stop_pairing_task and not self._stop_pairing_task.done():
            self._stop_pairing_task.cancel()
            self._stop_pairing_task = None

        if self._transport:
            self._transport.close()
            self._transport = None
            _LOGGER.info("Disconnected from Schellenberg USB stick")


class SchellenbergProtocol(asyncio.Protocol):
    """Serial protocol for reading newline-terminated messages."""

    def __init__(
        self, message_callback: Callable[[str], None], api: SchellenbergUsbApi
    ) -> None:
        """Initialize the protocol."""
        self.message_callback = message_callback
        self.api = api
        self.buffer = ""
        self.transport: asyncio.Transport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Called when a connection is made."""
        self.transport = transport  # type: ignore[assignment]

    def data_received(self, data: bytes) -> None:
        """Called with new data from the serial port."""
        _LOGGER.debug("Received from serial device: %s", data)
        self.buffer += data.decode("ascii", errors="ignore")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                _LOGGER.debug("Parsed message from serial device: %s", line.strip())
                self.message_callback(line.strip())

    def connection_lost(self, exc: Exception | None) -> None:
        """Called when the connection is lost."""
        _LOGGER.warning("Serial port connection lost: %s", exc)
        self.api.update_connection_status(False)
        # Clear the stale transport reference before scheduling so the
        # connect() re-entrancy guard cannot wedge on a transport that
        # reports is_closing()==False after an abrupt loss (WR-02).
        self.api._transport = None
        # Route through the shared single-flight chokepoint (CR-01/WR-04):
        # increments _reconnect_attempts once and stores the TimerHandle.
        # Uses hass.loop (not asyncio.get_running_loop()) because this
        # transport callback may be invoked synchronously without a running loop.
        self.api._schedule_reconnect()
