"""Test protocol from config/protocol.yaml: conditions, self-report range and the step orders."""

from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "protocol.yaml"  # data collection
USE_PATH = DEFAULT_PATH.with_name("therapy.yaml")  # closed loop: demo and actual use


def load_protocol(path=DEFAULT_PATH):
    """Returns {"settling_s", "auto_steps", "conditions": {code: {"name", "color", "pump"}}, "self_report": {...} or
    None (no self_report section: not a data collection; no self-report buttons or ML hints),
    "orders": {name: [{"code", "seconds", "note"}]}}. pump: the airbag runs during this condition
    (run_session.py --pump protocol); auto_steps: run_session.py enters the next step's code itself
    when the time is up (and "end" after the last one). Raises ValueError on an inconsistent file."""
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
                                  "note": step.get("note", "")})
    if not orders:
        raise ValueError(f"{path}: no orders")
    sr = data.get("self_report")
    return {"settling_s": round(float(data.get("settling_minutes", 3)) * 60),
            "auto_steps": bool(data.get("auto_steps", False)), "conditions": conditions,
            "self_report": sr and {"min": int(sr.get("min", 1)), "max": int(sr.get("max", 5)),
                                   "hint": sr.get("hint", "")},
            "orders": orders}


def default_order(protocol, subject):
    """Counterbalancing: odd subject number -> first order, even -> second (first if only one)."""
    names = list(protocol["orders"])
    digits = "".join(ch for ch in subject if ch.isdigit())
    if len(names) > 1 and digits and int(digits) % 2 == 0:
        return names[1]
    return names[0]
