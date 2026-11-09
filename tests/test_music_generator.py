import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import mido
import numpy as np

from music_generator import (
    CHORD_PROGRESSIONS,
    INSTRUMENTS,
    KEYS,
    SoundFontRenderError,
    generate_music,
    interpret_prompt,
    resolve_instruments,
    resolve_key,
)


class GenerateMusicTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "prompt": "gentle evening piano",
            "genre": "Ambient",
            "mood": "Calm",
            "key": "C major",
            "bpm": 90,
            "bars": 2,
            "instruments": ["Piano", "Warm synth"],
            "seed": 123,
        }

    def test_creates_readable_wav_and_midi(self):
        result = generate_music(**self.settings)

        with wave.open(io.BytesIO(result.wav_data), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(wav_file.getframerate(), 22050)
            self.assertAlmostEqual(wav_file.getnframes() / wav_file.getframerate(), 2 * 4 * 60 / 90, places=2)

        midi_file = mido.MidiFile(file=io.BytesIO(result.midi_data))
        self.assertEqual(midi_file.type, 1)
        self.assertEqual(len(midi_file.tracks), 3)
        self.assertGreater(result.note_count, 0)
        self.assertIn("Piano", [track.name for track in midi_file.tracks])

    def test_same_settings_produce_same_files(self):
        first = generate_music(**self.settings)
        second = generate_music(**self.settings)

        self.assertEqual(first.wav_data, second.wav_data)
        self.assertEqual(first.midi_data, second.midi_data)

    def test_prompt_is_required(self):
        with self.assertRaisesRegex(ValueError, "description"):
            generate_music(**{**self.settings, "prompt": "  "})

    def test_at_least_one_instrument_is_required(self):
        with self.assertRaisesRegex(ValueError, "instrument"):
            generate_music(**{**self.settings, "instruments": []})

    def test_auto_key_is_repeatable_and_mood_compatible(self):
        first = resolve_key(None, "Ambient", "Mysterious", "quiet night", 15)
        second = resolve_key(None, "Ambient", "Mysterious", "quiet night", 15)

        self.assertEqual(first, second)
        self.assertIn(first, KEYS)
        self.assertTrue(first.endswith("minor"))

    def test_auto_key_is_used_for_generation(self):
        result = generate_music(**{**self.settings, "key": None})

        self.assertIn(result.resolved_key, KEYS)
        self.assertEqual(result.resolved_key, resolve_key(None, "Ambient", "Calm", self.settings["prompt"], 123))

    def test_new_instruments_generate_midi_tracks(self):
        instruments = ["Electric piano", "Acoustic guitar", "Violin", "Flute", "Marimba"]
        result = generate_music(**{**self.settings, "instruments": instruments})
        midi_file = mido.MidiFile(file=io.BytesIO(result.midi_data))
        track_names = [track.name for track in midi_file.tracks]

        self.assertEqual(set(track_names[1:]), set(instruments))
        self.assertNotIn(9, [message.channel for track in midi_file.tracks for message in track if hasattr(message, "channel")])

    def test_selected_progression_and_drum_kit_export(self):
        result = generate_music(
            **{
                **self.settings,
                "instruments": ["Piano", "Drum kit"],
                "progression_name": "ii–V–I–vi (Jazz)",
            }
        )
        midi_file = mido.MidiFile(file=io.BytesIO(result.midi_data))
        drum_track = next(track for track in midi_file.tracks if track.name == "Drum kit")

        self.assertIn("Piano", [track.name for track in midi_file.tracks])
        self.assertIn(9, [message.channel for message in drum_track if hasattr(message, "channel")])
        self.assertFalse(any(message.type == "program_change" for message in drum_track))
        self.assertGreater(result.note_count, 0)
        self.assertIn("ii–V–I–vi (Jazz)", CHORD_PROGRESSIONS)

    def test_reverb_changes_audio_and_is_validated(self):
        dry = generate_music(**{**self.settings, "reverb": 0})
        wet = generate_music(**{**self.settings, "reverb": 40})

        self.assertNotEqual(dry.wav_data, wet.wav_data)
        with self.assertRaisesRegex(ValueError, "Reverb"):
            generate_music(**{**self.settings, "reverb": 80})

    def test_interprets_common_music_descriptors(self):
        interpretation = interpret_prompt(
            "A dreamy ambient piece in D minor at 72 BPM, 8 bars, with flute and subtle reverb"
        )

        self.assertEqual(interpretation.genre, "Ambient")
        self.assertEqual(interpretation.mood, "Dreamy")
        self.assertEqual(interpretation.key, "D minor")
        self.assertEqual(interpretation.bpm, 72)
        self.assertEqual(interpretation.bars, 8)
        self.assertIn("Flute", interpretation.add_instruments)
        self.assertEqual(interpretation.reverb, 15)

    def test_interpreter_respects_instrument_negation(self):
        interpretation = interpret_prompt("A calm piano melody without drums or bells")

        self.assertIn("Piano", interpretation.add_instruments)
        self.assertIn("Drum kit", interpretation.remove_instruments)
        self.assertIn("Bell", interpretation.remove_instruments)
        self.assertNotIn("Drum kit", interpretation.add_instruments)

    def test_explicit_prompt_instruments_replace_sidebar_defaults(self):
        interpretation = interpret_prompt("A solo flute melody")

        self.assertEqual(resolve_instruments(["Piano", "Warm synth", "Bass"], interpretation), ["Flute"])

    def test_prompt_instrument_exclusions_apply_to_explicit_and_default_choices(self):
        interpretation = interpret_prompt("Flute and piano, without piano")

        self.assertEqual(resolve_instruments(["Warm synth", "Bass"], interpretation), ["Flute"])

    def test_interprets_progression_and_reverb_cues(self):
        interpretation = interpret_prompt("Jazz with ii-V-I chords and no reverb")

        self.assertEqual(interpretation.genre, "Jazz")
        self.assertEqual(interpretation.progression_name, "ii–V–I–vi (Jazz)")
        self.assertEqual(interpretation.reverb, 0)

    def test_unknown_prompt_cues_leave_controls_unchanged(self):
        interpretation = interpret_prompt("A song for a rainy afternoon")

        self.assertIsNone(interpretation.genre)
        self.assertIsNone(interpretation.mood)
        self.assertIsNone(interpretation.bpm)
        self.assertEqual(interpretation.add_instruments, ())
        self.assertEqual(interpretation.remove_instruments, ())

    def test_soundfont_renderer_requires_a_soundfont_path(self):
        with self.assertRaisesRegex(SoundFontRenderError, "SoundFont file path"):
            generate_music(**{**self.settings, "audio_engine": "FluidSynth + SoundFont"})

    def test_soundfont_renderer_renders_midi_and_applies_reverb(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            soundfont = Path(temp_dir) / "test.sf2"
            soundfont.write_bytes(b"test soundfont")

            def fake_fluidsynth(command, **_kwargs):
                wav_path = Path(command[command.index("-F") + 1])
                samples = np.zeros((22050, 2), dtype="<i2")
                samples[0] = (12000, 12000)
                with wave.open(str(wav_path), "wb") as wav_file:
                    wav_file.setnchannels(2)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(22050)
                    wav_file.writeframes(samples.tobytes())
                return type("CompletedProcess", (), {"returncode": 0, "stderr": "", "stdout": ""})()

            with patch("music_generator.subprocess.run", side_effect=fake_fluidsynth) as synth:
                dry = generate_music(
                    **{
                        **self.settings,
                        "audio_engine": "FluidSynth + SoundFont",
                        "soundfont_path": str(soundfont),
                        "fluidsynth_path": "fluidsynth",
                        "reverb": 0,
                    }
                )
                wet = generate_music(
                    **{
                        **self.settings,
                        "audio_engine": "FluidSynth + SoundFont",
                        "soundfont_path": str(soundfont),
                        "fluidsynth_path": "fluidsynth",
                        "reverb": 40,
                    }
                )

        self.assertEqual(synth.call_count, 2)
        self.assertIn("-R", synth.call_args.args[0])
        self.assertNotEqual(dry.wav_data, wet.wav_data)
        with wave.open(io.BytesIO(wet.wav_data), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(wav_file.getsampwidth(), 2)


if __name__ == "__main__":
    unittest.main()
