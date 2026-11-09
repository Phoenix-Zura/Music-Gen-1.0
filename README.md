# Soundcraft

Soundcraft is a Streamlit music sketching app. Its offline, rule-based composer creates a short arrangement and exports synchronized WAV audio and editable MIDI.

## Requirements

- Python 3.10 or newer
- Packages listed in `requirements.txt`

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

If PowerShell blocks virtual-environment activation, install with `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and run with `.\.venv\Scripts\python.exe -m streamlit run app.py`.

## How it works

Choose a style, mood, key (or Auto), tempo, length, chord progression, reverb, instruments, variation seed, and variation strength, then enter a short description. The same seed and settings reproduce the same version; **Make a different version** advances the seed while keeping the generated settings. Variation strength changes the available chord voicings, chord rhythms, melody contours, bass patterns, and drum grooves. The offline prompt interpreter recognizes a limited vocabulary of genre, mood, key, tempo, length, instruments (including simple negation), chord progression, and reverb cues. Recognized prompt settings override the matching sidebar settings; unspecified settings use the sidebar. The app shows recognized cues with each result. This interpreter is rule-based, not a trained language model, so unfamiliar or ambiguous phrases may not be understood.

The app renders 22.05 kHz stereo WAV audio directly in Python and writes a type-1 MIDI file with separate instrument tracks. WAV is rendered audio; MIDI is editable note and instrument data. The current version supports 2-16 bars in 4/4, selectable common chord progressions, adjustable reverb, and piano, electric piano, acoustic guitar, warm synth, bass, strings, violin, flute, bell, marimba, and synthesized drum-kit presets.

The default built-in renderer uses simple synthesized tones. For sampled instrument timbres, obtain a SoundFont (`.sf2` or `.sf3`) that you have permission to use. Select **FluidSynth + SoundFont** in the sidebar and enter the SoundFont path. Install the FluidSynth command-line program and add it to `PATH`, or enter its executable path in the app. On Windows, FluidSynth installed under `%LOCALAPPDATA%\Soundcraft\FluidSynth` is auto-detected. Alternatively, set `SOUNDFONT_PATH` and `FLUIDSYNTH_PATH` in the environment. Prompted instrument names replace the default instrument selection; unsupported instrument names continue to use the sidebar selection.

## Tests

```powershell
python -m unittest discover -s tests -v
```
