"""providers/ — GPU adapter package.

See base.py for the Protocol every adapter implements. The router in
scripts/gpu.py iterates configured adapters, but ships with none in its
default rank — compute-supplier selection now lives behind the
deidentified `rockie-gpu` broker (the single GPU surface; see the
gpu-spend and inference-engineer skills). The runpod.py adapter is
retained only because the legacy scripts/runtime/runpod.py CLI still
imports it; inject any other adapter ad-hoc via
`gpu.py --providers <dotted.module.path>`.
"""
