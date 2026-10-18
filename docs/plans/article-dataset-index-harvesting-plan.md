# Implementation Plan: Article ↔ Dataset Cross-Reference Index

## 1. Goal

Build a persistent index mapping **article identifiers (PMID/PMCID/DOI)** to
every **dataset accession** across our clients (PRIDE/PXD, MetaboLights/MTBLS,
Metabolomics Workbench/ST, GNPS2/MassIVE/MSV, plus BioProject/BioSample as
secondary signals) that is linked to that article — so a single lookup
answers "what datasets does this paper describe?" and "what articles touch
this dataset?" in either direction. This is the entity-resolution bridge from
[metabolomics-clients-implementation-plan.md §10](./metabolomics-clients-implementation-plan.md)
(this plan picks up where that addendum paused: bulk-harvesting it, not just
resolving one dataset at a time).

You proposed starting from PRIDE (easiest — PMIDs are often self-reported
there) and using DOI as a common indexer. Both are right, and the research
below confirms and sharpens them with real numbers.

## 2. What I verified before designing the pipeline

### 2.1 PRIDE has a bulk endpoint, not just per-project lookups — and it's cheap

`GET https://www.ebi.ac.uk/pride/ws/archive/v3/search/projects?pageSize=100&page=N`
returns the **same `references` field** (with `pubMed:` / `doi:` embedded in
the citation string) as the per-project detail endpoint, paginated.

- Total public PXD projects right now: **42,133** (`total_records` response header).
- At `pageSize=100`, that's **~422 HTTP calls** to sweep every single PRIDE
  project's self-reported publication — not 42,133 individual detail calls.
- This is by far the cheapest, highest-confidence seed source we have, and
  matches your instinct exactly: start here.

### 2.2 MetaboLights and Metabolomics Workbench both have bulk accession lists, but no bulk publication field

- MetaboLights `GET /ws/studies` returns all public study accessions in one
  call: **3,543 studies** currently (matches the report's cited ~3,493,
  consistent with organic growth since scouting). But publications are only
  available per-study (no bulk `references` field like PRIDE's), so this
  requires up to ~3,543 detail calls to sweep self-reported pubs.
- Metabolomics Workbench `GET /rest/study/study_id/ST/available`-style
  wildcard returns all `study_id`/`project_id`/`analysis_id` triples in one
  call (**7,625 rows**, collapsing to roughly the ~4,620 unique studies the
  report cites). But as established in the §10 addendum, MW's clean JSON API
  never exposes PMID/DOI at all — only the legacy `mwtab/txt` dump's
  free-text `PR:PUBLICATIONS` field does, needing per-study fetch + regex.
- GNPS2's `/api/datasets` already returns its **entire** ~13.5k-dataset
  metabolomics inventory in one call, but — as found previously — carries
  **no publication field whatsoever**.

### 2.3 Important caveat: Europe PMC's own pre-built annotation index does *not* cover PXD/MTBLS

I checked whether Europe PMC's Annotations API (a pre-computed, bulk-queryable
text-mining layer) already extracts ProteomeXchange/MetaboLights accessions,
which would have let us skip our own per-article full-text regex entirely.
Running it against PMC13625717 (the article we know cites `PXD076809` and
`MTBLS14236`) returned 1,027 annotations across types like Gene_Proteins,
Diseases, Chemicals, Organisms — and an `"Accession Numbers"` type that only
recognized an **ENA/GenBank** accession and a DOI reference. **Neither PXD076809
nor MTBLS14236 were present under any annotation type.** Europe PMC's curated
accession recognizer doesn't have ProteomeXchange/MetaboLights patterns
registered.

**Implication:** we cannot shortcut this with Europe PMC's bulk annotation
dump. We have to keep using our own `europepmc_client.find_accessions_in_publication()`
(full-text XML + regex, already built and validated) per article. That's
fine at pilot scale (hundreds–low thousands of articles) via per-article API
calls, but if this ever needs to scale to the full OA corpus, the better
architecture is Europe PMC's **bulk OA full-text XML FTP dump**
(`ftp://ftp.ebi.ac.uk/pub/databases/pmc/oa_bulk/`, updated weekly) downloaded
once and grepped locally, rather than one `fullTextXML` API call per article.
Not needed for the pilot scope below — flagging it now so the harvester's
"fetch full text" step is written behind a small interface that can be
swapped from per-article API calls to local-file lookups later without
touching the rest of the pipeline.

## 3. Identifier normalization: DOI vs PMID as the canonical key

Agreeing with your instinct to use DOI as a common indexer: **DOI is the
preferred canonical key when present** (assigned at publication regardless of
PubMed indexing, used identically by preprint servers, data repositories'
own self-minted DOIs, and journals), with **PMID/PMCID stored as alternate
IDs on the same article record**, not separate records. Concretely:

- One canonical `articles` row per real-world publication, with nullable
  `pmid`, `pmcid`, `doi` columns, each uniquely indexed.
- Before inserting a "new" article, look it up by whichever identifiers are
  available (any of the three matching an existing row merges into it rather
  than creating a duplicate).
- Use the existing `ncbi_client.py` (`pmid_to_pmcid`, `search_by_doi`) and
  `europepmc_client.py` (`fetch_publication`) utilities to backfill whichever
  of PMID/PMCID/DOI a given source didn't supply, so every article row ends
  up with as complete an identifier triad as the literature record allows.
- **Watch out for the self-minted dataset DOI trap** (found in the previous
  pass): PRIDE's `doi` field at the project level (e.g. `10.6019/PXD000001`)
  identifies *the dataset*, not the paper. Only the `references[].doi` /
  `references[].pubmedID` fields (or Metabolomics Workbench's `10.21228/...`
  dataset DOI vs. its `PR:PUBLICATIONS` paper reference) are the actual
  article identifiers. The harvester must keep these two identifier
  *roles* (dataset-citation DOI vs. article DOI) in separate columns so they
  are never accidentally merged.

## 4. Revised architecture: per-modality registers, then a separate connection-finding pass

Simplified per feedback, and it's a cleaner design, not just a simpler one:
**decouple register-building from connection-finding entirely.**

- **Stage A — build one independent register per modality.** For PRIDE,
  MetaboLights/Workbench, and GNPS/MassIVE separately, build a dictionary of
  `accession -> (pmid, pmcid, doi)` using whatever is the best available
  resolution method *for that modality alone* (self-reported field if the
  repository has one, Europe PMC accession-search fallback if not). Each
  register can be built, refreshed, and resumed completely independently of
  the others — no cross-modality logic yet.
- **Stage B — find connections amongst the registers.** Once registers exist,
  normalize each one's identifiers to a single canonical join key (prefer
  DOI, fall back to PMCID, then PMID) and group rows across *all* registers
  by that key. Any group touching ≥2 distinct modalities is a cross-omics
  link. This stage does **no network calls at all** — it's a pure local
  join/group-by over data Stage A already collected, so it's cheap to
  re-run every time any one register is refreshed.

**This removes a step I originally had, and I confirmed empirically that it's
safe to drop:** my earlier Phase 2 ("mine the PRIDE-seeded article's full
text for companion accessions") is not actually necessary. I re-checked the
validated example — `PXD076809` resolves to PMID `42802602` from PRIDE's own
self-reported field, and `MTBLS14236` resolves to the **same** PMID `42802602`
purely from its own per-accession Europe PMC search, with no full-text
mining involved. Each modality's register entry is independently sufficient;
Stage B's join finds the connection for free. The only remaining use for
`europepmc_client.find_accessions_in_publication()` (full-text mining) is as
an optional, lower-priority *enrichment* pass later (e.g., discovering a
companion dataset that was never deposited anywhere searchable by accession
text), not a required part of this pipeline.

### 4.1 Schema (SQLite, local file — matches the project's lightweight, no-server conventions)

```sql
-- The "register" / dictionary, one row per accession, flat by design so any
-- single modality's register is just `SELECT * FROM dataset_register WHERE repository = ?`.
CREATE TABLE dataset_register (
    accession          TEXT NOT NULL,
    repository         TEXT NOT NULL,       -- 'pride' | 'metabolights' | 'metabolomics_workbench'
                                             -- | 'massive'
    pmid               TEXT,
    pmcid              TEXT,
    doi                TEXT,                -- normalized lowercase, no "https://doi.org/" prefix;
                                             -- this is the ARTICLE's doi, never the repo-minted
                                             -- dataset DOI (see the dataset-DOI trap in §3)
    resolution_source  TEXT NOT NULL,       -- 'self_reported' | 'text_mined' | 'unresolved'
    resolved_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (accession, repository)
);

CREATE INDEX idx_register_doi   ON dataset_register(doi);
CREATE INDEX idx_register_pmid  ON dataset_register(pmid);
CREATE INDEX idx_register_pmcid ON dataset_register(pmcid);
```

Stage B's "connections" are a **view**, not a stored table — it's cheap
enough to recompute on demand and never goes stale:

```sql
CREATE VIEW article_connections AS
SELECT
    COALESCE(doi, pmcid, pmid)            AS join_key,
    COUNT(DISTINCT repository)            AS modality_count,
    GROUP_CONCAT(DISTINCT repository)     AS repositories,
    GROUP_CONCAT(accession || ':' || repository) AS accessions
FROM dataset_register
WHERE resolution_source != 'unresolved'
GROUP BY join_key
HAVING modality_count >= 1;             -- filter to >= 2 downstream for cross-omics links
```

(`modality_count >= 2` rows are exactly "papers tying a proteomics dataset to
a metabolomics dataset" — the core question motivating this effort. Rows
with `modality_count = 1` are still useful: they're every dataset's resolved
publication, pre-joined and ready to reuse.)

## 5. Building each register (Stage A, one per modality)

**PRIDE register** — cheapest and highest-confidence, build first. Paginate
`/search/projects` (~422 calls for all 42,133 projects), regex the
`references` strings for `pubMed:(\d+)` / `doi:\s*(\S+)`, write one row per
project with `resolution_source='self_reported'` (or `'unresolved'` if no
reference present — still worth recording, so Stage B's join and future
re-runs can distinguish "checked, nothing found" from "not yet checked").

**MetaboLights register** — enumerate via bulk `/ws/studies` (3,543
accessions), then per study: try `extract_publication_info()` first
(`self_reported`); if empty, fall back to
`europepmc_client.find_publications_mentioning_accession(accession)`
(`text_mined`) — this is exactly what recovered `MTBLS14236`.

**Metabolomics Workbench register** — enumerate via the bulk study/project
list, then per study: fetch+regex the `mwtab/txt` dump's `PR:PUBLICATIONS`
field (`self_reported`), falling back to the same Europe PMC accession search
(`text_mined`) when that's empty or unparseable.

**GNPS/MassIVE register** — enumerate via `/api/datasets` (one call, ~13.5k
entries), but since this modality has **no self-reported publication field at
all**, every single entry's register lookup is a Europe PMC accession search
— no bulk shortcut exists here, unlike the other three. This makes it the
single costliest register to build (~13.5k Europe PMC calls vs. low
thousands for the others) for comparatively low payoff per the report's own
prioritization of GNPS as a secondary evidence layer. Recommend building this
register last, and consider sampling/subsetting it for v1 rather than a full
sweep.

## 6. Rate limiting / resumability

- PRIDE, MetaboLights, Metabolomics Workbench, and GNPS2's bulk-list calls
  are either single-shot or server-paginated and idempotent — safe to
  checkpoint by `page` number (or just accession list position) and resume
  after interruption.
- Europe PMC should be called politely (no documented hard rate limit for
  the REST API, but the existing `http_utils` backoff/retry already handles
  429s). Each register's text-mined fallback cost is bounded by how many of
  that modality's accessions lack a self-reported publication.
- The SQLite file itself doubles as the resumability checkpoint for Stage A:
  re-running any one modality's register build is just "insert or ignore /
  update" against the existing `(accession, repository)` primary key, and
  rows already marked `resolution_source != 'unresolved'` can be skipped on
  re-run. Stage B needs no checkpointing at all — it's a cheap view, safe to
  recompute from scratch every time.

## 7. Open questions before implementation

1. **Scope for v1**: full PRIDE sweep (all 42k) in one pass, or a bounded
   pilot subset (e.g., a date range or organism filter) first to validate the
   schema and spot-check link quality before committing to the full harvest?
2. **Where does the SQLite file live?** A repo-relative path (e.g.
   `data/article_dataset_index.db`, gitignored) vs. somewhere under the
   session/workspace storage — depends on whether this needs to persist
   across sessions/machines or just support one analysis run.
3. **Confidence scoring for text-mined entries**: right now `resolution_source`
   is a flat enum (`self_reported`/`text_mined`/`unresolved`). Do we want a
   numeric confidence (e.g., penalize `ST######`-style accessions, which are
   more prone to false positives in free text than `PXD`/`MTBLS`, which have
   distinctive prefixes with virtually no collision risk)?
4. **Build the GNPS/MassIVE register in v1, or defer it** given its cost is
   ~13.5k Europe PMC calls for comparatively low payoff per the report's own
   prioritization?

## 8. Suggested new modules (naming only — implementation deferred until scope above is confirmed)

- `src/python/dataset_register_db.py` — schema creation + `upsert_register_entry()`
  helper implementing the DOI/PMID/PMCID normalization from §3, plus the
  `article_connections` view from §4.1.
- `src/python/build_pride_register.py` — Stage A for PRIDE (paginated bulk
  sweep + regex extraction of `references`).
- `src/python/build_metabolights_register.py` — Stage A for MetaboLights
  (bulk accession list + per-study two-tier resolution).
- `src/python/build_workbench_register.py` — Stage A for Metabolomics
  Workbench (bulk accession list + mwtab-text two-tier resolution).
- `src/python/build_gnps_register.py` — Stage A for GNPS/MassIVE (bulk
  accession list + Europe-PMC-only resolution; lowest priority per §5).
- `find_connections()` — not a standalone module, just a thin wrapper that
  runs the `article_connections` view query against `dataset_register_db.py`'s
  SQLite file; Stage B needs no new client logic at all.

Each Stage A module is a thin orchestration layer over the clients and
`dataset_linker.py`/`europepmc_client.py` already built — no new HTTP logic
needed beyond what's in §2's confirmed bulk endpoints.

## 9. Status: implemented and harvested (2026-10-17/18)

All of §8 is implemented and tested. A full-scope harvest (all four
modalities, not the pilot scope originally floated in §7) ran to completion
against live APIs into `data/article_dataset_index.db`:

| Repository | Total accessions | Self-reported | Text-mined | Unresolved |
|---|---|---|---|---|
| PRIDE | 42,129 | 38,035 | — | 4,094 |
| MetaboLights | 3,543 | 1,686 | 849 | 1,008 |
| Metabolomics Workbench | 4,641 | 39 | 1,493 | 3,109 |
| GNPS/MassIVE | 5,376 | — | 1,626 | 3,750 |

**317 cross-modality connections found** (publications whose cited datasets
span ≥2 repositories), including one article spanning all 4 modalities at
once. `article_connections`/`query_connections()` produced these directly,
confirming §4's core design claim: no full-text companion-mining pass was
needed — each modality's independently-resolved register entry was
sufficient for Stage B's join to find the link.

**Two bugs were caught and fixed mid-harvest** (both now covered by
regression tests):
1. `pride_client.py`'s bulk-listing pagination had no resilience to a single
   malformed page response, which aborted the entire 42k-project sweep near
   its end. Fixed with page-level retry + a "stop after N consecutive
   failures" cutoff in `build_pride_register.py`, rather than propagating
   the exception.
2. GNPS2's `/api/datasets` inventory is itself a cross-repository index — it
   includes MetaboLights- and Metabolomics-Workbench-sourced entries
   alongside native MassIVE ones, not just MassIVE. `build_gnps_register.py`
   initially mislabeled all of these as `repository='massive'`; fixed by
   mapping GNPS2's own per-entry `repository` field to the correct register
   label instead of hardcoding it.

**CSV export** (`export_register_csv.py`, backed by
`dataset_register_db.export_register_to_csv()` /
`export_connections_to_csv()` / `export_connections_long_to_csv()`) writes
plain-text snapshots alongside the SQLite file for spreadsheet/pandas use
without a SQLite reader:

- `data/csv/dataset_register.csv` — full register dump, one row per
  accession (55,689 rows for the harvest above).
- `data/csv/article_connections.csv` — one row per cross-modality
  publication, repositories/accessions as delimited strings (317 rows).
- `data/csv/article_connections_long.csv` — the same connections in
  one-row-per-accession form, easier to filter/pivot (792 rows).

