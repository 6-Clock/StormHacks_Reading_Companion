# Notebook interface plan

Build two persistent views, Reader and Developer, switched by the supplied green bookmark. Display LOOB above the notebook on a solid #2D2D2D background. Reuse the supplied notebook artwork, yellow highlights and blue/red ink, Lora story font, Architects Daughter notes, Google Sans Flex UI, and IBM Plex Mono diagnostics.

The Reader has a lined journal of actual questions and answers on the left and 16px story paragraphs on the right. It starts with blank lines and no suggested prompts. A microphone sits beside Voice log, with a typed question input and send button beneath the heading; Read/Stop sits in the story header. These controls share draft, recording, submission, and playback state with Developer. Explicit word questions highlight matching source text in yellow and show the actual answer on the supplied sticky note. Keep scanning, answer replay, and immersive sound settings in Developer. Keep the suspense demo for sound testing. Support narrow screens by stacking the pages and allow long passages and journals to scroll.

The Developer has an eye diagram driven by actual tracker estimates and an OCR panel with real scan metrics, the captured page photo with optional actual word boxes, and timestamped events. An atomic local snapshot connects the standalone tracker to a FastAPI diagnostics endpoint. Mark missing/stale tracking as disconnected; describe iris direction as an estimate. Never present simulated frames, word boxes, device acknowledgments, or calibrated word tracking as real data. Local Tesseract provides optional word geometry; AI remains responsible for the accepted transcript. Scanning is manual, not automatically triggered by page flips.

Verify bookmark switching by click, Enter, and Space, responsive layouts, session persistence, disconnected states, scan success/failure, question submission, and narration controls. Run production build, frontend lint, and focused backend tests; use gstack browse for browser checks. Physical camera validation remains a separate hardware check.

## Implemented and verified

- Reader and Developer notebook views, interactive bookmark navigation, supplied artwork, bundled fonts, and responsive stacked pages are implemented.
- Live eye telemetry, stale/disconnected handling, retained OCR metrics, and session event logs are implemented.
- Production build, frontend ESLint, all 12 backend tests, and diff whitespace checks pass.
- Initial gstack browser checks passed 22 assertions covering drafts and preferences across views, the original tab navigation, text selection and highlighting, live eye rendering, question success/failure, narration stop/planning, OCR acceptance/rejection, and horizontal overflow. Provider and scan responses were mocked for these interaction checks; the actual diagnostics endpoint was also checked in its offline state.
- Bookmark follow-up: verified click and Space navigation, draft preservation, accessible destination labels, exact #2D2D2D background, removed header/status text, and desktop/mobile layout without horizontal overflow. Frontend lint and TypeScript checks pass.
- Desktop and phone layouts were inspected with loaded local fonts/images and no browser console errors. Physical cameras, microphone recording hardware, and paid provider audio were not exercised in this frontend pass.

## Checklist refinement — October 4, 2026

Source: `LOOB_UI_checklist.xlsx`, sheet `UI 수정 체크리스트`, rows 7–30. The source workbook is unchanged.

- Removed Reader greeting, starter prompts, composer, replay controls, scan/narration controls, instructional slogans, sparkle icon, and visible scrollbars.
- Subsequent user refinement restores a Reader microphone beside Voice log, typed question input/send below it, and Read/Stop in the story header. Both views share their question and playback state; replay, immersive settings, and scanning stay in Developer.
- Kept the actual title and folio, shortened mode to Reading, and count distinct requested words on the current page.
- Kept Architects Daughter; use blue ink, subtle alternating line rotations, yellow word highlights, and the supplied yellow note for an actual answer.
- Simplified Appendix headings, offline/empty states, four eye metrics, monospace command log, and compact camera controls.
- Added captured OCR photos and normalized real word boxes, with honest unavailable states and preservation of the previous capture on cancellation.
- Verification: production build, frontend ESLint, 20 backend tests, and 29 mocked browser assertions passed. Desktop and phone layouts inspected. Paid services and physical camera/microphone hardware were not exercised.
