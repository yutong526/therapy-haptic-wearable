"""Test protocol from config/protocol.yaml: conditions, self-report range and the step orders."""

from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "protocol.yaml"  # data collection
USE_PATH = DEFAULT_PATH.with_name("therapy.yaml")  # closed loop: demo and actual use


def load_protocol(path=DEFAULT_PATH):
    """Returns {"settling_s", "auto_steps", "conditions": {code: {"name", "color", "pump"}}, "self_report": {...} or
    None (no self_report section: not a data collection; no self-report buttons or ML hints),
    "orders": {name: [{"code", "seconds", "note", "airbag"}]}}. pump: the airbag runs during this
    condition (run_session.py --pump protocol); auto_steps: run_session.py enters the next step's code
    itself when the time is up (and "end" after the last one); airbag (per step, control test): if any
    step of the order has it, the closed loop (--pump auto) may start the pump only in those steps and
    only logs what it would do in the others; control (control test): {"tasks": [...], "airbag_task":
    {order: task}} or None: note:<task> starts a block, and the closed loop may start the pump only in
    the block of the order's airbag task. Raises ValueError on an inconsistent file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    conditions = {str(code): {"name": str(c["name"]), "color": c.get("color", "#FFFFFF"),
                              "pump": bool(c.get("pump", False))}
                  for code, c in data["conditions"].items()}
    orders = {}
    for order, steps in data["orders"].items():
        orders[order] = []
        for step in steps:
            code = str(step["code"])
            if code not in conditions:
                raise ValueError(f"{path}: order {order} uses code {code!r}, not in conditions")
            orders[order].append({"code": code, "seconds": round(float(step["minutes"]) * 60),
                                  "note": step.get("note", ""), "airbag": bool(step.get("airbag", False))})
    if not orders:
        raise ValueError(f"{path}: no orders")
    sr = data.get("self_report")
    control = data.get("control")
    if control:
        control = {"tasks": [str(t) for t in control["tasks"]], "airbag_task": dict(control["airbag_task"])}
        missing = [o for o in orders if control["airbag_task"].get(o) not in control["tasks"]]
        if missing:
            raise ValueError(f"{path}: control.airbag_task needs a task for orders {missing}")
    return {"settling_s": round(float(data.get("settling_minutes", 3)) * 60),
            "auto_steps": bool(data.get("auto_steps", False)), "conditions": conditions,
            "self_report": sr and {"min": int(sr.get("min", 1)), "max": int(sr.get("max", 5)),
                                   "hint": sr.get("hint", "")},
            "orders": orders, "control": control}


def default_order(protocol, subject):
    """Counterbalancing: subject number 1, 2, 3, ... -> orders 1, 2, 3, ... in turn (two orders: odd
    -> first, even -> second, as before); no number -> first."""
    names = list(protocol["orders"])
    digits = "".join(ch for ch in subject if ch.isdigit())
    return names[(int(digits) - 1) % len(names)] if digits else names[0]
