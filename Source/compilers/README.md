# MixSplitR Build Files

This folder holds packaging and compiler-related files for the source bundle.

## What Lives Here

- `MixSplitR_ONEDIR.spec`
- `MixSplitR_ONEFILE.spec`
- `MixSplitR_ONEDIR_Installer.iss`
- `build_mac_dmg_TERMINAL.sh`
- `build_mac_dual_arch.sh`
- `setup_mac_dual_venvs.sh`
- `compile_windows_v72.bat`
- icon/background helper scripts
- `windows_process_loopback/ProcessLoopbackCaptureHelper.cpp`

## Expected Layout

These files expect the project root to be the parent directory of `compilers/`.

That means:

- runtime source stays in the project root
- runtime assets like `mixsplitr.png`, `mixsplitr_icon_512.png`, `fonts/`, and `mixsplitr_process_loopback.exe` stay in the project root
- build outputs land in the project root under `dist/`, `build/`, `release_artifacts/`, or app bundle outputs

## Typical Commands

macOS:

```bash
./compilers/setup_mac_dual_venvs.sh
./compilers/build_mac_dmg_TERMINAL.sh
./compilers/build_mac_dual_arch.sh
```

Windows:

```powershell
compilers\\compile_windows_v72.bat --onedir
compilers\\compile_windows_v72.bat --installer
```
