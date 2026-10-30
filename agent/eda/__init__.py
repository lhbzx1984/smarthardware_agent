"""EDA 链路：网表结构化 → KiCad 原理图 / PCB（纯离线，零第三方依赖）。

事实源只有两个：
  netlist.py —— 电路（器件 + 网络）
  board.py   —— 板级约束（板框 + 安装孔 + 禁布区 + 限高 + 接口靠边）
所有文件（SVG 预览、.kicad_sch、.kicad_pcb）都是这两个源的投影，不许各写一份。
"""
from __future__ import annotations

from . import board, easyeda, kicad, netlist, parts, pcb, symbols  # noqa: F401

__all__ = ["board", "easyeda", "kicad", "netlist", "parts", "pcb", "symbols"]
