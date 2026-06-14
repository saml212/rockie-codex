---
name: physics
description: Route physics simulation, modeling, validation, and research-compute requests across force fields, molecular dynamics, electronic structure, particle transport/collision, continuum multiphysics, plasma/PIC, nuclear/radiation, and astro/cosmology. Use open-source-first engines, refuse local heavyweight execution on the orchestrator, and attach physics dashboard monitoring profiles to run plans.
scope: community
contributor_name: Nikhil Rao
contributed_at: "2026-05-28"
---

# Physics Research Router

Use this top-level skill for physics requests that imply simulation,
calculation, modeling, validation, input-deck generation, run monitoring,
or result interpretation. Current skill discovery is top-level only, so
the domain modules are routed references under `references/` rather than
nested discoverable skills.

Physics is a router by default, not a direct provisioner. If a user asks
for direct monitor status without a bound underlying run owner, return
`router_not_provisioner`. Executed-run monitoring belongs to the owning provisioner parent skill such as `experiment` or `inference-engineer`, not the physics router itself.

## First Steps

1. Classify the request into one family:
   `force_fields`, `molecular_dynamics`, `electronic_structure`,
   `particle_transport_collision`, `continuum_multiphysics`,
   `plasma_pic`, `nuclear_radiation`, `astro_cosmology`, or
   `literature_only`.
2. If classification is ambiguous and the answer would materially change
   the engine or safety posture, ask at most two clarifying questions.
   If the user does not answer, choose the safest open-source-first
   default and state the assumption.
3. Load the routed reference module plus `references/licensing.md` and
   `references/software-landscape.md`.
4. Write a run plan before execution or handoff.
5. Attach the matching dashboard profile id and preserve every generated
   config, script, log, parser output, artifact path, and Note outline.

Non-computational literature review requests route to source search and
note writing only. Do not invent a simulation run.

## Router Rules

| Signal | Family | Reference |
|---|---|---|
| Missing parameters, charges, water/ion compatibility, metals, surfaces, unusual residues, mixing rules | `force_fields` | `references/force-fields.md` |
| Classical atomistic dynamics, LAMMPS/GROMACS/OpenMM, trajectory, thermostat, barostat, enhanced sampling | `molecular_dynamics` | `references/molecular-dynamics.md` |
| DFT, SCF, band structure, orbitals, phonons, geometry optimization, AIMD setup | `electronic_structure` | `references/electronic-structure.md` |
| Detector simulation, event generation, collisions, PYTHIA, MadGraph, Geant4, ROOT | `particle_transport_collision` | `references/particle-transport-collision.md` |
| CFD, FEA, mesh, residuals, CFL, stress, heat transfer, coupled PDEs, OpenFOAM, SU2, MOOSE, CalculiX | `continuum_multiphysics` | `references/continuum-multiphysics.md` |
| Plasma sheath, PIC, fields plus particles, WarpX, Smilei, openPMD, Courant constraints | `plasma_pic` | `references/plasma-pic.md` |
| Neutron/photon transport, reactor, shielding, tallies, depletion, source convergence, OpenMC, MCNP | `nuclear_radiation` | `references/nuclear-radiation.md` |
| Galaxy, cosmology, N-body, MHD, redshift, Athena++, GADGET-family workflows | `astro_cosmology` | `references/astro-cosmology.md` |
| Literature review, compare papers, summarize methods without running | `literature_only` | source search and note writing |

Ambiguous defaults:

- Atomistic dynamics with empirical potentials defaults to
  `molecular_dynamics`, with `force_fields` loaded as a dependency.
- Detector, collision, event, or ROOT language defaults to
  `particle_transport_collision`, unless neutron/photon/reactor/tally
  language points to `nuclear_radiation`.
- "Can you calculate this property?" defaults to asking one question
  about scale/method when scale is unclear; otherwise use the family
  implied by system size and observable.

## Run Plan Shape

Before execution, handoff, or artifact-only generation, write this shape:

```yaml
family: molecular_dynamics
confidence: high
physical_model: Lennard-Jones fluid NVT smoke benchmark
engine: LAMMPS
engine_license_posture: open_source
required_inputs:
  - input deck
  - initial structure or generated lattice
expected_outputs:
  - stdout/stderr log
  - thermo metrics JSON
  - plot artifact path
  - result or failure Note
compute_mode: rockie_compute | user_hpc_ssh | tenant_runtime | github_handoff | artifact_only
budget_cap: "smoke first; no full run without explicit cap"
dashboard_profile_id: physics.molecular_dynamics.v1
stop_conditions:
  - energy drift exceeds profile threshold
  - missing parameter or license check fails
  - output freshness gap exceeds profile max_gap_seconds
notes:
  dashboard_note: required for executed runs
  result_note: required on success
  failure_note: required on failure or blocked license/environment check
```

## Compute Routing

Prefer credible open-source engines. Run cheap smoke tests before full
runs. Full runs require explicit compute mode, budget cap, and spend
authorization.

Local heavyweight execution is forbidden on the orchestrator host.
Do not run or import LAMMPS, GROMACS, OpenMM, Quantum ESPRESSO, CP2K,
Geant4, ROOT, OpenFOAM, SU2, MOOSE, WarpX, Smilei, OpenMC, Athena++,
GADGET-family engines, VASP, Gaussian, ORCA, COMSOL, Ansys, Amber,
CHARMM, MCNP, or equivalent heavy scientific engines locally. Generate
scripts/input decks locally, then route execution through Rockie compute,
tenant runtime, user HPC/SSH, GitHub handoff, or artifact-only review.

When execution is not safe or available, be explicit: create the handoff
package, expected-output checklist, parser/profile fixture, dashboard
Note outline, and result/failure Note outline without claiming the run
occurred.

Fixture and handoff artifacts are not executed dogfood evidence. Executed
dogfood requires actual dashboard, result, or failure Notes from a later
cascade phase, or an explicit partial/blocking status when infrastructure
is unavailable.

## License Policy

Every named package must have one posture:
`open_source`, `free_academic_or_noncommercial`,
`commercial_or_proprietary`, `restricted_export_or_controlled`, or
`unknown_needs_review`.

If a user asks for proprietary or restricted software, do not download,
bundle, scrape, bypass, crack, or expose license material. Use only a
researcher-provided authorized environment such as tenant secrets,
SSH/HPC modules, a license server, an existing binary path, or mounted
filesystem. Redact license tokens, sensitive server strings, private
endpoints, and user-marked sensitive paths from chat, logs, Notes, and
artifacts. If verification fails, stop before compute and create a
failure Note plan with open-source fallback options.

## Dashboard and Note Requirements

Every run plan names a `physics.*.v1` monitoring profile from
`runtime/monitoring-profiles/`. Parser outputs must use the metric/event
envelope fixture shape in `runtime/parser-fixtures/`.

Successful executed runs require:

- dashboard Note with profile id, active metrics, stop policy, and
  artifact links
- result Note with scientific summary, method, software/version, inputs,
  parameters, compute shape, budget/spend, key plots/tables, limitations,
  and next experiments

Failed or blocked runs require:

- dashboard or failure Note with failure class, last healthy metrics,
  suspected cause, implicated file/config, preserved partial artifacts,
  and relaunch plan gated on budget/authorization

Do not mutate source records to store dashboard/result outputs. Sources
are inputs; Notes and artifacts are outputs.
