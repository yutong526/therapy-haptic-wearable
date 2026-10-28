"""Laptop side of the armband: sensor sources, recording, features and arousal scoring.

Data flow (every source only knows the shared Clock and a sink function):
    EdaSource (BITalino)  ----+
                              +--> sink(stream, t_ms, values) --> Recorder (one CSV per stream)
    Esp32Source (PPG + IMU) --+                                 --> FeatureExtractor
    markers (keyboard)  ----------------------------------------> Recorder (markers.csv)
    every 1 s: FeatureExtractor.compute -> Scorer.update -> Recorder (features.csv) + Publisher
"""
