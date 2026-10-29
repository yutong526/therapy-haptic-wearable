"""Laptop side of the armband: sensor sources, recording, features and arousal scoring.

Data flow (every source only knows the shared Clock and a sink function):
    EdaSource (BITalino: EDA, ACC) ---+
    PpgBleSource (RT7015 over BLE) ---+--> sink(stream, t_ms, values) --> Recorder (one CSV per stream)
    Esp32Source (output link) <-- C,state,arousal lines             --> Processor.add
    markers (keyboard) ----------------------------------------------> Recorder (markers.csv)
    every 1 s: Processor.step -> features.csv + publishers (ESP32 serial, UDP)
"""
