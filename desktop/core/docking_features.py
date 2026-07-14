"""Which scoring functions support which optional docking features.

Confirmed with the LADOCK author:

* **Flexible-residue docking** — supported by every engine
  (Vina, Vinardo, AutoDock4, AutoDock-GPU).
* **Multiple-Ligand Simultaneous Docking (MLSD)** — only the AutoDock Vina 1.2
  engine (Vina / Vinardo). AutoDock4 and AutoDock-GPU dock a single ligand per
  run and therefore cannot arrange several ligands in the pocket at once.

The UI uses these sets to enable/disable the Flexible-mode and Simultaneous-
Ligands controls according to the currently selected scoring functions.
"""
from __future__ import annotations

from typing import Iterable

SF_SUPPORTS_FLEX: frozenset[str] = frozenset({"vina", "vinardo", "ad4", "ad4gpu"})
SF_SUPPORTS_MLSD: frozenset[str] = frozenset({"vina", "vinardo"})


def any_supports_flex(sf_keys: Iterable[str]) -> bool:
    """True if at least one of the given scoring functions supports flex."""
    return bool(set(sf_keys) & SF_SUPPORTS_FLEX)


def any_supports_mlsd(sf_keys: Iterable[str]) -> bool:
    """True if at least one of the given scoring functions supports MLSD."""
    return bool(set(sf_keys) & SF_SUPPORTS_MLSD)
