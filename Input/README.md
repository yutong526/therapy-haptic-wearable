# BITalino EDA recorder

Python command-line recorder using the official BITalino (r)evolution API.

## Windows setup

1. Install Python. The commands below use your installed version.
2. Power on and pair your BITalino through Windows Bluetooth settings. Find its outgoing serial COM port in Bluetooth settings or Device Manager. This recorder uses the Bluetooth serial port; compatibility depends on your board exposing that connection.
3. Your EDA sensor is connected to A1, so use `--channel 1`.
4. Close OpenSignals or any other program using the board.
5. In PowerShell, change to this folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps bitalino==1.2.6
.\.venv\Scripts\python.exe record_eda.py --port COM5 --channel 1 --rate 100 --output eda.csv
```

Replace COM5 with your outgoing Bluetooth COM port. Press Ctrl+C to finish. To record 60 seconds of samples, add `--duration 60`. Existing CSV files are never overwritten. Parent directories are created automatically.

The separate `--no-deps` installation skips the upstream PyBluez dependency, which otherwise needs a C++ compiler on this system. The dependencies required for COM-port recording are installed by requirements.txt. Bluetooth still works through Windows' paired serial port. Direct MAC-address connections and Bluetooth discovery need PyBluez and are outside this installation path.

## CSV contents

Each row contains a sample index, nominal elapsed seconds (`index / rate`), board sequence number, sequence gap modulo 16, raw EDA ADC value, physical input, sampling rate, and batch receipt time in UTC. The UTC value describes receipt of a batch on the computer, not the individual sensor measurement time. Nominal elapsed time does not compensate for lost samples or clock drift.

Sequence gaps flag discontinuities detectable with the board's 4-bit sequence counter; losses of exact multiples of 16 cannot be detected this way. Raw ADC values are not microsiemens. Conversion must use the transfer function for your EDA sensor and input resolution (A1–A4: 10 bits; A5–A6: 6 bits). No filtering or calibration is applied.

The program flushes each batch to the operating system and attempts to stop and close the board on exit. Previously written batches remain available after an acquisition error; an interrupted partial batch may be lost. Flushing is not a guarantee against disk or power failure. A duration specifies sample count, so connection/setup time is additional.

## Check without a board

Simulation generates synthetic values as quickly as possible and needs no external packages:

```powershell
py record_eda.py --simulate --channel 3 --rate 100 --duration 2 --output simulated.csv
```

This should produce 200 data rows plus a header. Real acquisition still needs verification with your board.

## References

- [Official API and installation guidance](https://github.com/BITalinoWorld/revolution-python-api)
- [API implementation and data layout](https://github.com/BITalinoWorld/revolution-python-api/blob/master/bitalino.py)
