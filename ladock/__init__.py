"""LADOCK — molecular docking workstation.

One distribution, two front-ends that share the same bundled engine binaries:

* ``ladock-cli``     — rule-based (non-LLM) docking agent, :mod:`ladock.cli`
* ``ladock-desktop`` — PySide6 GUI workstation, :mod:`ladock.desktop`

Importing this package is deliberately cheap: nothing here pulls in PySide6,
RDKit or Meeko, so ``ladock-cli`` starts without paying for the GUI stack.
"""

__version__ = "2.0.0"

__all__ = ["__version__"]
