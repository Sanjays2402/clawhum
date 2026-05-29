# Recording flow

1. User taps the mic.
2. `MediaRecorder` records webm/opus.
3. AnalyserNode powers the waveform canvas at 60 fps.
4. On stop, the blob posts to `/api/match`.
5. Results render with album art, score, +/-.
