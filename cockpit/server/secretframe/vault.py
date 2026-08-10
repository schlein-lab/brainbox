

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from frame import Frame
from inject import inject_once

class SecretVault(abc.ABC):

    @abc.abstractmethod
    def list_names(self) -> List[str]:
        pass

    @abc.abstractmethod
    def has(self, name: str) -> bool:
        ...

    @abc.abstractmethod
    def fetch(self, name: str) -> bytes:
        pass

    @property
    def durable(self) -> bool:

        return True

class ClientVault(SecretVault):

    def __init__(self, secrets: Optional[Dict[str, bytes]] = None):
        self._secrets: Dict[str, bytes] = dict(secrets or {})

    def list_names(self) -> List[str]:
        return sorted(self._secrets.keys())

    def has(self, name: str) -> bool:
        return name in self._secrets

    def fetch(self, name: str) -> bytes:
        if name not in self._secrets:
            raise KeyError(name)
        return self._secrets[name]

    def store(self, name: str, value: bytes) -> None:

        self._secrets[name] = value

    def __repr__(self) -> str:
        return f"<ClientVault names={self.list_names()} (values redacted)>"

    __str__ = __repr__

class BoxEphemeralVault(ClientVault):

    @property
    def durable(self) -> bool:
        return False

class CredentialError(Exception):
    pass

@dataclass
class ForgeTarget:

    ref: str
    origin: str
    spoken: str
    fields: List[str]

class CredentialEnterFlow:

    def __init__(self, vault: SecretVault,
                 resolve_target: Callable[[str], ForgeTarget],
                 speak: Callable[[str], None],
                 confirm: Callable[[str], bool],
                 forge_inject: Callable[["memoryview", ForgeTarget], object],
                 audit_sink: Callable[[Frame], None]):
        self.vault = vault
        self.resolve_target = resolve_target
        self.speak = speak
        self.confirm = confirm
        self.forge_inject = forge_inject
        self.audit_sink = audit_sink

    def enter(self, target_ref: str, credential_name: str, *,
              require_mlock: bool = True) -> dict:

        if not self.vault.has(credential_name):
            raise CredentialError(
                f"unknown credential name {credential_name!r} "
                f"(owner speaks a name; the client vault holds the value, §8.2)")

        target = self.resolve_target(target_ref)
        self.speak(target.spoken)
        if not self.confirm(target.spoken):
            raise CredentialError("owner did not confirm the login target; "
                                  "injection aborted (§8.2 verify+speak)")

        secret_frame = Frame.secret_inject(target=target.ref,
                                            credential_name=credential_name)
        assert secret_frame.is_secret()

        def _provider() -> bytes:
            return self.vault.fetch(credential_name)

        def _consumer(view: "memoryview") -> object:
            return self.forge_inject(view, target)

        forge_result = inject_once(_provider, _consumer,
                                   require_mlock=require_mlock)

        from frame import FrameClass
        audit = Frame(cls=FrameClass.LEDGER, kind="verb.credential_enter",
                      payload={"target_origin": target.origin,
                               "fields": target.fields,
                               "credential_name": credential_name,
                               "injected": True})
        self.audit_sink(audit)

        return {"injected": True, "origin": target.origin,
                "forge_result": forge_result}

