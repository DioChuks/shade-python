"""
Base class for typed Shade API resource objects.

The Shade backend speaks JSON with ``camelCase`` keys (mirroring its Prisma
schema).  Python models expose the same data as ``snake_case`` attributes.
:class:`ShadeObject` handles that translation: each subclass declares its
``camelCase`` -> ``snake_case`` mapping in :attr:`ShadeObject._ALIASES` and is
constructed from an API payload via :meth:`ShadeObject.from_dict`.
"""
from __future__ import annotations

import inspect
from typing import Any, ClassVar, Dict, Mapping

from .errors import InvalidRequestError


class ShadeObject:
    """Base for API resource models with camelCase <-> snake_case mapping.

    Subclasses populate :attr:`_ALIASES` with the JSON keys whose names differ
    from their Python attribute (i.e. the multi-word, ``camelCase`` ones).
    Single-word keys that already match their attribute need no entry.
    """

    # JSON (camelCase) key -> attribute (snake_case) name.
    _ALIASES: ClassVar[Dict[str, str]] = {}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ShadeObject":
        """Build an instance from a raw API response body.

        camelCase keys are translated to their snake_case attribute names via
        :attr:`_ALIASES`; keys the constructor does not accept are ignored so
        that additive backend changes do not break deserialization.
        """
        if not isinstance(data, Mapping):
            raise InvalidRequestError(
                f"{cls.__name__}.from_dict expected a mapping, "
                f"got {type(data).__name__}"
            )

        translated: Dict[str, Any] = {}
        for key, value in data.items():
            translated[cls._ALIASES.get(key, key)] = value

        accepted = cls._constructor_params()
        kwargs = {k: v for k, v in translated.items() if k in accepted}
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize back to a camelCase payload using the reverse of ``_ALIASES``."""
        reverse = {attr: key for key, attr in self._ALIASES.items()}
        result: Dict[str, Any] = {}
        for attr in self._constructor_params():
            result[reverse.get(attr, attr)] = getattr(self, attr)
        return result

    @classmethod
    def _constructor_params(cls) -> frozenset[str]:
        params = inspect.signature(cls).parameters
        return frozenset(
            name
            for name, param in params.items()
            if param.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        )

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        return self.to_dict() == other.to_dict()  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{attr}={getattr(self, attr)!r}" for attr in self._constructor_params()
        )
        return f"{type(self).__name__}({fields})"
