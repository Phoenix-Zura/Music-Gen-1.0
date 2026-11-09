from __future__ import annotations

import os

import streamlit as st

from music_generator import (
    CHORD_PROGRESSIONS,
    INSTRUMENTS,
    KEYS,
    SoundFontRenderError,
    generate_music,
    interpret_prompt,
    locate_fluidsynth,
    resolve_instruments,
)


st.set_page_config(
    page_title="Soundcraft | Music Studio",
    page_icon="🎼",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(135deg, #0b1020 0%, #13182b 52%, #111322 100%); }
    [data-testid="stSidebar"] { background: rgba(15, 20, 36, 0.96); }
    .hero { padding: 1.2rem 0 0.8rem; }
    .hero h1 { font-size: clamp(2.4rem, 5vw, 4.4rem); letter-spacing: -0.06em; margin: 0; }
    .hero p { color: #a7aec4; font-size: 1.08rem; margin-top: 0.7rem; }
    .eyebrow { color: #a995ff; text-transform: uppercase; letter-spacing: .16em;
               font-size: .75rem; font-weight: 700; }
    .note { border: 1px solid rgba(169, 149, 255, .22); border-radius: 12px;
            padding: 1rem 1.1rem; background: rgba(169, 149, 255, .07);
            color: #c9cce0; font-size: .9rem; }
    div[data-testid="stMetric"] { background: rgba(255,255,255,.035); padding: 1rem;
                                  border: 1px solid rgba(255,255,255,.07); border-radius: 12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "generation" not in st.session_state:
    st.session_state.generation = None

st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">A small studio for big ideas</div>
      <h1>Make a little <span style="color:#a995ff">music.</span></h1>
      <p>Shape a musical sketch, listen to it, then take the MIDI into your favorite DAW.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("## 🎛️ Studio settings")
    genre = st.selectbox("Style", ["Pop", "Lo-fi", "Ambient", "Jazz", "Cinematic"])
    mood = st.selectbox("Mood", ["Dreamy", "Calm", "Energetic", "Mysterious", "Hopeful"])
    key_options = ["Auto (choose for me)", *KEYS]
    selected_key = st.selectbox(
        "Key",
        key_options,
        help="Leave this on Auto to choose a repeatable key that fits the mood.",
    )
    bpm = st.slider("Tempo", min_value=50, max_value=180, value=92, step=1, format="%d BPM")
    bars = st.slider("Length", min_value=2, max_value=16, value=8, step=2, format="%d bars")
    progression_name = st.selectbox("Chord progression", list(CHORD_PROGRESSIONS))
    reverb = st.slider("Reverb", min_value=0, max_value=60, value=20, step=5, format="%d%%")
    instruments = st.multiselect(
        "Instruments",
        list(INSTRUMENTS),
        default=["Piano", "Warm synth", "Bass"],
    )
    audio_engine = st.selectbox(
        "Audio renderer",
        ["Built-in synth", "FluidSynth + SoundFont"],
        help="FluidSynth uses sampled instrument sounds from a SoundFont file.",
    )
    soundfont_path = ""
    fluidsynth_path = locate_fluidsynth() or ""
    if audio_engine == "FluidSynth + SoundFont":
        soundfont_path = st.text_input(
            "SoundFont file (.sf2 / .sf3)",
            value=os.environ.get("SOUNDFONT_PATH", ""),
            placeholder=r"C:\path\to\soundfont.sf2",
            help="Use a SoundFont you have permission to use. It is not included with this app.",
        )
        fluidsynth_path = st.text_input(
            "FluidSynth executable (optional)",
            value=fluidsynth_path,
            placeholder="fluidsynth (if available on PATH)",
            help="Leave blank to find FluidSynth on PATH, or provide its full executable path.",
        )
    seed = st.number_input("Variation seed", min_value=0, max_value=999999, value=42, step=1)

left, right = st.columns([1.45, 0.85], gap="large")
with left:
    st.markdown("### Describe your idea")
    with st.form("generation_form"):
        prompt = st.text_area(
            "Music description",
            placeholder="A mellow late-night groove with soft keys and a hopeful melody...",
            height=130,
            label_visibility="collapsed",
            max_chars=500,
        )
        submitted = st.form_submit_button("✨  Compose track", type="primary", use_container_width=True)
    st.markdown(
        '<div class="note"><strong>Offline prompt interpreter</strong> · Common style, mood, '
        'instrument, key, tempo, chord, and effect phrases can guide the track. Explicit prompt '
        'cues override matching sidebar settings; named instruments replace the sidebar instrument '
        'selection. Anything not recognized uses your selections.</div>',
        unsafe_allow_html=True,
    )

with right:
    st.markdown("### Your setup")
    key_label = "Auto (chosen on compose)" if selected_key == "Auto (choose for me)" else selected_key
    st.write(f"**{genre}** · {mood.lower()} · {key_label}")
    st.write(f"♩ {bpm} BPM &nbsp; · &nbsp; {bars} bars", unsafe_allow_html=True)
    st.write(f"**Progression:** {progression_name}")
    st.write(f"**Reverb:** {reverb}%")
    st.write("**Playing:** " + (", ".join(instruments) if instruments else "Choose at least one instrument"))
    st.caption("Every result includes an audio preview, WAV download, and editable MIDI.")

if submitted:
    try:
        interpretation = interpret_prompt(prompt)
        effective_genre = interpretation.genre or genre
        effective_mood = interpretation.mood or mood
        effective_key = interpretation.key or (
            None if selected_key == "Auto (choose for me)" else selected_key
        )
        effective_bpm = interpretation.bpm or bpm
        effective_bars = interpretation.bars or bars
        effective_progression = interpretation.progression_name or progression_name
        effective_reverb = interpretation.reverb if interpretation.reverb is not None else reverb
        effective_instruments = resolve_instruments(instruments, interpretation)

        with st.spinner("Composing and rendering your track..."):
            result = generate_music(
                prompt=prompt,
                genre=effective_genre,
                mood=effective_mood,
                key=effective_key,
                bpm=effective_bpm,
                bars=effective_bars,
                instruments=effective_instruments,
                seed=int(seed),
                progression_name=effective_progression,
                reverb=effective_reverb,
                audio_engine=audio_engine,
                soundfont_path=soundfont_path or None,
                fluidsynth_path=fluidsynth_path or None,
            )
        st.session_state.generation = {
            "result": result,
            "prompt": prompt.strip(),
            "genre": effective_genre,
            "mood": effective_mood,
            "key": result.resolved_key,
            "bpm": effective_bpm,
            "bars": effective_bars,
            "instruments": effective_instruments,
            "seed": int(seed),
            "progression_name": effective_progression,
            "reverb": effective_reverb,
            "audio_engine": audio_engine,
            "recognized_cues": interpretation.recognized_cues,
        }
    except ValueError as error:
        st.error(str(error))
    except SoundFontRenderError as error:
        st.error(str(error))

generation = st.session_state.generation
if generation:
    st.divider()
    result = generation["result"]
    st.markdown("## Your latest track")
    st.caption(
        f"{generation['genre']} · {generation['mood']} · {generation['key']} · "
        f"{generation['bpm']} BPM · {generation['bars']} bars"
    )
    st.caption("Instruments: " + ", ".join(generation["instruments"]))
    st.caption(
        f"Progression: {generation['progression_name']} · Reverb: {generation['reverb']}% · "
        f"Renderer: {generation['audio_engine']}"
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Duration", f"{result.duration_seconds:.1f} sec")
    metric_columns[1].metric("Musical events", result.note_count)
    metric_columns[2].metric("Tempo", f"{generation['bpm']} BPM")

    st.audio(result.wav_data, format="audio/wav")
    safe_name = f"soundcraft_{generation['genre'].lower()}_{generation['bpm']}bpm"
    download_columns = st.columns([1, 1, 2])
    download_columns[0].download_button(
        "⬇ Download WAV",
        data=result.wav_data,
        file_name=f"{safe_name}.wav",
        mime="audio/wav",
        use_container_width=True,
    )
    download_columns[1].download_button(
        "⬇ Download MIDI",
        data=result.midi_data,
        file_name=f"{safe_name}.mid",
        mime="audio/midi",
        use_container_width=True,
    )
    with st.expander("Generation details"):
        st.write(f"**Prompt:** {generation['prompt']}")
        st.write(f"**Variation seed:** {generation['seed']}")
        if generation["recognized_cues"]:
            st.write("**Interpreted as:** " + " · ".join(generation["recognized_cues"]))
        else:
            st.write("**Interpreted as:** No supported music cues detected; sidebar settings were used.")
        st.caption("The MIDI contains separate instrument tracks, tempo, time signature, and the prompt text.")
