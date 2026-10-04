# Notebook interface

Reader and Developer share one accepted page and GPT-Live conversation. The green
bookmark switches between them; use the bundled notebook artwork and fonts and
stack pages on small screens.

Reader shows the journal transcript, typed input, microphone/session control,
and Read/Stop. Developer adds eye measurements, camera framing/capture/cancel,
accepted text, and actual capture/session events. Preserve the accepted page when
capture or transcription fails. Show disconnected camera/session states honestly.

The voice hook owns WebRTC and hosted model delegation. Capture requests start
immediately; manual framing waits for Capture. Captures made while disconnected
wait for the browser session before OCR. Word highlights, sticky-note answers,
requested-word counts, word-box geometry, immersive modes, and sound effects have
been removed.

Current setup and behavior are documented in [README.md](../README.md), and the
backend API is documented in [backend/README.md](../backend/README.md). Validate
bookmark keyboard/click navigation, shared state, text and voice submission,
Stop/reconnect, accepted/rejected/cancelled captures, and narrow-screen layout
through the mocked browser tests. Live audio, model fidelity, OCR latency, and
physical hardware still require target-device checks.
