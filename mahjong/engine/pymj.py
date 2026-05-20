"""The single PyMahjongGB integration seam.

Spec: docs/specs/engine-api.md § PyMahjongGB integration boundary.

Any future engine logic needing fan, shanten, or winning-tile calculation goes
through this module. Direct imports of MahjongGB from elsewhere in
mahjong.engine.* are a lint failure (see tests/lint/).
"""
