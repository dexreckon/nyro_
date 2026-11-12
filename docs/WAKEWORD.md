# Training the "Hey Nyro" wake word (openwakeword, free, no account)

Nyro always works with the hotkey (`Ctrl+Alt+N`). The spoken "Hey Nyro" turns on once this file exists:

    models/hey_nyro.onnx

## Steps
1. In the [openWakeWord](https://github.com/dscripka/openWakeWord) repository, open the
   **automatic model training** notebook (under `notebooks/`) in Google Colab (a free Google account is enough).
2. Set the target phrase to `hey nyro`. Because "nyro" is not an English word, also try the spelling variants
   `hey nairo` / `hey nero` in the notebook's pronunciation list so the synthetic voices say it the way you do.
3. Run all cells (about an hour). It generates synthetic speech, so you do not have to record anything.
4. Download the resulting `.onnx` file, rename it to `hey_nyro.onnx`, and put it in this repo's `models/` folder.
5. Restart Nyro. The log line `[wake] model not found ...` disappears.

## Tuning
- Too many false triggers: raise `wake_word.threshold` (default `0.5`) in `nyro_config.local.json`, e.g. `0.65`.
- It misses you: lower it, e.g. `0.4`, and speak the phrase slightly slower.
- Cooldown is 2 s so one utterance triggers once.

The model file is git-ignored by default (`models/*.onnx`).
