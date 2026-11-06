# Pilot route fixtures — Sjöfartsverket, Gothenburg pilot area

Official pilot-planned routes (RTZ format) published by the Swedish Maritime Administration
(Sjöfartsverket) for the Gothenburg pilot area. They are "intended ideal tracks" drawn by
professional pilots — used here as **ground-truth references** to benchmark and tune the planner
(see [docs/BENCHMARK.md](../../../docs/BENCHMARK.md)).

Source page: <https://www.sjofartsverket.se/sv/tjanster/lotsning/lotsomrade-goteborg/rutter_got/>
Full bundle (44 routes): `…/globalassets/tjanster/lotsning/lo-goteborg/routes/seaiq_all_waypoints_routes.rtz.zip`

This directory holds a small representative subset checked in for deterministic tests; download the
full bundle from the URL above for the complete benchmark set (kept out of git).

| File | Direction | Notes |
|------|-----------|-------|
| `01_PS1_via_N_Ch_to_Frihamnen.rtz`        | inbound  | Pilot Station 1 → North Channel → Frihamnen |
| `02_PS1_via_S_Ch_to_Frihamnen.rtz`        | inbound  | same endpoints via South Channel (a deliberate alternative) |
| `31_Frihamnen_via_N_Ch_to_PS1.rtz`        | outbound | reverse of 01 |
| `10_VLCC_via_N_Ch_to_Torshamnen_R1NM.rtz` | inbound  | deep-draft VLCC variant |
| `11X_Sankberget_via_N_Ch_to_Arendal_CT.rtz` | inbound | Arendal terminal approach |

Format: RTZ (CIRM RTZ, namespace `http://www.cirm.org/RTZ/1/0`). XTD and turn `radius` are in
nautical miles; `geometryType` is `Orthodrome`/`Loxodrome`.
