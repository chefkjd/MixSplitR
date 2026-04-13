Place Roboto font files in this folder to bundle them with the app build.

Supported filenames are any `Roboto*.ttf` or `Roboto*.otf`.

Recommended set:
- `Roboto-Regular.ttf`
- `Roboto-Medium.ttf`
- `Roboto-Bold.ttf`

Build notes:
- `MixSplitR_ONEFILE.spec` automatically includes these files in the bundle under `/fonts`.
- `main_ui.py` auto-registers bundled Roboto fonts at startup.
