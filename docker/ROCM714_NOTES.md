# ROCm 7.14 preview backend - parked, not abandoned

## Status: disabled

`rocm` (7.2.4 stable) and `vulkan` both build and run successfully.
`rocm714` (preview/TheRock) is disabled - the pip venv install succeeds,
but the llama.cpp BUILD against it is skipped.

## Why

Installed via:
  pip install --index-url https://repo.amd.com/rocm/whl-multi-arch/ "rocm[libraries,devel,device-gfx1201]==7.14.0"

Confirmed via exhaustive `find /opt/rocm714-venv -name hip-lang-config.cmake`
that this file DOES NOT EXIST anywhere in the installed wheel tree. This
pip package appears to target *running* pre-built ROCm workloads (e.g.
PyTorch), not compiling new HIP source like llama.cpp.

Debugging trail (each a real, separate bug, in order encountered):
1. hipcc is a Python wrapper, not a real compiler - CMake's HIP language
   support rejects it. Fix: find the real ELF amdclang++ binary instead.
2. Global ROCM_PATH/HIP_PATH env vars overrode -D CMake flags, because
   ROCm's cmake configs read the environment variables directly.
3. hip-lang-config.cmake missing entirely - the actual wall, not fixable
   by path/env adjustments.

## To revisit later
- Check for a pip extra that ships hip-lang-config.cmake
- Check newer 7.14.x patches or 7.15+
- Check if ROCm 7.14 ever gets an apt-based install (sidesteps this)
