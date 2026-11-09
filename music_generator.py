from __future__ import annotations

import hashlib
import io
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np


KEYS = {
    "C major": (0, "major"),
    "G major": (7, "major"),
    "D major": (2, "major"),
    "A major": (9, "major"),
    "E major": (4, "major"),
    "F major": (5, "major"),
    "A minor": (9, "minor"),
    "E minor": (4, "minor"),
    "D minor": (2, "minor"),
    "G minor": (7, "minor"),
}

SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
}

INSTRUMENTS = {
    "Piano": {"program": 0, "role": "chords", "harmonics": (1.0, 0.48, 0.2, 0.1, 0.05), "decay": 2.5},
    "Electric piano": {"program": 4, "role": "chords", "harmonics": (1.0, 0.4, 0.14, 0.05), "decay": 1.8},
    "Acoustic guitar": {"program": 24, "role": "chords", "harmonics": (1.0, 0.3, 0.12, 0.04), "decay": 2.0},
    "Warm synth": {"program": 89, "role": "melody", "harmonics": (1.0, 0.35, 0.16, 0.08), "decay": 0.8},
    "Bass": {"program": 33, "role": "bass", "harmonics": (1.0, 0.25, 0.08), "decay": 1.4},
    "Strings": {"program": 48, "role": "pad", "harmonics": (1.0, 0.42, 0.16, 0.07), "decay": 0.35},
    "Violin": {"program": 40, "role": "melody", "harmonics": (1.0, 0.42, 0.18, 0.09), "decay": 0.32},
    "Flute": {"program": 73, "role": "melody", "harmonics": (1.0, 0.12, 0.03), "decay": 0.45},
    "Bell": {"program": 10, "role": "bell", "harmonics": (1.0, 0.55, 0.28, 0.15, 0.08), "decay": 3.8},
    "Marimba": {"program": 12, "role": "bell", "harmonics": (1.0, 0.45, 0.18, 0.06), "decay": 1.6},
    "Drum kit": {"program": 0, "role": "drums", "harmonics": (), "decay": 0.2},
}

PROGRESSIONS = {
    "Pop": (0, 4, 5, 3),
    "Lo-fi": (0, 5, 3, 4),
    "Ambient": (5, 3, 0, 4),
    "Jazz": (1, 4, 0, 5),
    "Cinematic": (0, 5, 3, 4),
}

CHORD_PROGRESSIONS = {
    "Style default": None,
    "I–V–vi–IV (Pop)": (0, 4, 5, 3),
    "I–vi–IV–V (Classic)": (0, 5, 3, 4),
    "vi–IV–I–V (Emotional)": (5, 3, 0, 4),
    "ii–V–I–vi (Jazz)": (1, 4, 0, 5),
    "i–VI–III–VII (Minor)": (0, 5, 2, 6),
}

DRUM_NOTES = {"Kick": 36, "Snare": 38, "Closed hi-hat": 42}

MOOD_VELOCITY = {
    "Calm": 0.72,
    "Dreamy": 0.68,
    "Energetic": 1.0,
    "Mysterious": 0.82,
    "Hopeful": 0.9,
}


@dataclass(frozen=True)
class NoteEvent:
    instrument: str
    pitch: int
    start_beats: float
    duration_beats: float
    velocity: int


@dataclass(frozen=True)
class GenerationResult:
    wav_data: bytes
    midi_data: bytes
    duration_seconds: float
    note_count: int
    resolved_key: str


class SoundFontRenderError(RuntimeError):
    """Raised when the optional FluidSynth renderer cannot produce audio."""


def locate_fluidsynth(fluidsynth_path: str | None = None) -> str | None:
    if fluidsynth_path:
        return fluidsynth_path
    configured_path = os.environ.get("FLUIDSYNTH_PATH")
    if configured_path:
        return configured_path
    executable = shutil.which("fluidsynth")
    if executable:
        return executable

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        installed_executables = Path(local_app_data).glob(
            "Soundcraft/FluidSynth/*/*/bin/fluidsynth.exe"
        )
        candidates = sorted(installed_executables)
        if candidates:
            return str(candidates[-1])
    return None


@dataclass(frozen=True)
class PromptInterpretation:
    genre: str | None = None
    mood: str | None = None
    key: str | None = None
    bpm: int | None = None
    bars: int | None = None
    progression_name: str | None = None
    reverb: int | None = None
    add_instruments: tuple[str, ...] = ()
    remove_instruments: tuple[str, ...] = ()
    recognized_cues: tuple[str, ...] = ()


_GENRE_ALIASES = {
    "Pop": ("pop", "radio pop"),
    "Lo-fi": ("lo-fi", "lofi", "chillhop", "chill hop", "study beats"),
    "Ambient": ("ambient", "soundscape", "atmospheric"),
    "Jazz": ("jazz", "swing"),
    "Cinematic": ("cinematic", "film score", "soundtrack", "orchestral score"),
}
_MOOD_ALIASES = {
    "Calm": ("calm", "relaxed", "peaceful", "gentle", "soothing", "laid-back"),
    "Dreamy": ("dreamy", "ethereal", "floaty", "hazy"),
    "Energetic": ("energetic", "upbeat", "driving", "high energy"),
    "Mysterious": ("mysterious", "dark", "suspenseful", "moody"),
    "Hopeful": ("hopeful", "happy", "joyful", "uplifting", "positive"),
}
_INSTRUMENT_ALIASES = {
    "Electric piano": ("electric piano", "rhodes", "epiano"),
    "Acoustic guitar": ("acoustic guitar", "acoustic guitars"),
    "Warm synth": ("warm synth", "synth", "synthesizer", "synthesizers"),
    "Bass": ("bass guitar", "bass"),
    "Drum kit": ("drum kit", "drums", "drum", "percussion", "beat"),
    "Strings": ("strings", "string section", "string pad"),
    "Violin": ("violin", "fiddle"),
    "Flute": ("flute",),
    "Piano": ("piano", "pianos", "keys", "keyboard"),
    "Bell": ("bell", "bells"),
    "Marimba": ("marimba",),
}
_PROGRESSION_ALIASES = {
    "I–V–vi–IV (Pop)": (r"\bi\s*[-–]\s*v\s*[-–]\s*vi\s*[-–]\s*iv\b", "pop progression"),
    "I–vi–IV–V (Classic)": (r"\bi\s*[-–]\s*vi\s*[-–]\s*iv\s*[-–]\s*v\b", "classic progression"),
    "vi–IV–I–V (Emotional)": (r"\bvi\s*[-–]\s*iv\s*[-–]\s*i\s*[-–]\s*v\b", "emotional progression"),
    "ii–V–I–vi (Jazz)": (r"\bii\s*[-–]\s*v\s*[-–]\s*i\s*[-–]\s*vi\b", "ii-v-i"),
    "i–VI–III–VII (Minor)": (r"\bi\s*[-–]\s*vi\s*[-–]\s*iii\s*[-–]\s*vii\b", "minor progression"),
}
def _first_matching_category(text: str, aliases: dict[str, tuple[str, ...]]) -> str | None:
    matches = [
        (match.start(), category)
        for category, phrases in aliases.items()
        for phrase in phrases
        if (match := re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text))
    ]
    return min(matches, default=(0, None))[1]


def _is_negated_instrument(text: str, start: int) -> bool:
    clause_start = max((text.rfind(mark, 0, start) for mark in ".!?;"), default=-1) + 1
    prefix = text[clause_start:start]
    negation = re.search(r"\b(?:no|without|avoid|exclude|skip)\b", prefix)
    if not negation:
        return False

    preceding_words = re.findall(r"[a-z]+", prefix[negation.end():])
    instrument_words = {
        word
        for aliases in _INSTRUMENT_ALIASES.values()
        for alias in aliases
        for word in re.findall(r"[a-z]+", alias)
    }
    connectors = {"and", "or", "any", "the", "using", "with"}
    return all(word in instrument_words or word in connectors for word in preceding_words)


def interpret_prompt(prompt: str) -> PromptInterpretation:
    """Extract supported music settings from common natural-language cues."""
    text = prompt.lower()
    cues: list[str] = []
    genre = _first_matching_category(text, _GENRE_ALIASES)
    mood = _first_matching_category(text, _MOOD_ALIASES)
    if genre:
        cues.append(f"style: {genre}")
    if mood:
        cues.append(f"mood: {mood}")

    key = None
    key_match = re.search(
        r"\b(?:key\s+(?:of\s+)?)?([a-g])\s*([#b]?)\s+(major|minor|maj|min)\b",
        text,
    )
    if key_match:
        root, accidental, mode = key_match.groups()
        mode_name = "minor" if mode.startswith("min") else "major"
        accidental_name = "sharp" if accidental == "#" else "flat" if accidental == "b" else ""
        key_name = f"{root.upper()}{accidental_name} {mode_name}"
        if key_name in KEYS:
            key = key_name
            cues.append(f"key: {key}")

    bpm = None
    bpm_match = re.search(r"\b(\d{2,3})\s*(?:bpm|beats?\s+per\s+minute)\b", text)
    if bpm_match:
        candidate_bpm = int(bpm_match.group(1))
        if 50 <= candidate_bpm <= 180:
            bpm = candidate_bpm
    else:
        tempo_aliases = {
            68: ("slow tempo", "slow and relaxed", "very slow"),
            92: ("moderate tempo", "mid-tempo", "medium tempo"),
            128: ("fast tempo", "up-tempo", "uptempo", "fast-paced"),
        }
        bpm = _first_matching_category(text, {str(value): phrases for value, phrases in tempo_aliases.items()})
        if bpm is not None:
            bpm = int(bpm)
    if bpm is not None:
        cues.append(f"tempo: {bpm} BPM")

    bars = None
    bars_match = re.search(r"\b(\d{1,2})\s*(?:bars?|measures?)\b", text)
    if bars_match:
        candidate_bars = int(bars_match.group(1))
        if 1 <= candidate_bars <= 16:
            bars = candidate_bars
            cues.append(f"length: {bars} bars")

    progression_name = None
    for name, patterns in _PROGRESSION_ALIASES.items():
        if any(re.search(pattern, text) for pattern in patterns):
            progression_name = name
            cues.append(f"chords: {name}")
            break

    reverb = None
    if re.search(r"\b(?:dry|no reverb|without reverb)\b", text):
        reverb = 0
        cues.append("no reverb")
    elif re.search(r"\b(?:lots of reverb|heavy reverb|very reverberant)\b", text):
        reverb = 50
        cues.append("strong reverb")
    elif re.search(r"\b(?:subtle reverb|light reverb)\b", text):
        reverb = 15
        cues.append("subtle reverb")

    instrument_matches: list[tuple[int, int, str, bool]] = []
    for instrument, aliases in _INSTRUMENT_ALIASES.items():
        for alias in aliases:
            for match in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", text):
                negated = _is_negated_instrument(text, match.start())
                instrument_matches.append((match.start(), match.end(), instrument, negated))
    selected_matches: list[tuple[int, int, str, bool]] = []
    for candidate in sorted(instrument_matches, key=lambda match: (match[0], -(match[1] - match[0]))):
        if any(candidate[0] < chosen[1] and candidate[1] > chosen[0] for chosen in selected_matches):
            continue
        selected_matches.append(candidate)

    add_instruments = tuple(dict.fromkeys(match[2] for match in selected_matches if not match[3]))
    remove_instruments = tuple(dict.fromkeys(match[2] for match in selected_matches if match[3]))
    if add_instruments:
        cues.append("instruments: " + ", ".join(add_instruments))
    if remove_instruments:
        cues.append("omit: " + ", ".join(remove_instruments))

    return PromptInterpretation(
        genre=genre,
        mood=mood,
        key=key,
        bpm=bpm,
        bars=bars,
        progression_name=progression_name,
        reverb=reverb,
        add_instruments=add_instruments,
        remove_instruments=remove_instruments,
        recognized_cues=tuple(cues),
    )


def resolve_instruments(
    selected_instruments: list[str], interpretation: PromptInterpretation
) -> list[str]:
    """Let explicit positive prompt instrument requests replace sidebar defaults."""
    if interpretation.add_instruments:
        instruments = list(interpretation.add_instruments)
    else:
        instruments = list(selected_instruments)
    return [instrument for instrument in instruments if instrument not in interpretation.remove_instruments]


def _scale_pitch(root: int, scale: tuple[int, ...], degree: int, octave: int = 0) -> int:
    scale_length = len(scale)
    octave_shift, scale_index = divmod(degree, scale_length)
    return 12 * (octave + octave_shift) + root + scale[scale_index]


def resolve_key(key: str | None, genre: str, mood: str, prompt: str, seed: int) -> str:
    """Choose a repeatable, mood-compatible key when the user selects Auto."""
    if key is not None and key != "Auto":
        if key not in KEYS:
            raise ValueError(f"Unsupported key: {key}")
        return key

    if mood == "Mysterious":
        candidates = [name for name, (_, mode) in KEYS.items() if mode == "minor"]
    elif mood in ("Energetic", "Hopeful"):
        candidates = [name for name, (_, mode) in KEYS.items() if mode == "major"]
    else:
        candidates = list(KEYS)
    if genre in ("Ambient", "Cinematic") and mood in ("Dreamy", "Calm"):
        candidates = [name for name, (_, mode) in KEYS.items() if mode == "minor"]

    selection = f"{seed}|{prompt.strip()}|{genre}|{mood}"
    digest = hashlib.sha256(selection.encode("utf-8")).digest()
    return candidates[int.from_bytes(digest[:4], "big") % len(candidates)]


def _make_events(
    prompt: str,
    genre: str,
    mood: str,
    key: str,
    bpm: int,
    bars: int,
    instruments: list[str],
    seed: int,
    progression_name: str,
    variation_strength: int,
) -> list[NoteEvent]:
    root, mode = KEYS[key]
    scale = SCALES[mode]
    seed_material = (
        f"{seed}|{prompt}|{genre}|{mood}|{key}|{bpm}|{bars}|"
        f"{','.join(instruments)}|{progression_name}|{variation_strength}"
    )
    stable_seed = int.from_bytes(hashlib.sha256(seed_material.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(stable_seed)
    velocity_scale = MOOD_VELOCITY[mood]
    progression = CHORD_PROGRESSIONS[progression_name] or PROGRESSIONS[genre]
    chord_patterns = ["block", "inversion"]
    if variation_strength >= 2:
        chord_patterns.append("broken")
    if variation_strength >= 3:
        chord_patterns.append("arpeggiated")
    if variation_strength >= 4:
        chord_patterns.append("syncopated")
    if variation_strength >= 5:
        chord_patterns.append("sparse")

    bass_patterns = ["pulse"]
    if variation_strength >= 2:
        bass_patterns.append("alternating")
    if variation_strength >= 3:
        bass_patterns.append("syncopated")
    if variation_strength >= 4:
        bass_patterns.append("driving")
    if variation_strength >= 5:
        bass_patterns.append("sparse")

    melody_patterns = ["chord_tones", "stepwise"]
    if variation_strength >= 2:
        melody_patterns.append("mixed")
    if variation_strength >= 3:
        melody_patterns.append("wide_leaps")
    if variation_strength >= 4:
        melody_patterns.append("arpeggio")
    if variation_strength >= 5:
        melody_patterns.append("sparse")

    drum_patterns = ["straight"]
    if variation_strength >= 2:
        drum_patterns.append("half_time")
    if variation_strength >= 3:
        drum_patterns.append("syncopated")
    if variation_strength >= 4:
        drum_patterns.append("driving")
    if variation_strength >= 5:
        drum_patterns.append("sparse")

    events: list[NoteEvent] = []

    def add(instrument: str, pitch: int, start: float, length: float, velocity: float) -> None:
        velocity += rng.randint(-2 * variation_strength, 2 * variation_strength)
        events.append(
            NoteEvent(
                instrument=instrument,
                pitch=max(0, min(127, pitch)),
                start_beats=start,
                duration_beats=max(0.08, length),
                velocity=max(1, min(127, round(velocity * velocity_scale))),
            )
        )

    for instrument in instruments:
        role = INSTRUMENTS[instrument]["role"]
        for bar in range(bars):
            bar_start = bar * 4
            chord_degree = progression[bar % len(progression)]
            chord_root = _scale_pitch(root, scale, chord_degree, 3)
            chord_third = _scale_pitch(root, scale, chord_degree + 2, 3)
            chord_fifth = _scale_pitch(root, scale, chord_degree + 4, 3)
            chord_pitches = [chord_root, chord_third, chord_fifth]

            if role == "drums":
                drum_pattern = rng.choice(drum_patterns)
                kick_positions = {
                    "straight": (0, 2),
                    "half_time": (0,),
                    "syncopated": (0, 1.5, 2.5),
                    "driving": (0, 1, 2, 3),
                    "sparse": (0,),
                }[drum_pattern]
                snare_positions = {
                    "straight": (1, 3),
                    "half_time": (2,),
                    "syncopated": (1.5, 3),
                    "driving": (1, 3),
                    "sparse": (3,),
                }[drum_pattern]
                for beat in kick_positions:
                    add(instrument, DRUM_NOTES["Kick"], bar_start + beat, 0.24, 94)
                for beat in snare_positions:
                    add(instrument, DRUM_NOTES["Snare"], bar_start + beat, 0.22, 76)
                for eighth in range(8):
                    base_hat_probability = 0.28 if drum_pattern == "sparse" else 0.65
                    if genre == "Ambient":
                        base_hat_probability *= 0.5
                    if rng.random() < base_hat_probability:
                        add(instrument, DRUM_NOTES["Closed hi-hat"], bar_start + eighth * 0.5, 0.12, 45)

            elif role in ("chords", "pad"):
                chord_pattern = rng.choice(chord_patterns) if role == "chords" else "pad"
                inversion = rng.randrange(3) if variation_strength >= 2 else 0
                voiced_chord = chord_pitches[inversion:] + chord_pitches[:inversion]
                voiced_chord = [
                    pitch + (12 if index < inversion else 0)
                    for index, pitch in enumerate(voiced_chord)
                ]
                octave_shift = rng.choice((-12, 0, 12)) if variation_strength >= 4 else 0
                voiced_chord = [pitch + octave_shift for pitch in voiced_chord]
                chord_length = (3.4 + rng.random() * 0.6) if role == "chords" else 4.0
                chord_velocity = 78 if role == "chords" else 58
                if role == "pad":
                    for pitch in voiced_chord:
                        add(instrument, pitch, bar_start, chord_length, chord_velocity)
                elif chord_pattern == "arpeggiated":
                    direction = voiced_chord if rng.random() < 0.5 else list(reversed(voiced_chord))
                    for index, beat in enumerate((0, 0.75, 1.5, 2.5)):
                        add(instrument, direction[index % len(direction)], bar_start + beat, 0.62, chord_velocity)
                elif chord_pattern == "broken":
                    for index, beat in enumerate((0, 1.5, 2.5)):
                        add(instrument, voiced_chord[index], bar_start + beat, 0.72, chord_velocity)
                elif chord_pattern == "syncopated":
                    for index, beat in enumerate((0, 1.75, 2.5, 3.5)):
                        add(instrument, voiced_chord[index % len(voiced_chord)], bar_start + beat, 0.38, chord_velocity)
                elif chord_pattern == "sparse":
                    for pitch in (voiced_chord[0], voiced_chord[2]):
                        add(instrument, pitch, bar_start, chord_length, chord_velocity)
                else:
                    for pitch in voiced_chord:
                        add(instrument, pitch, bar_start, chord_length, chord_velocity)
                    if genre in ("Pop", "Lo-fi") and rng.random() < 0.7:
                        for beat in (2, 3):
                            add(instrument, voiced_chord[0] + 12, bar_start + beat, 0.42, 43)

            elif role == "bass":
                bass_pattern = rng.choice(bass_patterns)
                bass_notes = {
                    "pulse": ((0, chord_root - 24), (2, chord_root - 24)),
                    "alternating": ((0, chord_root - 24), (2, chord_fifth - 24)),
                    "syncopated": ((0, chord_root - 24), (1.5, chord_fifth - 24), (3, chord_root - 12)),
                    "driving": tuple((beat, chord_root - 24) for beat in (0, 1, 2, 3)),
                    "sparse": ((0, chord_root - 24),),
                }[bass_pattern]
                for beat, pitch in bass_notes:
                    add(instrument, pitch, bar_start + beat, 0.72, 82 if beat else 90)

            elif role in ("melody", "bell"):
                melody_pattern = rng.choice(melody_patterns) if role == "melody" else "bell"
                if role == "bell" or melody_pattern == "arpeggio":
                    note_positions = (0.5, 1.5, 2.5, 3.5)
                elif melody_pattern == "sparse":
                    note_positions = (0, 3) if rng.random() < 0.5 else (1, 3)
                elif melody_pattern == "stepwise":
                    note_positions = (0, 1, 2, 3)
                elif genre == "Ambient":
                    note_positions = (0, 2) if rng.random() < 0.5 else (0, 1.5, 3)
                elif melody_pattern == "wide_leaps":
                    note_positions = (0, 0.75, 2, 2.75, 3.5)
                else:
                    note_positions = (0, 0.5, 1.5, 2, 3, 3.5)
                previous_degree = chord_degree
                for position in note_positions:
                    if role == "melody" and position % 1 == 0.5 and rng.random() < 0.34:
                        continue
                    chord_tones = (chord_degree, chord_degree + 2, chord_degree + 4)
                    if melody_pattern == "stepwise":
                        degree = (previous_degree + rng.choice((-1, 1))) % len(scale)
                    elif melody_pattern == "wide_leaps":
                        degree = rng.choice((chord_degree, chord_degree + 2, chord_degree + 4, chord_degree + 6))
                    elif melody_pattern == "arpeggio":
                        degree = chord_tones[int(position) % len(chord_tones)]
                    elif melody_pattern == "sparse":
                        degree = rng.choice(chord_tones)
                    else:
                        degree = rng.choice(chord_tones if rng.random() < 0.72 else tuple(range(7)))
                    previous_degree = degree
                    octave = (5 if role == "bell" else 4) + (
                        rng.choice((-1, 0, 1)) if role == "melody" and variation_strength >= 4 else 0
                    )
                    pitch = _scale_pitch(root, scale, degree, octave)
                    length = 0.38 if position % 1 == 0.5 else (0.82 if role == "melody" else 0.65)
                    if role == "melody" and variation_strength >= 3 and rng.random() < 0.2:
                        length *= 0.65
                    add(instrument, pitch, bar_start + position, length, 72 if role == "melody" else 62)

    return events


def _render_wav(
    events: list[NoteEvent], bpm: int, bars: int, sample_rate: int, reverb: int
) -> tuple[bytes, float]:
    duration_seconds = bars * 4 * 60 / bpm
    frame_count = math.ceil(duration_seconds * sample_rate)
    audio = np.zeros((frame_count, 2), dtype=np.float32)
    active_instruments = list(dict.fromkeys(event.instrument for event in events))
    track_pan = {
        name: 0.5 if len(active_instruments) == 1 else 0.25 + 0.5 * index / (len(active_instruments) - 1)
        for index, name in enumerate(active_instruments)
    }

    for event in events:
        start = round(event.start_beats * 60 / bpm * sample_rate)
        length = max(1, round(event.duration_beats * 60 / bpm * sample_rate))
        end = min(frame_count, start + length)
        if start >= frame_count or end <= start:
            continue

        sample_count = end - start
        time = np.arange(sample_count, dtype=np.float32) / sample_rate
        if event.instrument == "Drum kit":
            rng = np.random.default_rng(event.pitch * 1000 + start)
            noise = rng.standard_normal(sample_count).astype(np.float32)
            if event.pitch == DRUM_NOTES["Kick"]:
                phase = 2 * np.pi * (48 * time + 75 * (1 - np.exp(-time * 24)) / 24)
                signal = np.sin(phase) * np.exp(-time * 16)
            elif event.pitch == DRUM_NOTES["Snare"]:
                signal = (0.65 * noise + 0.35 * np.sin(2 * np.pi * 185 * time)) * np.exp(-time * 22)
            else:
                signal = (noise - np.concatenate((np.zeros(1, dtype=np.float32), noise[:-1]))) * np.exp(-time * 38)
            signal = signal * (event.velocity / 127) * 0.16
        else:
            settings = INSTRUMENTS[event.instrument]
            frequency = 440.0 * (2.0 ** ((event.pitch - 69) / 12))
            tone = np.zeros(sample_count, dtype=np.float32)
            for harmonic, amplitude in enumerate(settings["harmonics"], start=1):
                tone += amplitude * np.sin(2 * np.pi * frequency * harmonic * time)
            tone /= sum(settings["harmonics"])

            attack = min(0.035 if settings["decay"] < 1 else 0.008, sample_count / sample_rate)
            release = min(0.16, sample_count / sample_rate * 0.45)
            envelope = np.ones(sample_count, dtype=np.float32)
            attack_frames = min(sample_count, max(1, round(attack * sample_rate)))
            release_frames = min(sample_count, max(1, round(release * sample_rate)))
            envelope[:attack_frames] = np.linspace(0, 1, attack_frames, dtype=np.float32)
            envelope[-release_frames:] *= np.linspace(1, 0, release_frames, dtype=np.float32)
            envelope *= np.exp(-time * settings["decay"] / max(0.3, event.duration_beats * 60 / bpm))
            signal = tone * envelope * (event.velocity / 127) * 0.24

        pan = 0.5 if event.instrument == "Drum kit" else track_pan[event.instrument]
        left_gain = math.cos(pan * math.pi / 2)
        right_gain = math.sin(pan * math.pi / 2)
        audio[start:end, 0] += signal * left_gain
        audio[start:end, 1] += signal * right_gain

    if reverb:
        dry_audio = audio.copy()
        for delay_seconds, gain in ((0.11, 0.28), (0.19, 0.2), (0.29, 0.13)):
            delay_frames = round(delay_seconds * sample_rate)
            if delay_frames < frame_count:
                audio[delay_frames:] += dry_audio[:-delay_frames] * (reverb / 100) * gain

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio *= min(1.0, 0.92 / peak)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return output.getvalue(), duration_seconds


def _make_midi(events: list[NoteEvent], bpm: int, prompt: str) -> bytes:
    midi_file = mido.MidiFile(type=1, ticks_per_beat=480)
    tempo_track = mido.MidiTrack()
    midi_file.tracks.append(tempo_track)
    tempo_track.append(mido.MetaMessage("track_name", name="Song information", time=0))
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    tempo_track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    if prompt.strip():
        tempo_track.append(mido.MetaMessage("text", text=prompt.strip()[:500], time=0))

    active_instruments = [
        name for name in INSTRUMENTS
        if name != "Drum kit" and any(e.instrument == name for e in events)
    ]
    channels = [channel for channel in range(16) if channel != 9]
    tracks_to_write = [
        (channel, instrument, False)
        for channel, instrument in zip(channels, active_instruments)
    ]
    if any(event.instrument == "Drum kit" for event in events):
        tracks_to_write.append((9, "Drum kit", True))

    for channel, instrument, is_percussion in tracks_to_write:
        track = mido.MidiTrack()
        midi_file.tracks.append(track)
        track.append(mido.MetaMessage("track_name", name=instrument, time=0))
        if not is_percussion:
            track.append(
                mido.Message(
                    "program_change",
                    channel=channel,
                    program=INSTRUMENTS[instrument]["program"],
                    time=0,
                )
            )
        timed_messages: list[tuple[int, int, mido.Message]] = []
        for event in events:
            if event.instrument != instrument:
                continue
            start_tick = round(event.start_beats * midi_file.ticks_per_beat)
            end_tick = round((event.start_beats + event.duration_beats) * midi_file.ticks_per_beat)
            timed_messages.append(
                (start_tick, 1, mido.Message("note_on", channel=channel, note=event.pitch, velocity=event.velocity))
            )
            timed_messages.append(
                (end_tick, 0, mido.Message("note_off", channel=channel, note=event.pitch, velocity=0))
            )

        previous_tick = 0
        for tick, _, message in sorted(timed_messages, key=lambda item: (item[0], item[1])):
            message.time = tick - previous_tick
            track.append(message)
            previous_tick = tick

    output = io.BytesIO()
    midi_file.save(file=output)
    return output.getvalue()


def _apply_wav_reverb(wav_data: bytes, sample_rate: int, reverb: int) -> bytes:
    if not reverb:
        return wav_data

    try:
        with wave.open(io.BytesIO(wav_data), "rb") as rendered:
            if (
                rendered.getnchannels() != 2
                or rendered.getframerate() != sample_rate
                or rendered.getsampwidth() != 2
            ):
                raise SoundFontRenderError(
                    "FluidSynth returned an unexpected WAV format; expected 16-bit stereo at "
                    f"{sample_rate} Hz."
                )
            audio = np.frombuffer(rendered.readframes(rendered.getnframes()), dtype="<i2")
            audio = audio.reshape(-1, 2).astype(np.float32) / 32768
    except (wave.Error, EOFError, ValueError) as error:
        raise SoundFontRenderError(f"Could not process FluidSynth WAV audio: {error}") from error

    dry_audio = audio.copy()
    frame_count = len(audio)
    for delay_seconds, gain in ((0.11, 0.28), (0.19, 0.2), (0.29, 0.13)):
        delay_frames = round(delay_seconds * sample_rate)
        if delay_frames < frame_count:
            audio[delay_frames:] += dry_audio[:-delay_frames] * (reverb / 100) * gain

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio *= min(1.0, 0.98 / peak)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as rendered:
        rendered.setnchannels(2)
        rendered.setsampwidth(2)
        rendered.setframerate(sample_rate)
        rendered.writeframes(pcm.tobytes())
    return output.getvalue()


def _render_soundfont(
    midi_data: bytes,
    sample_rate: int,
    soundfont_path: str,
    fluidsynth_path: str | None,
    reverb: int,
) -> bytes:
    executable = locate_fluidsynth(fluidsynth_path)
    if not executable:
        raise SoundFontRenderError(
            "FluidSynth was not found. Install FluidSynth and add it to PATH, "
            "or set FLUIDSYNTH_PATH to the fluidsynth executable."
        )

    soundfont = Path(soundfont_path).expanduser()
    if not soundfont.is_file():
        raise SoundFontRenderError(f"SoundFont file not found: {soundfont}")
    if soundfont.suffix.lower() not in (".sf2", ".sf3"):
        raise SoundFontRenderError("Choose a SoundFont file with an .sf2 or .sf3 extension.")

    with tempfile.TemporaryDirectory(prefix="soundcraft-") as temp_dir:
        midi_file = Path(temp_dir) / "track.mid"
        wav_file = Path(temp_dir) / "track.wav"
        midi_file.write_bytes(midi_data)
        command = [
            str(executable),
            "-ni",
            "-R",
            "0",
            "-r",
            str(sample_rate),
            "-F",
            str(wav_file),
            str(soundfont),
            str(midi_file),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SoundFontRenderError(f"Could not run FluidSynth: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "No diagnostic was returned."
            raise SoundFontRenderError(f"FluidSynth could not render the track: {detail}")
        if not wav_file.is_file() or wav_file.stat().st_size == 0:
            raise SoundFontRenderError("FluidSynth finished without producing a WAV file.")

        wav_data = wav_file.read_bytes()
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as rendered:
                if (
                    rendered.getnchannels() != 2
                    or rendered.getframerate() != sample_rate
                    or rendered.getsampwidth() != 2
                ):
                    raise SoundFontRenderError(
                        "FluidSynth returned an unexpected WAV format; expected 16-bit stereo at "
                        f"{sample_rate} Hz."
                    )
        except (wave.Error, EOFError) as error:
            raise SoundFontRenderError(f"FluidSynth returned an invalid WAV file: {error}") from error
        return _apply_wav_reverb(wav_data, sample_rate, reverb)


def generate_music(
    prompt: str,
    genre: str,
    mood: str,
    key: str | None,
    bpm: int,
    bars: int,
    instruments: list[str],
    seed: int,
    sample_rate: int = 22050,
    progression_name: str = "Style default",
    reverb: int = 20,
    audio_engine: str = "Built-in synth",
    soundfont_path: str | None = None,
    fluidsynth_path: str | None = None,
    variation_strength: int = 4,
) -> GenerationResult:
    """Create a deterministic procedural composition as a MIDI file and synthesized WAV."""
    if not prompt.strip():
        raise ValueError("Enter a short description of the music you want.")
    if genre not in PROGRESSIONS:
        raise ValueError(f"Unsupported genre: {genre}")
    if mood not in MOOD_VELOCITY:
        raise ValueError(f"Unsupported mood: {mood}")
    if progression_name not in CHORD_PROGRESSIONS:
        raise ValueError(f"Unsupported chord progression: {progression_name}")
    resolved_key = resolve_key(key, genre, mood, prompt, seed)
    if not 50 <= bpm <= 180:
        raise ValueError("Tempo must be between 50 and 180 BPM.")
    if not 1 <= bars <= 16:
        raise ValueError("Track length must be between 1 and 16 bars.")
    if not instruments:
        raise ValueError("Select at least one instrument.")
    if any(instrument not in INSTRUMENTS for instrument in instruments):
        raise ValueError("One or more selected instruments are not supported.")
    if not 8000 <= sample_rate <= 48000:
        raise ValueError("Sample rate must be between 8,000 and 48,000 Hz.")
    if not 0 <= reverb <= 60:
        raise ValueError("Reverb must be between 0 and 60 percent.")
    if not 1 <= variation_strength <= 5:
        raise ValueError("Variation strength must be between 1 and 5.")
    if audio_engine not in ("Built-in synth", "FluidSynth + SoundFont"):
        raise ValueError(f"Unsupported audio engine: {audio_engine}")
    if audio_engine == "FluidSynth + SoundFont" and not soundfont_path:
        raise SoundFontRenderError(
            "Provide a SoundFont file path (.sf2 or .sf3) to use FluidSynth rendering."
        )

    events = _make_events(
        prompt,
        genre,
        mood,
        resolved_key,
        bpm,
        bars,
        instruments,
        seed,
        progression_name,
        variation_strength,
    )
    midi_data = _make_midi(events, bpm, prompt)
    duration_seconds = bars * 4 * 60 / bpm
    if audio_engine == "FluidSynth + SoundFont":
        wav_data = _render_soundfont(
            midi_data,
            sample_rate,
            soundfont_path or "",
            fluidsynth_path,
            reverb,
        )
    else:
        wav_data, duration_seconds = _render_wav(events, bpm, bars, sample_rate, reverb)
    return GenerationResult(wav_data, midi_data, duration_seconds, len(events), resolved_key)
