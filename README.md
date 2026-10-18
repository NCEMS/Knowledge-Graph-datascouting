# Knowledge-Graph-datascouting

Data scouting and API client library supporting the **Proteomics Perturbation
Atlas Knowledge Graph** proto-proposal (see [docs/report.docx](docs/report.docx)).
This repo contains Python clients for the proteomics/metabolomics data
repositories identified in the scouting report, plus a pipeline that cross-links
dataset accessions across repositories via their associated publications.

## Contents

- [`src/python/`](src/python) — one API client per data/metadata source, plus
  shared HTTP utilities, a cross-repository register database, and harvester
  scripts.
- [`tests/`](tests) — mocked unit tests (default) and live smoke tests
  (`--run-live`) for every client and harvester.
- [`docs/plans/`](docs/plans) — implementation plans with the research/API
  verification behind each design decision.
- [`data/`](data) — the harvested register database and CSV exports (see
  [Harvested results](#harvested-results) below).

## API clients (`src/python/`)

| Client | Resource | What it does |
|---|---|---|
| [`pride_client.py`](src/python/pride_client.py) | [PRIDE Archive](https://www.ebi.ac.uk/pride/archive/) (proteomics) | Project metadata, paginated file listings, FTP download-link extraction, bulk project search (`iter_all_projects`/`fetch_projects_page`), self-reported publication (PMID/DOI) extraction |
| [`metabolights_client.py`](src/python/metabolights_client.py) | [MetaboLights](https://www.ebi.ac.uk/metabolights/) (metabolomics) | Study metadata (ISA-Tab model), file manifests, metabolite-annotation-file (MAF) table parsing, bulk study-accession listing, publication extraction |
| [`metabolomics_workbench_client.py`](src/python/metabolomics_workbench_client.py) | [Metabolomics Workbench](https://www.metabolomicsworkbench.org/) (NIH NMDR) | Study summary/factors/metabolites/analysis/data, compound and RefMet cross-reference lookups, bulk study-ID listing, legacy mwTab-text publication extraction |
| [`gnps_client.py`](src/python/gnps_client.py) | [GNPS2](https://gnps2.org/) / MassIVE (metabolomics spectra) | Cross-repository dataset inventory, per-dataset file manifests, FASST spectral library search (by USI or peak list), ReDU-curated sample metadata, structure/NPClassifier enrichment |
| [`metabolomics_hub_client.py`](src/python/metabolomics_hub_client.py) | [MetabolomicsHub](https://www.metabolomicshub.org/) | Thin stopgap client — MetabolomicsHub has no stable public query API yet (confirmed by probing); documents the blocker and defers to the three repository-specific clients above |
| [`europepmc_client.py`](src/python/europepmc_client.py) | [Europe PMC](https://europepmc.org/) | Publication lookup by PMID/PMCID/DOI, full-text accession-mention search, regex-based companion-accession mining from article full text |
| [`ncbi_client.py`](src/python/ncbi_client.py) | NCBI E-utilities (PubMed/PMC) | PubMed summaries, PMID↔PMCID, PMID↔DOI, full-text retrieval |
| [`pmc_client.py`](src/python/pmc_client.py) | PubMed Central (BioC) | Full-text JSON, supplementary file listing/download, section extraction |
| [`ols_client.py`](src/python/ols_client.py) | OLS4 (EBI Ontology Lookup Service) | Free-text term → ontology CURIE resolution |
| [`llm_client.py`](src/python/llm_client.py) | OpenAI-compatible LLM API | Prompted extraction/summarization helper used elsewhere in the pipeline |

All clients follow the same pattern: one `XClient` class, a `requests.Session`,
and `fetch_*`/`extract_*` methods built on the shared retry/backoff helper in
[`http_utils.py`](src/python/http_utils.py) (`get_json_with_retry`/`get_text_with_retry`).

## Cross-repository dataset linking

**Problem:** identify which proteomics dataset(s) and metabolomics dataset(s)
across repositories describe the same underlying study, so the knowledge
graph can connect them — even though each repository's own metadata is
inconsistently populated (e.g. MetaboLights studies frequently omit the
publication their own ISA metadata should carry).

**Approach** ([`dataset_linker.py`](src/python/dataset_linker.py),
documented in [docs/plans/article-dataset-index-harvesting-plan.md](docs/plans/article-dataset-index-harvesting-plan.md)):

1. **Per-modality registers** — for each repository, resolve every dataset
   accession to its publication identifiers (PMID/PMCID/DOI), preferring the
   repository's own self-reported field and falling back to an Europe PMC
   full-text accession search when that's missing or empty.
2. **Cross-modality join** — group all repositories' register entries by a
   canonical key (DOI, else PMCID, else PMID). Any group touching ≥2
   repositories is a cross-omics link — found entirely via a local SQL
   `GROUP BY`, no extra network calls or full-text companion-mining needed.

[`dataset_register_db.py`](src/python/dataset_register_db.py) implements the
SQLite schema, upsert/query helpers, and CSV export. Four harvester scripts
build the registers:

- [`build_pride_register.py`](src/python/build_pride_register.py) — sweeps PRIDE's bulk project-search endpoint
- [`build_metabolights_register.py`](src/python/build_metabolights_register.py) — enumerates all public MetaboLights studies
- [`build_workbench_register.py`](src/python/build_workbench_register.py) — enumerates all public Metabolomics Workbench studies
- [`build_gnps_register.py`](src/python/build_gnps_register.py) — enumerates GNPS2's dataset inventory (MassIVE/MetaboLights/Workbench-sourced entries, correctly re-routed to their native repository)

All four are idempotent/resumable (already-registered accessions are
skipped on re-run) and safe to run concurrently against the same SQLite file
(WAL journal mode).

## Harvested results

A full sweep of all four repositories has been run and is stored in
[`data/article_dataset_index.db`](data/article_dataset_index.db), with plain-text
snapshots in [`data/csv/`](data/csv):

| Repository | Accessions | Self-reported publication | Text-mined (Europe PMC) | Unresolved |
|---|---:|---:|---:|---:|
| PRIDE | 42,129 | 38,035 | — | 4,094 |
| MetaboLights | 3,543 | 1,686 | 849 | 1,008 |
| Metabolomics Workbench | 4,641 | 39 | 1,493 | 3,109 |
| GNPS/MassIVE | 5,376 | — | 1,626 | 3,750 |
| **Total** | **55,689** | | | |

**317 cross-modality connections found** — publications whose cited datasets
span ≥2 repositories (including one article spanning all 4 at once). CSV
exports ([regenerate with `export_register_csv.py`](src/python/export_register_csv.py)):

- [`data/csv/dataset_register.csv`](data/csv/dataset_register.csv) — full register, one row per accession
- [`data/csv/article_connections.csv`](data/csv/article_connections.csv) — one row per cross-modality publication
- [`data/csv/article_connections_long.csv`](data/csv/article_connections_long.csv) — the same connections, one row per linked accession (easier to filter/pivot)

## Setup

```bash
pip install -r requirements.txt
```

## Running tests

```bash
# Mocked unit tests (default; no network calls)
pytest

# Include live smoke tests against real APIs
pytest --run-live
```

## Regenerating the harvest

```bash
cd src/python
python build_pride_register.py --db ../../data/article_dataset_index.db
python build_metabolights_register.py --db ../../data/article_dataset_index.db
python build_workbench_register.py --db ../../data/article_dataset_index.db
python build_gnps_register.py --db ../../data/article_dataset_index.db
python export_register_csv.py --db ../../data/article_dataset_index.db --output-dir ../../data/csv
```

Each harvester script skips accessions already present in the database, so
re-running any of them (e.g. after an interruption, or periodically to pick
up newly published studies) only processes what's new.

## Plans

- [docs/plans/metabolomics-clients-implementation-plan.md](docs/plans/metabolomics-clients-implementation-plan.md) — research and design behind the metabolomics clients
- [docs/plans/article-dataset-index-harvesting-plan.md](docs/plans/article-dataset-index-harvesting-plan.md) — research and design behind the cross-repository linking pipeline
