from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import httpx

from al1s_terminal.app.editor_coordinator import EditorCoordinator
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.device_authority import DeviceInputAuthority, PathRuntimeOwner
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.adb import AdbProvider


class DevicePathCoordinator:
    def __init__(
        self,
        request: Callable[..., httpx.Response],
        secrets: FileSecretStore,
        adb: AdbProvider,
        uows: Callable[[], LocalUnitOfWork],
        owner: PathRuntimeOwner,
        editor: EditorCoordinator,
        native_ready: Callable[[], bool],
    ) -> None:
        self._request, self._secrets, self._adb, self._uows = request, secrets, adb, uows
        self._owner, self._editor, self._native_ready = owner, editor, native_ready
        self.input = DeviceInputAuthority()
        self._released: dict[UUID, tuple[int, UUID | None]] = {}
        self._cursor: str | None = None

    def cycle(self) -> None:
        identity = self._secrets.load()
        if identity is None:
            return
        probe = self._adb.probe()
        online = {device.serial for device in probe.online_devices}
        with self._uows() as uow:
            known = uow.target_devices.list_bound(after_serial=self._cursor, limit=100)
            active = uow.work_items.get_active()
        if not known:
            self._cursor = None
            return
        self._cursor = known[-1].adb_serial if len(known) == 100 else None
        by_device = {item.target_device_id: item for item in known if item.target_device_id}
        payload = []
        for device, observation in by_device.items():
            item: dict[str, object] = {
                "device_id": str(device),
                "instance_id": str(self._owner.instance),
                "control_ready": observation.adb_serial in online and self._native_ready(),
            }
            if device in self._released:
                generation, previous = self._released[device]
                item["release_generation"] = generation
                if previous:
                    item["released_previous_instance_id"] = str(previous)
            payload.append(item)
        if not payload:
            return
        response = self._request(
            "POST",
            "/api/v1/terminal/device-paths/observations",
            credential=identity.credential,
            json={"items": payload},
        ).json()
        for result in response["items"]:
            device = UUID(result["device_id"])
            serial = by_device[device].adb_serial
            granted = result["input_granted"] is True
            self.input.update(serial, granted)
            holder_is_us = result["holder_terminal_id"] == str(identity.terminal_id)
            if holder_is_us and result.get("holder_instance_id") == str(self._owner.instance):
                self._owner.confirm()
            replaced = (
                holder_is_us
                and self._owner.previous is not None
                and result.get("holder_instance_id") == str(self._owner.previous)
            )
            if (result["release_requested"] or replaced) and (
                active is None or active.target_device_id != device
            ):
                self._editor.release_device(device)
                self._released[device] = (
                    int(result["generation"]),
                    self._owner.previous if replaced else None,
                )
            elif granted:
                self._released.pop(device, None)
