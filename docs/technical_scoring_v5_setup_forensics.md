# Technical Scoring v5 setup forensics

The frozen type-specific SQ is compared with the v4 old-max logic and one named hybrid:
`0.80 * primary + 0.10 * confirmation_1 + 0.10 * confirmation_2` (or 0.20 for a sole
confirmation), with the existing Stage modifier applied once. Results are separated by
setup type in `output/technical_v5/setup_forensics.csv`.

Momentum continuation is also sliced by extension, trigger state, Stage, volume,
regime, RSI and EQ. None of these research scores changes the shipped model.
