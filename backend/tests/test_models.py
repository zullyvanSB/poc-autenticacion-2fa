"""Pruebas unitarias del contrato de dominio `UserType` (POCA-001).

Cubren el enum `common.models.UserType` y su etiqueta presentable:

- `CORPORATIVO` tiene el valor literal exacto ``"corporativo"`` (FR-1).
- Los valores existentes ``CLIENTE``/``INTERNO`` no se alteran (FR-1).
- Los tres miembros son distintos entre si, de modo que la derivacion por
  proveedor pueda diferenciar SAML (``INTERNO``) de Google (``CORPORATIVO``) (FR-2).
- Cada miembro expone una etiqueta presentable no vacia consumible por el
  dashboard (FR-3).

`models.py` es logica pura: no hay fronteras externas que simular.
"""

from __future__ import annotations

from common.models import UserType


def test_user_type_corporativo_value() -> None:
    """Given el enum UserType, When se lee CORPORATIVO, Then su valor es "corporativo"."""
    # Given/When/Then: valor literal exacto en minusculas (FR-1).
    assert UserType.CORPORATIVO.value == "corporativo"


def test_user_type_existing_values_unchanged() -> None:
    """Given el enum ampliado, When se leen CLIENTE/INTERNO, Then conservan su valor."""
    # Given/When/Then: ampliar el enum no altera los miembros previos (FR-1).
    assert UserType.CLIENTE.value == "cliente"
    assert UserType.INTERNO.value == "interno"


def test_user_type_values_are_distinct() -> None:
    """Given los tres miembros, When se comparan sus valores, Then son todos distintos."""
    # Given: los valores de los tres miembros.
    values = [member.value for member in UserType]
    # Then: CORPORATIVO es distinguible de INTERNO (SAML) y de CLIENTE (FR-2).
    assert len(values) == len(set(values))
    assert UserType.CORPORATIVO is not UserType.INTERNO
    assert UserType.CORPORATIVO is not UserType.CLIENTE


def test_user_type_label_present_for_all_members() -> None:
    """Given cada miembro, When se lee su etiqueta, Then es un string no vacio (FR-3)."""
    # Given/When/Then: todos los miembros tienen etiqueta presentable, en particular CORPORATIVO.
    for member in UserType:
        assert isinstance(member.label, str)
        assert member.label.strip() != ""


def test_user_type_corporativo_has_specific_label() -> None:
    """Given CORPORATIVO, When se lee su etiqueta, Then existe y es distinta de las otras."""
    # Given: las etiquetas de los tres miembros.
    labels = {member: member.label for member in UserType}
    # Then: la etiqueta de CORPORATIVO no colisiona con las demas (coherencia FR-3).
    assert labels[UserType.CORPORATIVO] != labels[UserType.CLIENTE]
    assert labels[UserType.CORPORATIVO] != labels[UserType.INTERNO]
