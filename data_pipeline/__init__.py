"""Gen 9 data pipeline for Pokemon Island Battle Royale (stdlib only).

Stages (each writes distinct artifacts; see README.md):
  pin   -> source.lock.json            (upstream revision)
  fetch -> data/cache/showdown/<sha>/  (raw checkout + raw JSON export)   [network]
  build -> data/gen9/                  (normalized DB, learnsets, engine records, reports) [offline]
"""
__version__ = "0.1.0"
