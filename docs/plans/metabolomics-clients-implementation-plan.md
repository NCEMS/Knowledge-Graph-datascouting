# Implementation Plan: Metabolomics Data/Metadata Clients

## 1. Background

### 1.1 What already exists (`src/python/`)

| Client | Resource | Role |
|---|---|---|
| [pride_client.py](/storage/group/epo2/default/ims86/git_repos/Knowledge-Graph-datascouting/src/python/pride_client.py) | PRIDE Archive (proteomics) | Project metadata, paginated file listing, FTP download-link extraction, DOI/PMID extraction |
| [ncbi_client.py](/storage/group/epo2/default/ims86/git_repos/Knowledge-Graph-datascouting/src/python/ncbi_client.py) | NCBI E-utilities (PubMed/PMC) | PubMed summaries, PMID↔PMCID, PMID↔DOI, full-text retrieval |
| [pmc_client.py](/storage/group/epo2/default/ims86/git_repos/Knowledge-Graph-datascouting/src/python/pmc_client.py) | PubMed Central BioC | Full-text JSON, supplementary file listing/download, section extraction |
| [ols_client.py](/storage/group/epo2/default/ims86/git_repos/Knowledge-Graph-datascouting/src/python/ols_client.py) | OLS4 (EBI Ontology Lookup Service) | Free-text term → ontology CURIE resolution, module-level cache |
| [llm_client.py](/storage/group/epo2/default/ims86/git_repos/Knowledge-Graph-datascouting/src/python/llm_client.py) | OpenAI-compatible LLM API | Prompted extraction/summarization helper, JSON parsing, response caching |

**Shared conventions** across clients (to be preserved in new clients):
- One `XClient` class per resource, `__init__(self, base_url=..., timeout=...)`, `requests.Session()` with an `Accept: application/json` header.
- A private `_get_json()` (or `_get_xml()`) helper implementing exponential-backoff retry and `429`/`Retry-After` handling, used by all public methods.
- Public methods named `fetch_*` (raw API calls) and `extract_*` (derived/normalized views, e.g. `extract_file_download_links`, `extract_publication_info`).
- Module-level `logger = logging.getLogger(__name__)`, info/warning/error logging around each network call.
- No shared base class today — each client reimplements its own retry loop. See §5 (recommended refactor).
- No `requirements.txt`, no `tests/` directory, no orchestrator script yet exist in the repo; these clients are currently a library of independent building blocks.

### 1.2 Report reviewed

[docs/report.docx](/storage/group/epo2/default/ims86/git_repos/Knowledge-Graph-datascouting/docs/report.docx) ("Data Scouting a working group proposal" — Proteomics Perturbation Atlas Knowledge Graph) section 2, "Candidate Data Sets," names the following metabolomics-relevant resources:

| Resource | Report's stated role | Client exists? |
|---|---|---|
| **MetabolomicsHub** | "Direct match to requested 'Metabolomics Hub'" — harmonized metabolomics metadata/search/API across repositories | ❌ Missing |
| **MetaboLights** | Primary metabolomics study/raw-data repository; ISA-style metadata, sample characteristics, factors, metabolite annotations | ❌ Missing |
| **Metabolomics Workbench (NIH NMDR)** | Study/project, subjects/samples, factors, chromatography/MS methods, named-metabolite data, raw files | ❌ Missing |
| **GNPS / GNPS2** | Public metabolomics spectra, molecular networking, spectral library; also the de facto access layer for MassIVE's metabolomics holdings | ❌ Missing |
| OmicsDI | Cross-omics aggregator/bridge (proteomics+metabolomics+transcriptomics); "aggregator rather than authoritative raw-data host" | ❌ Missing (lower priority — see §4) |
| MassIVE | Proteomics-oriented, but metabolomics content is explicitly accessed "through GNPS/MassIVE" tooling per the report | Covered via GNPS2 client, no separate client planned |
| ProteomeXchange, PRIDE, PeptideAtlas, NCI PDC, UniProt, NCBI BioSample, Biolink, LINCS/Panorama, OBO Foundry | Proteomics/ontology/reference resources | Out of scope for this plan (proteomics side already has `pride_client.py`/`ncbi_client.py`/`ols_client.py`, or are non-API reference resources) |

**Conclusion:** four metabolomics-specific clients are missing and map directly onto the report's "Metabolomics result" information class (compound/metabolite identifier, precursor/product ions, spectrum, abundance/change, annotation confidence) and its biological-context/experimental-design/governance classes. This plan covers those four, plus an optional fifth (OmicsDI) as a stretch goal.

## 2. APIs verified during scouting (live, working as of this session)

| Resource | Base URL | Confirmed endpoint(s) |
|---|---|---|
| MetaboLights | `https://www.ebi.ac.uk/metabolights/ws` | `GET /studies/{accession}` (study metadata JSON), `GET /studies/{accession}/files` (ISA-Tab file manifest: investigation/sample/assay/MAF files + status) |
| Metabolomics Workbench | `https://www.metabolomicsworkbench.org/rest` | `GET /study/study_id/{ST id}/summary`, `/factors`, `/metabolites`, `/data`, `/analysis`; `GET /compound/{item}/{value}/{output}`; `GET /refmet/...` |
| GNPS2 | `https://explorer.gnps2.org`, `https://fasst.gnps2.org`, `https://redu.gnps2.org`, `https://structure.gnps2.org` | `GET /api/datasets` (paginated cross-repo metabolomics dataset inventory incl. MassIVE/GNPS), `GET /api/datasets/{accession}/files` (per-file manifest w/ size, collection type), `GET /search?usi=...` (FASST spectral search by USI or peak list), `GET /attribute/{attr}/attributeterms` and `.../attributeterm/{term}/files` (ReDU curated sample metadata) |
| MetabolomicsHub | `https://www.metabolomicshub.org` | Public portal only (`/portal`); **no stable, documented public REST API found** — confirmed by probing `/api/v1/*`, `/api/docs`, `/api/v1/openapi.json` (all 404) and by the organization's own GitHub (`MetabolomicsHub/mhd-model`, `mtbls-mhd-integration`, `mw-mhd-integration`, `gnps-mhd-integration`), which show the integration is currently done via per-source Python CLI tools converting into the shared "MHD model," not a finished query API |

This matches the report's own caution: *"its UI and data model are explicitly not final and may change. Pin snapshots/schema versions."* [8]

## 3. Proposed new clients

All four follow the existing `XClient(base_url, timeout)` + `requests.Session` + `_get_json` retry-loop pattern (modeled closely on `pride_client.py`), with `fetch_*` / `extract_*` method naming.

### 3.1 `metabolights_client.py` — Priority 1

Purpose: primary metabolomics study/raw-data repository (ISA-Tab metadata model).

- `MetaboLightsClient(base_url="https://www.ebi.ac.uk/metabolights/ws", timeout=30)`
- `fetch_study_details(mtbls_id) -> dict` — `GET /studies/{accession}`; returns status, organism/sample summary, factors, publication links, license.
- `fetch_study_files(mtbls_id) -> dict` — `GET /studies/{accession}/files`; returns investigation/sample/assay/MAF manifest entries (`type`, `file`, `status`, timestamps).
- `fetch_maf_table(mtbls_id, assay_filename) -> List[dict]` — download + parse the metabolite-annotation-file TSV (`m_*_maf.tsv`) referenced in the files manifest for compound IDs (InChI, formula, database cross-refs) and raw/derived abundance columns.
- `extract_file_download_links(mtbls_id) -> Optional[dict]` — builds FTP/HTTP URLs from `studyFtpUrl`/`studyHttpUrl` + file manifest, mirroring `pride_client.extract_file_download_links` output shape (`fileName`, `fileSizeBytes` if available, `ftpUrl`).
- `extract_publication_info(study_details) -> Tuple[doi, [pmids]]` — mirrors `pride_client.extract_publication_info`.
- Maps to report fields: Dataset identity (accession/title/release date/submitter), Biological context (organism, sample characteristics), Experimental design (factors), Metabolomics result (MAF compound/annotation rows), Governance (per-study license field — MetaboLights studies carry individual licenses, do not assume one blanket license).

### 3.2 `metabolomics_workbench_client.py` — Priority 1

Purpose: NIH National Metabolomics Data Repository; complements MetaboLights with a differently structured (mwTab) metadata model.

- `MetabolomicsWorkbenchClient(base_url="https://www.metabolomicsworkbench.org/rest", timeout=30)`
- `fetch_study_summary(study_id) -> dict` — `GET /study/study_id/{id}/summary` (title, species, institute, analysis_type, n_samples, license).
- `fetch_study_factors(study_id) -> List[dict]` — `GET /study/study_id/{id}/factors` (per-sample factor/treatment table).
- `fetch_study_metabolites(study_id) -> List[dict]` — `GET /study/study_id/{id}/metabolites` (named-metabolite × analysis rows, RefMet-normalized names).
- `fetch_study_data(study_id) -> Any` — `GET /study/study_id/{id}/data` (quantitative result matrix, mwTab JSON).
- `fetch_compound(item, value, output="all") -> dict` — `GET /compound/{item}/{value}/{output}` for compound cross-reference normalization (PubChem CID, InChIKey, HMDB/KEGG/ChEBI IDs) — used to enrich metabolite identifiers, independent of a specific study.
- `resolve_refmet(name) -> dict` — `GET /refmet/name/{name}` / `GET /refmet/match/{name}` for standardized metabolite nomenclature (parallels `ols_client.lookup` for ontology terms, but RefMet-specific).
- Maps to report fields: Dataset identity, Experimental design (factors), Metabolomics result (named-metabolite + RefMet + compound cross-refs), Governance (`license`/`license_url` is present per-study in the summary payload — capture verbatim).

### 3.3 `gnps_client.py` (GNPS2) — Priority 1

Purpose: spectral evidence layer; also the practical access point for MassIVE's metabolomics-tagged datasets, per the report's own framing ("GNPS tooling can work across GNPS/MassIVE... files").

- `GNPSClient(explorer_base="https://explorer.gnps2.org/api", fasst_base="https://fasst.gnps2.org", redu_base="https://redu.gnps2.org", structure_base="https://structure.gnps2.org", timeout=30)`
- `fetch_datasets(page=0, page_size=..., metabolomics_only=True) -> List[dict]` — `GET /api/datasets`; paginate over the explorer's cross-repository inventory (`accession`, `repository` [MassIVE/GNPS/MetaboLights/Workbench], `species`, `instrument`, `title`, `keywords`), filtered to `metabolomics: true` entries to avoid double-counting pure-proteomics MassIVE sets already reachable via a future proteomics MassIVE path.
- `fetch_dataset_files(accession) -> List[dict]` — `GET /api/datasets/{accession}/files` (per-file `filename`, `collection`, `size_mb`).
- `search_by_usi(usi, library="gnpslibrary", **kwargs) -> dict` — wraps `GET fasst.gnps2.org/search?usi=...` for spectral library hits (cosine, analog search params from report: `pm_tolerance`, `fragment_tolerance`, `cosine_threshold`).
- `search_by_peaks(precursor_mz, peaks, charge=1, library="gnpslibrary") -> dict` — same endpoint, `query_spectrum` JSON payload variant.
- `fetch_sample_metadata(attribute, term) -> List[dict]` — `GET redu.gnps2.org/attribute/{attribute}/attributeterm/{term}/files`; used to backfill organism/tissue/sample-type fields that the bare GNPS2 explorer listing lacks (ReDU-curated `ATTRIBUTE_*` columns), directly addressing the report's noted biological-context gap for spectral repositories.
- `resolve_structure(smiles=None, inchi=None, kind="classyfire"|"formula"|"structuremass"|...) -> dict` — thin wrapper over `structure.gnps2.org` endpoints for compound-identity enrichment.
- Maps to report fields: Dataset identity, Biological context (via ReDU), Metabolomics result (spectra/compound/USI evidence), File/evidence (USI, file manifests).

### 3.4 `metabolomics_hub_client.py` — Priority 2 (unstable upstream)

Purpose: directly satisfies the proposal's explicit "Metabolomics Hub" request, but the upstream has **no finished public query API** today (see §2). Scope the client narrowly and defensively:

- `MetabolomicsHubClient(portal_base="https://www.metabolomicshub.org", timeout=30)`
- `fetch_portal_page(path="/portal") -> str` — thin fetch, documented as a stopgap (the portal is a client-rendered Next.js app; no stable JSON endpoint was discoverable by probing `/api/v1/*`, `/api/docs`, `/api/v1/openapi.json`).
- `TODO`/raise `NotImplementedError` placeholders for `search_datasets()` and `fetch_dataset()`, with a docstring pointing to `MetabolomicsHub/mhd-model` (MHD schema) and the per-source integration repos (`mtbls-mhd-integration`, `mw-mhd-integration`, `gnps-mhd-integration`) as the actual mechanism once the hub API stabilizes.
- Recommended interim strategy: **do not block on this client.** Since MetabolomicsHub's own integration pipeline sources its content from MetaboLights, Metabolomics Workbench, and GNPS/MassIVE, the three Priority-1 clients above already give pipeline code equivalent reach. Revisit this client once MetabolomicsHub publishes a versioned API (re-check `/api/v1/openapi.json` and the GitHub org periodically), or add a `pin_schema_version` parameter once one exists, per the report's explicit recommendation to "pin snapshots/schema versions." [8]

## 4. Optional stretch: `omicsdi_client.py` — Priority 3

Not a metabolomics-only resource (cross-omics aggregator), but the report calls it "a useful pre-built bridge among proteomics, metabolomics, transcriptomics and publications" with documented filters for MetaboLights/GNPS/Metabolomics Workbench. Worth a minimal client (`fetch_dataset(accession)`, `search(query, domain="metabolomics")`) once the four core clients are done and verified, to give cross-repository entity-resolution hints for the knowledge graph. Not required to satisfy the report's explicit "Metabolomics Hub" ask, so it is sequenced last.

## 5. Recommended shared refactor (do first, small, de-risks the rest)

`pride_client.py` and `ncbi_client.py` each hand-roll an almost identical retry/backoff `_get_json`. Before adding four more near-identical loops, extract a small shared helper, e.g. `src/python/http_utils.py`:

```python
def get_json_with_retry(session, url, params=None, timeout=30, max_retries=5, backoff=1.5) -> Optional[Any]:
    ...
```

Each new client's `_get_json` becomes a one-line wrapper calling this, keeping per-client classes thin while avoiding four more copies of the same retry logic. This is a non-breaking addition — existing clients are not required to migrate as part of this plan, though they could optionally adopt it later.

## 6. Cross-cutting requirements for every new client

- **Rate limiting / retries**: reuse the `_get_json` (backoff + `429`/`Retry-After`) pattern from `pride_client.py`.
- **Pagination**: GNPS2 `/api/datasets` and MetaboLights file listings must be fully paginated/collected the same way `pride_client.fetch_project_files` loops pages until an empty response.
- **License/governance capture**: every `fetch_study_*`/`fetch_dataset*` method must surface whatever per-dataset license field the source provides (MetaboLights: study-level license; Metabolomics Workbench: `license`/`license_url`; GNPS2: dataset-level reuse varies) rather than assuming a blanket license — this directly follows the report's governance warnings. [7,8]
- **Identifier normalization**: every client should expose the resource's stable accession (MTBLS…, ST…, MSV…/GNPS accessions) plus any cross-reference IDs (PubChem CID, InChIKey, HMDB/KEGG/ChEBI, DOI/PMID) in a flat, predictable shape so a future graph-loading step can join across repositories without per-resource special-casing.
- **No secrets required**: none of the four APIs need an API key (unlike `ncbi_client.py`'s `NCBI_EMAIL`/`NCBI_API_KEY`), simplifying config.
- **Logging**: module-level `logger`, info-level per request, warning on empty/missing data, error with context on failure — consistent with existing clients.

## 7. Testing plan

The repo currently has no `tests/` directory or test runner configured. For each new client:
- Add `tests/test_metabolights_client.py`, `tests/test_metabolomics_workbench_client.py`, `tests/test_gnps_client.py` (and a thin one for the MHD stopgap) using `pytest` + `responses` (or `unittest.mock.patch("requests.Session.get")`) to mock HTTP responses — no live network calls in CI.
- At least one test per public method covering: happy path parsing, empty/paginated-to-empty termination, and a simulated `429` retry.
- A separate, explicitly-marked `@pytest.mark.live` smoke test per client hitting the real endpoints confirmed in §2 (e.g., MetaboLights `MTBLS1`, Metabolomics Workbench `ST000001`, GNPS2 `MSV000086989`) — skipped by default, runnable manually to catch upstream breaking changes.
- Add `requirements.txt` (currently absent) with `requests`, `pytest`, `responses`.

## 8. Suggested build order

1. `http_utils.py` shared retry helper (§5) — small, de-risks everything else.
2. `metabolights_client.py` — richest metadata model, closest analog to `pride_client.py`.
3. `metabolomics_workbench_client.py` — second primary repository, reuses helper.
4. `gnps_client.py` — spectral/USI layer, slightly more endpoints (explorer + fasst + redu + structure) but no auth complexity.
5. `metabolomics_hub_client.py` — thin stopgap client + explicit TODOs, documented blocker.
6. (Optional) `omicsdi_client.py` — only after 2–4 are verified against live data.

Each step should land as its own module with its own tests before moving to the next, matching the granularity of the existing one-client-per-file layout.

## 9. Status: implemented (2026-10-17)

Steps 1–5 above are implemented and tested (`http_utils.py`, `metabolights_client.py`,
`metabolomics_workbench_client.py`, `gnps_client.py`, `metabolomics_hub_client.py`),
with a `tests/` suite (`pytest` + `responses`, mocked by default, `--run-live` for
real-endpoint smoke tests) and a `requirements.txt`. `omicsdi_client.py` remains
unimplemented (optional, §4).

## 10. Addendum: cross-repository entity-resolution identifier

A follow-up question once these clients existed: what identifier best links a
proteomics dataset (PRIDE/PXD) to a companion metabolomics dataset (MetaboLights/
Metabolomics Workbench/GNPS) studying the same biology — the actual cross-omics
linking the Proteomics Perturbation Atlas KG needs?

**Empirical finding:** repository-self-reported publication identifiers
(PMID/PMCID/DOI) are the most broadly useful bridge, but are inconsistently
populated — confirmed by a live, present-day example. A 2026 multi-omics paper
(PMID `42802602`, PMCID `PMC13625717`) explicitly cites both `PXD076809`
(PRIDE) and `MTBLS14236` (MetaboLights). PRIDE's own API correctly reports the
paper's PMID/DOI on `PXD076809`. MetaboLights' own API reports **no**
publication at all for `MTBLS14236`, despite the paper citing it. Relying
solely on each repository's self-reported field would miss this real link.

**Resolution implemented:** `europepmc_client.py` + `dataset_linker.py`
operationalize a three-tier hierarchy:
1. Prefer the repository's own self-reported PMID/PMCID/DOI when present.
2. If absent, recover the publication by searching Europe PMC's full-text
   index for the dataset's own accession as an exact phrase (this is what
   recovers `MTBLS14236`'s publication).
3. Once a publication is known, text-mine its full text (Europe PMC's
   `fullTextXML`) for companion accessions across repositories (PXD, MTBLS,
   ST, MSV, BioProject, BioSample patterns in `ACCESSION_PATTERNS`), to
   surface the actual cross-omics dataset pairing.

The publication is treated as a **bridging entity/edge** between dataset
nodes, not as a dataset's primary key — each dataset keeps its own native
accession as its identity, plus any repository-minted DOI (e.g. PRIDE's
`10.6019/PXD...`, Metabolomics Workbench's `10.21228/M...`) as a secondary
citation ID. NCBI BioSample/BioProject accessions are captured opportunistically
as a secondary, higher-precision "same physical sample" signal, consistent
with the main report's observation that this linkage is inconsistently present.

Verified live (`tests/test_live_smoke.py::test_dataset_linker_live_bidirectional_resolution`):
resolving from the PRIDE side (`self_reported`) and from the MetaboLights side
(`text_mined`) converge on the same publication and the same companion-dataset
link in both directions.
