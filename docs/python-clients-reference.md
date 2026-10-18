# Python Clients & Scripts Reference

Detailed reference for every module in [`src/python/`](../src/python). For a
high-level overview see the top-level [README.md](../README.md); for the
research and API verification behind each design decision see
[docs/plans/](.).

## Table of contents

- [Shared conventions](#shared-conventions)
- [Shared HTTP utilities](#shared-http-utilities)
  - [`http_utils.py`](#http_utilspy)
- [Data/metadata repository clients](#datametadata-repository-clients)
  - [`pride_client.py`](#pride_clientpy)
  - [`metabolights_client.py`](#metabolights_clientpy)
  - [`metabolomics_workbench_client.py`](#metabolomics_workbench_clientpy)
  - [`gnps_client.py`](#gnps_clientpy)
  - [`metabolomics_hub_client.py`](#metabolomics_hub_clientpy)
- [Literature & publication clients](#literature--publication-clients)
  - [`europepmc_client.py`](#europepmc_clientpy)
  - [`ncbi_client.py`](#ncbi_clientpy)
  - [`pmc_client.py`](#pmc_clientpy)
- [Ontology & LLM utilities](#ontology--llm-utilities)
  - [`ols_client.py`](#ols_clientpy)
  - [`llm_client.py`](#llm_clientpy)
- [Cross-repository dataset linking pipeline](#cross-repository-dataset-linking-pipeline)
  - [`dataset_linker.py`](#dataset_linkerpy)
  - [`dataset_register_db.py`](#dataset_register_dbpy)
  - [`build_pride_register.py`](#build_pride_registerpy)
  - [`build_metabolights_register.py`](#build_metabolights_registerpy)
  - [`build_workbench_register.py`](#build_workbench_registerpy)
  - [`build_gnps_register.py`](#build_gnps_registerpy)
  - [`export_register_csv.py`](#export_register_csvpy)

## Shared conventions

Every client in this repo follows the same shape:

- One `XClient` class per external resource.
- `__init__(self, base_url=..., timeout=...)` with sensible defaults, so
  `XClient()` works out of the box with no configuration.
- A `requests.Session()` held on `self.session`, usually with a default
  `Accept: application/json` header.
- Public methods named `fetch_*` (raw API calls returning the resource's
  native shape) and `extract_*` (derived/normalized views built from one or
  more `fetch_*` results).
- A private `_get_json()`/`_get_text()` helper wrapping the shared retry
  logic in [`http_utils.py`](#http_utilspy).
- Module-level `logger = logging.getLogger(__name__)`, with info-level
  logging around each network call and warning/error logging on
  empty/failed responses.
- No API keys required, except `ncbi_client.py` (`NCBI_EMAIL`/`NCBI_API_KEY`)
  and `llm_client.py` (`OPENROUTER_API_KEY`/`OPENAI_API_KEY`).

---

## Shared HTTP utilities

### `http_utils.py`

Shared retry/backoff GET helpers used by every client added after
`pride_client.py`/`ncbi_client.py` (those two still carry their own
pre-existing, functionally identical retry loops; new clients call these
functions instead of duplicating that logic).

#### `get_json_with_retry(session, url, params=None, headers=None, timeout=30, max_retries=5, backoff=1.5)`

GETs a URL and parses the response as JSON.

- Retries on connection/HTTP errors with exponential backoff
  (`wait = backoff ** attempt`).
- Specifically handles `429 Too Many Requests` by sleeping for the
  server-provided `Retry-After` header (default 3s) before retrying, rather
  than counting it against the retry budget's backoff schedule.
- `headers` are merged over (and can override) the session's default
  headers for that single request only — needed when, e.g., a session
  defaults to `Accept: application/json` but one specific endpoint only
  returns XML.
- Returns `None` for an empty response body (not an error).
- Raises `requests.RequestException` if every retry attempt fails.

#### `get_text_with_retry(session, url, params=None, headers=None, timeout=30, max_retries=5, backoff=1.5)`

Identical behavior to `get_json_with_retry`, but returns the raw response
body as text instead of parsing it as JSON. Used for non-JSON payloads:
TSV/mwTab downloads, XML full text, etc.

---

## Data/metadata repository clients

### `pride_client.py`

Client for the [PRIDE Archive](https://www.ebi.ac.uk/pride/archive/)
(proteomics). Has its own internal `_get_json` retry loop (predates
`http_utils.py`).

`PrideClient(base_url="https://www.ebi.ac.uk/pride/ws/archive/v3", timeout=30)`

| Method | Purpose |
|---|---|
| `fetch_project_details(pxd)` | Full metadata for one project (title, description, protocols, instruments, organisms, references, license, etc.) |
| `fetch_projects_page(page, page_size=100)` | One page of PRIDE's **bulk** `/search/projects` listing endpoint. Each summary already includes the same `references` field as `fetch_project_details()`, so sweeping every public project's publication needs only `~total_projects / page_size` calls, not one call per project. |
| `iter_all_projects(page_size=100)` | Generator that paginates `fetch_projects_page()` until an empty page, yielding one project summary at a time. |
| `fetch_project_files(pxd, page_size=1000)` | All files for a project, paginated internally until exhausted. |
| `extract_file_download_links(pxd)` | Fetches all files and extracts `{fileName, fileSizeBytes, ftpUrl}` per file (FTP URL pulled from `publicFileLocations`). Returns `{"files": [...], "totalCount": N}` or `None` on failure. |
| `extract_publication_info(project_details)` | Extracts `(doi, [pmids])` from a `fetch_project_details()` result's structured `references` list-of-dicts. Skips placeholder PMID `0`/`None`/empty. |
| `extract_publication_info_from_search_summary(project_summary)` | Same output shape as above, but parses the **different** string-based citation format used by `fetch_projects_page()`/`iter_all_projects()` (e.g. `"...e76205--pubMed:42348392--doi: 10.1002/advs.76205"`), via regex `_SEARCH_SUMMARY_REFERENCE_RE`. |

**Gotcha:** `fetch_project_details()`'s `doi` field is PRIDE's own
**dataset** DOI (e.g. `10.6019/PXD000001`), not the associated paper's DOI —
only the DOI inside `references[]` (or `extract_publication_info()`'s
return value) is the article DOI.

```python
from pride_client import PrideClient

client = PrideClient()
details = client.fetch_project_details("PXD000001")
doi, pmids = client.extract_publication_info(details)

# Bulk sweep — ~422 calls covers all ~42,000 public PRIDE projects
for project in client.iter_all_projects(page_size=100):
    doi, pmids = client.extract_publication_info_from_search_summary(project)
```

### `metabolights_client.py`

Client for [MetaboLights](https://www.ebi.ac.uk/metabolights/), EMBL-EBI's
primary metabolomics study/raw-data repository (ISA-Tab metadata model).

`MetaboLightsClient(base_url="https://www.ebi.ac.uk/metabolights/ws", timeout=30)`

| Method | Purpose |
|---|---|
| `fetch_study_details(mtbls_id)` | Full study metadata: top-level `mtblsStudy` (status, license, FTP/HTTP paths, revision info) and `isaInvestigation` (title, factors, publications, assays, samples). |
| `fetch_all_study_accessions()` | **Every** public MetaboLights study accession in one call (no server-side pagination on this endpoint) — e.g. 3,543 as of the last harvest. |
| `fetch_study_files(mtbls_id)` | File manifest: investigation/sample/assay/metabolite-annotation-file (MAF) entries, each with `file`, `type`, `status`, `createdAt`. |
| `fetch_maf_table(mtbls_id, maf_filename, study_details=None)` | Downloads and parses a MAF TSV (metabolite identifiers, formula/SMILES/InChI, organism/taxid, annotation confidence, per-sample abundance columns) into a list of row dicts. |
| `extract_file_download_links(mtbls_id, study_details=None, study_files=None)` | Combines the study's FTP/HTTP base URLs with its file manifest into `{"files": [{"fileName", "fileSizeBytes", "ftpUrl", "httpUrl", "type"}], "totalCount": N}`. |
| `extract_publication_info(study_details)` | Extracts `(doi, [pmids])` from the ISA investigation's `publications` list. |

**Known limitation:** self-reported publication coverage is inconsistent —
e.g. study `MTBLS14236` has an *empty* publications list despite being
explicitly cited in a real paper alongside PRIDE's `PXD076809`. This is why
the harvesting pipeline (below) falls back to Europe PMC text-mining.

```python
from metabolights_client import MetaboLightsClient

client = MetaboLightsClient()
details = client.fetch_study_details("MTBLS1")
files = client.fetch_study_files("MTBLS1")
maf_filename = next(f["file"] for f in files["study"] if f["type"] == "metadata_maf")
rows = client.fetch_maf_table("MTBLS1", maf_filename, study_details=details)
```

### `metabolomics_workbench_client.py`

Client for the [NIH Metabolomics Workbench](https://www.metabolomicsworkbench.org/)
(National Metabolomics Data Repository).

`MetabolomicsWorkbenchClient(base_url="https://www.metabolomicsworkbench.org/rest", timeout=30)`

A module-level `_normalize_rows(data)` helper collapses MW's three REST
response shapes — empty list `[]`, a single flat dict, or a dict keyed by
stringified integer indices (`"1"`, `"2"`, ...) — into one plain list, used
internally by every `fetch_*` method below.

| Method | Purpose |
|---|---|
| `fetch_all_study_ids()` | Every public `study_id` in one call (MW's `"ST"` wildcard input value), deduplicated and sorted — 4,641 as of the last harvest. |
| `fetch_study_mwtab_text(study_id)` | The legacy full mwTab text dump for a study. |
| `extract_publication_info_from_mwtab(mwtab_text)` | Extracts `(doi, pmid)` from the mwTab dump's free-text `PR:PUBLICATIONS` field — the *only* place MW exposes a study's publication (its clean JSON endpoints never do). Handles multi-line citations and HTML-anchor-wrapped PubMed links; only accepts DOI-shaped (`10.xxxx/...`) matches so an adjacent PubMed URL isn't mistaken for a DOI. |
| `fetch_study_summary(study_id)` | Title, species, institute, analysis type, sample count, license. |
| `fetch_study_factors(study_id)` | Per-sample factor/treatment assignments. |
| `fetch_study_metabolites(study_id)` | Named-metabolite results per analysis (`metabolite_name`, `refmet_name`). |
| `fetch_study_analysis(study_id)` | Instrumentation metadata per analysis (chromatography, MS/NMR instrument, ion mode). |
| `fetch_study_data(study_id)` | The quantitative result matrix (metabolite × sample abundance, nested under a `DATA` key per row). |
| `fetch_compound(input_item, input_value, output="all")` | Compound cross-reference lookup (PubChem CID, InChIKey, HMDB/KEGG/ChEBI IDs), independent of any specific study. |
| `resolve_refmet(name, exact=True)` | Resolves a free-text metabolite name to its standardized RefMet nomenclature and lipid classification. `exact=False` uses the fuzzy `/match/` endpoint instead of `/name/`. |

**Gotcha:** `fetch_study_summary()`/`fetch_study_factors()`/etc. (the clean
JSON endpoints) never include a publication field — you must use
`fetch_study_mwtab_text()` + `extract_publication_info_from_mwtab()` instead.

```python
from metabolomics_workbench_client import MetabolomicsWorkbenchClient

client = MetabolomicsWorkbenchClient()
mwtab = client.fetch_study_mwtab_text("ST000006")
doi, pmid = client.extract_publication_info_from_mwtab(mwtab)
compound = client.fetch_compound("pubchem_cid", "311")
```

### `gnps_client.py`

Client for [GNPS2](https://gnps2.org/) (Global Natural Products Social
Molecular Networking 2) — the practical access layer for MassIVE's
metabolomics-tagged holdings and the GNPS spectral library ecosystem. Spans
five distinct GNPS2 sub-services, each with its own base URL.

`GNPSClient(explorer_base=..., fasst_base=..., redu_base=..., structure_base=..., npclassifier_base=..., timeout=60)`

| Method | Purpose |
|---|---|
| `fetch_datasets(query=None, repository=None, domain="metabolomics")` | The **entire** cross-repository dataset inventory in one call (not server-paginated). **Important:** this inventory includes MassIVE-native (`MSV...`) datasets *and* MetaboLights/Metabolomics-Workbench-sourced entries that GNPS2 has separately indexed — check each entry's `repository` field rather than assuming everything is MassIVE. |
| `fetch_dataset_files(accession)` | Per-file manifest for one dataset (`filename`, `collection`, `size_mb`, `ms2`, `Vendor`, `Model`). |
| `search_by_usi(usi, library="gnpslibrary", **kwargs)` | FASST spectral library search by Universal Spectrum Identifier. `**kwargs`: `analog`, `cache`, `lower_delta`, `upper_delta`, `pm_tolerance`, `fragment_tolerance`, `cosine_threshold`. |
| `search_by_peaks(precursor_mz, peaks, charge=1, library="gnpslibrary", **kwargs)` | Same search, but against an explicit `[mz, intensity]` peak list instead of a USI. |
| `fetch_attribute_terms(attribute, filters=None)` | All known ReDU controlled-vocabulary values for a sample-metadata attribute (e.g. `"NCBITaxonomy"`, `"SampleType"`, `"UBERONBodyPartName"`, `"DOIDCommonName"`, `"HealthStatus"`). |
| `fetch_sample_metadata(attribute, term, filters=None)` | ReDU-curated sample metadata for every file matching an attribute/term pair — backfills organism/tissue/sample-type/health fields the bare dataset manifest lacks. `NCBITaxonomy` terms are formatted `"<taxid>|<name>"`. |
| `convert_structure(kind, smiles=None, inchi=None)` | Thin wrapper over `structure.gnps2.org`: `kind` ∈ `"convert"`, `"inchi"`, `"inchikey"`, `"mol"`, `"formula"`, `"structuremass"`. |
| `classify_structure_np(smiles)` | NPClassifier natural-product classification (`class_results`, `superclass_results`, `pathway_results`, `isglycoside`). |

```python
from gnps_client import GNPSClient

client = GNPSClient()
datasets = client.fetch_datasets(query="coral")
files = client.fetch_dataset_files(datasets[0]["accession"])
hits = client.search_by_usi("mzspec:GNPS:GNPS-LIBRARY:accession:CCMSLIB00000001547")
```

### `metabolomics_hub_client.py`

**Intentional stopgap client** for [MetabolomicsHub](https://www.metabolomicshub.org/)
— the resource the original proposal explicitly names as "Metabolomics
Hub." As of this scouting pass, MetabolomicsHub has **no stable, documented
public query API** (confirmed by probing `/api/v1/docs`, `/api/docs`,
`/api/v1/openapi.json` — all 404; its GitHub org ships per-source conversion
tooling, not a query API).

`MetabolomicsHubClient(portal_base="https://www.metabolomicshub.org", timeout=30)`

| Method | Purpose |
|---|---|
| `fetch_portal_page(path="/portal")` | Fetches raw HTML from the portal. **Not structured data** — a stopgap for manual inspection only; do not build pipeline logic parsing this HTML. |
| `search_datasets(query, **kwargs)` | **Always raises `NotImplementedError`** with guidance to use `MetaboLightsClient`/`MetabolomicsWorkbenchClient`/`GNPSClient` directly instead. |
| `fetch_dataset(accession)` | **Always raises `NotImplementedError`**, same guidance. |

Since MetabolomicsHub's own harmonization pipeline sources its content from
MetaboLights, Metabolomics Workbench, and GNPS/MassIVE, the three clients
above already give equivalent reach — revisit this client once
MetabolomicsHub publishes a versioned API.

---

## Literature & publication clients

### `europepmc_client.py`

Client for [Europe PMC](https://europepmc.org/), used to resolve the
publication that bridges dataset accessions across repositories when a
repository's own metadata doesn't self-report one. Defines module-level
`ACCESSION_PATTERNS`, a dict of repository name → regex used by
`find_accessions_in_publication()` (`pride`: `PXD\d{6,}`; `metabolights`:
`MTBLS\d+`; `metabolomics_workbench`: `ST\d{6}`; `massive`: `MSV\d{9}`;
`bioproject`: `PRJ[EDN][A-Z]\d+`; `biosample`: `SAM[EDN]?[A-Z]?\d+`).

`EuropePMCClient(base_url="https://www.ebi.ac.uk/europepmc/webservices/rest", timeout=30)`

| Method | Purpose |
|---|---|
| `search(query, result_type="core", page_size=25)` | Free-text Europe PMC search; supports quoted phrases, field queries (`"ext_id:42802602 AND src:MED"`), boolean AND/OR. `result_type="core"` includes pmid/pmcid/doi/abstract. |
| `fetch_publication(pmid=None, pmcid=None, doi=None)` | Fetches a single publication's core record by whichever identifier is supplied. |
| `find_publications_mentioning_accession(accession, page_size=25)` | Searches for publications whose text mentions a given dataset accession as an exact phrase — this is the method that recovers a publication when a repository's own self-reported field is empty. |
| `fetch_full_text_xml(pmcid)` | Fetches a PMC article's OA full text as XML (distinct from `pmc_client.py`'s NCBI BioC format). Returns `None` (with a warning, not an exception) if the article isn't open-access — this is an expected, routine outcome, not a failure. |
| `find_accessions_in_publication(pmcid, patterns=None)` | Text-mines a publication's full text for dataset accessions across all `ACCESSION_PATTERNS` repositories, surfacing companion datasets (e.g. a proteomics PXD accession's companion metabolomics MTBLS accession cited in the same paper). Returns `{repo_name: [accessions]}`, omitting repos with no matches. |

**Gotcha:** `fetch_full_text_xml()` must override the session's default
`Accept: application/json` header to `application/xml`, or the endpoint
responds `406 Not Acceptable`.

```python
from europepmc_client import EuropePMCClient

client = EuropePMCClient()
hits = client.find_publications_mentioning_accession("MTBLS14236")
pmid, pmcid, doi = hits[0]["pmid"], hits[0]["pmcid"], hits[0]["doi"]
companions = client.find_accessions_in_publication(pmcid)
# e.g. {"pride": ["PXD076809"], "bioproject": ["PRJNA1447169"]}
```

### `ncbi_client.py`

Client for NCBI E-utilities (PubMed/PubMed Central). Requires an
`NCBI_EMAIL` environment variable (or explicit `email=` argument) — raises
`ValueError` at construction time if neither is set. Has its own internal
retry loop (predates `http_utils.py`).

`NCBIClient(email=None, api_key=None, timeout=10)` — `email` falls back to
`NCBI_EMAIL`, `api_key` falls back to `NCBI_API_KEY` (optional, raises rate
limits).

| Method | Purpose |
|---|---|
| `fetch_pubmed_summary(pmid)` | Title, abstract, authors, journal, pub date, PMC ID, DOI via `esummary`. |
| `pmid_to_pmcid(pmid)` | PMID → PMCID (without the `"PMC"` prefix), via `fetch_pubmed_summary()`. |
| `fetch_pubmed_full_text(pmid)` | Attempts XML full text via `efetch`; falls back to the summary's abstract if XML parsing fails. |
| `search_pubmed(query, retmax=10)` | PubMed search returning a list of matching PMIDs (`retmax` capped at 100,000). |
| `search_by_doi(doi)` | Finds the PMID for a given DOI via a `[DOI]`-qualified search. |
| `fetch_publication_by_doi(doi)` | Convenience: `search_by_doi()` → `fetch_pubmed_full_text()` in one call. |
| `get_publication_metadata(pmid)` | Comprehensive metadata dict combining `fetch_pubmed_summary()`'s fields with a `fetched_at` timestamp. |

```python
from ncbi_client import NCBIClient

client = NCBIClient(email="you@example.org")
summary = client.fetch_pubmed_summary("17190852")
pmcid = client.pmid_to_pmcid("17190852")
```

### `pmc_client.py`

Client for PubMed Central's BioC-format full-text API (distinct from
`europepmc_client.py`'s XML full-text endpoint — different format, different
upstream service).

`PMCClient(email=None, api_key=None, timeout=30)`

| Method | Purpose |
|---|---|
| `pmid_to_pmcid(pmid)` | PMID → PMCID via NCBI E-Utilities `elink.fcgi` (a different underlying mechanism than `ncbi_client.py`'s version of the same conversion). |
| `fetch_full_text(pmcid)` | Full text as BioC JSON. |
| `fetch_supplementary_files(pmcid)` | List of supplementary file metadata. |
| `download_supplementary_file(bioc_url, output_path)` | Downloads a supplementary file via `wget` subprocess; removes and returns `False` if the downloaded file is empty. |
| `extract_text_sections(bioc_json)` | Extracts `title`/`abstract`/`results`/`methods` text sections from a BioC JSON full-text response. |

---

## Ontology & LLM utilities

### `ols_client.py`

Module-level functions (no class) for resolving free-text terms to ontology
CURIEs via EBI's [OLS4](https://www.ebi.ac.uk/ols4/) (Ontology Lookup
Service). Results are cached in a module-level in-process dict
(`_CACHE`), keyed by `(term_lower, ontology_ids_tuple)`, to avoid redundant
HTTP calls within a single pipeline run. Uses the standard library's
`urllib` directly (no `requests`/`http_utils.py` dependency).

| Function | Purpose |
|---|---|
| `lookup(term, ontology_ids, exact=False)` | Looks up `term` against each ontology in `ontology_ids` in order, returning the first `(label, curie)` hit (e.g. `("Homo sapiens", "NCBITaxon:9606")`), or `None`. |

```python
import ols_client
result = ols_client.lookup("Homo sapiens", ["ncbitaxon"])
```

### `llm_client.py`

Thin wrapper over the OpenAI Python SDK for prompted extraction/
summarization, routed through [OpenRouter](https://openrouter.ai) by default.

`LLMClient(api_key=None, model="gpt-4o-mini-2024-07-18", temperature=0.3, timeout=300, base_url=None)`

Resolution order when `api_key`/`base_url` aren't passed explicitly:
1. `OPENROUTER_API_KEY` set → routes through OpenRouter (model must be an
   OpenRouter slug, e.g. `"openai/gpt-4o-mini"`).
2. `OPENAI_API_KEY` set → calls OpenAI directly.
3. Neither set → raises `ValueError`.

| Method | Purpose |
|---|---|
| `query(prompt, text, system_role="assistant")` | Single system-prompt + user-text chat completion; returns the response text or `None` on failure. |
| `batch_query(prompts, text)` | Runs multiple named prompts against the same `text`, returning `{prompt_key: response}`. |
| `parse_json_response(response)` | Attempts to parse JSON from a response, including when it's wrapped in a ` ```json ` or plain ` ``` ` markdown code block. |
| `cache_response(responses_dict, prompt_key, response, metadata=None)` | Formats a response (with timestamp, model, optional parsed JSON, optional metadata) into a caching dict structure. |

---

## Cross-repository dataset linking pipeline

This set of modules implements the two-stage design documented in
[docs/plans/article-dataset-index-harvesting-plan.md](article-dataset-index-harvesting-plan.md):
**Stage A** resolves each dataset accession's publication identifiers
independently per repository ("registers"); **Stage B** finds cross-modality
connections via a pure local join — no extra network calls.

### `dataset_linker.py`

Implements the **per-accession** resolution hierarchy (used ad hoc; the
bulk harvesters below reimplement the same logic inline against the
register database rather than calling this module directly, since they
also need the resumability/skip-if-already-resolved behavior).

**`PublicationIdentity(pmid=None, pmcid=None, doi=None, source="unresolved")`**
— a resolved (or unresolved) publication identity.
- `.is_known()` → `True` if any of pmid/pmcid/doi is set.
- `.to_dict()` → `{"pmid", "pmcid", "doi", "source"}`.
- `source` ∈ `"self_reported"` | `"text_mined"` | `"unresolved"`.

**`DatasetLink(accession, repository, publication, companion_accessions=None)`**
— the result of resolving one dataset's publication and companion
accessions. `.to_dict()` → `{"accession", "repository", "publication", "companion_accessions"}`.

**`DatasetLinker(europepmc_client=None)`**

| Method | Purpose |
|---|---|
| `resolve_publication(accession, repository, self_reported_doi=None, self_reported_pmids=None)` | Prefers the caller-supplied self-reported DOI/PMIDs; if both are empty, falls back to `EuropePMCClient.find_publications_mentioning_accession()`. Returns a `PublicationIdentity` tagged with how it was resolved. |
| `find_companion_datasets(publication)` | Given a `PublicationIdentity`, text-mines its full text (resolving a PMCID from PMID first if needed) for companion accessions across repositories. |
| `link(accession, repository, self_reported_doi=None, self_reported_pmids=None)` | Full pipeline: `resolve_publication()` then `find_companion_datasets()`, with the input dataset's own accession excluded from its own companion-accession results. Returns a `DatasetLink`. |

```python
from dataset_linker import DatasetLinker
from pride_client import PrideClient

pride = PrideClient()
details = pride.fetch_project_details("PXD076809")
doi, pmids = pride.extract_publication_info(details)

linker = DatasetLinker()
link = linker.link("PXD076809", "pride", self_reported_doi=doi, self_reported_pmids=pmids)
print(link.publication.source)        # "self_reported"
print(link.companion_accessions)      # {"metabolights": ["MTBLS14236"], ...}
```

### `dataset_register_db.py`

SQLite-backed register: one row per `(accession, repository)` resolved to
its publication identifiers, plus a view that groups register rows across
all repositories by a canonical join key to surface cross-modality links.

**Schema:**

```sql
CREATE TABLE dataset_register (
    accession          TEXT NOT NULL,
    repository         TEXT NOT NULL,   -- 'pride' | 'metabolights' | 'metabolomics_workbench' | 'massive'
    pmid               TEXT,
    pmcid              TEXT,
    doi                TEXT,            -- normalized (lowercase, no URL prefix); the ARTICLE's DOI
    resolution_source  TEXT NOT NULL,   -- 'self_reported' | 'text_mined' | 'unresolved'
    resolved_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (accession, repository)
);

CREATE VIEW article_connections AS
SELECT COALESCE(doi, pmcid, pmid) AS join_key,
       COUNT(DISTINCT repository) AS modality_count,
       GROUP_CONCAT(DISTINCT repository) AS repositories,
       GROUP_CONCAT(accession || ':' || repository) AS accessions
FROM dataset_register
WHERE resolution_source != 'unresolved' AND COALESCE(doi, pmcid, pmid) IS NOT NULL
GROUP BY join_key;
```

| Function | Purpose |
|---|---|
| `normalize_doi(doi)` | Strips a `doi.org`/`dx.doi.org` URL prefix and lowercases, so the same article's DOI matches regardless of which source supplied it. |
| `get_connection(db_path)` | Opens (creating if needed) the SQLite file with **WAL journal mode** and a 30s busy timeout, and applies the schema. WAL mode is what makes it safe for the four harvester scripts below to run **concurrently** against the same file (each only ever writes rows for its own `repository` value, but SQLite still serializes writers at the file level without WAL). |
| `is_resolved(conn, accession, repository)` | `True` if a register row already exists — this is what makes every harvester idempotent/resumable. |
| `upsert_register_entry(conn, accession, repository, pmid=None, pmcid=None, doi=None, resolution_source="unresolved")` | Insert-or-update a single register row (`doi` is normalized automatically). |
| `register_stats(conn)` | Per-repository summary: `total`, `self_reported`, `text_mined`, `unresolved` counts. |
| `query_connections(conn, min_modalities=2)` | Queries `article_connections` for publications linking ≥ `min_modalities` distinct repositories. `repositories` is always returned alphabetically sorted/deduplicated (SQL `GROUP_CONCAT(DISTINCT ...)` doesn't guarantee row order, so the same repository combination would otherwise sometimes serialize as `"pride,metabolights"` and sometimes `"metabolights,pride"`). |
| `export_register_to_csv(conn, csv_path)` | Dumps the full register table to CSV, one row per accession. Returns row count. |
| `export_connections_to_csv(conn, csv_path, min_modalities=2)` | Dumps `query_connections()` to CSV — one row per publication, with `repositories`/`accessions` as delimited strings. Returns row count. |
| `export_connections_long_to_csv(conn, csv_path, min_modalities=2)` | Same data as above, but "long" format — one row per `(join_key, accession, repository)` triple, easier to filter/pivot in a spreadsheet or pandas. Returns accession-level row count (not publication-level). |

```python
from dataset_register_db import get_connection, upsert_register_entry, query_connections

conn = get_connection("data/article_dataset_index.db")
upsert_register_entry(conn, "PXD076809", "pride", pmid="42802602",
                       doi="10.1080/21505594.2026.2739013", resolution_source="self_reported")
connections = query_connections(conn, min_modalities=2)  # cross-modality links
```

### `build_pride_register.py`

Stage A harvester for PRIDE. Sweeps `pride_client.fetch_projects_page()`
(not `iter_all_projects()` — the harvester manages pagination itself to add
page-level failure resilience) and upserts each project's self-reported
publication (or `"unresolved"` if it has none) into the register.

**Resilience:** a single malformed page response is retried up to
`MAX_PAGE_RETRIES=3` times (waiting `PAGE_RETRY_WAIT_SECONDS=5` between
attempts) before being logged and skipped; the harvester only stops entirely
after `max_consecutive_page_failures=3` **consecutive** page failures
(treated as "ran off the end of pagination"), rather than letting one bad
page abort the whole multi-thousand-page sweep. A later re-run will revisit
any skipped pages since it always starts from page 0 and skips only
already-registered accessions.

```bash
python build_pride_register.py --db data/article_dataset_index.db [--page-size 100] [--limit N]
```

### `build_metabolights_register.py`

Stage A harvester for MetaboLights. Enumerates
`metabolights_client.fetch_all_study_accessions()`, then for each: tries
`extract_publication_info()` (self-reported) first, falling back to
`EuropePMCClient.find_publications_mentioning_accession()` (text-mined) when
the study's own ISA metadata has none.

```bash
python build_metabolights_register.py --db data/article_dataset_index.db [--delay 0.2] [--limit N]
```

### `build_workbench_register.py`

Stage A harvester for Metabolomics Workbench. Enumerates
`metabolomics_workbench_client.fetch_all_study_ids()`, then for each: parses
the mwTab text dump's `PR:PUBLICATIONS` field (self-reported) first, falling
back to Europe PMC text-mining when empty/unparseable.

```bash
python build_workbench_register.py --db data/article_dataset_index.db [--delay 0.2] [--limit N]
```

### `build_gnps_register.py`

Stage A harvester for GNPS/MassIVE. Enumerates
`gnps_client.fetch_datasets()` and resolves every entry **purely** via
Europe PMC accession search — GNPS2 has no self-reported publication field
at all, so this is the only harvester with no self-reported tier, and the
costliest to run (~13.5k Europe PMC calls).

**Important:** GNPS2's inventory is itself a cross-repository index — it
includes not just native MassIVE (`MSV...`) datasets but also
MetaboLights- and Metabolomics-Workbench-sourced entries it has separately
indexed. `REPOSITORY_LABELS` maps GNPS2's own per-entry `repository` field
(`"MassIVE"` → `"massive"`, `"MetaboLights"` → `"metabolights"`,
`"Workbench"` → `"metabolomics_workbench"`) to the correct register label,
so MetaboLights/Workbench accessions already resolved by their dedicated
harvesters are correctly recognized as already-done here (via `is_resolved()`)
instead of being reprocessed — and so they aren't mislabeled as `"massive"`.

```bash
python build_gnps_register.py --db data/article_dataset_index.db [--delay 0.2] [--limit N]
```

### `export_register_csv.py`

CLI wrapper around `dataset_register_db.py`'s three CSV export functions.
`export_all(db_path, output_dir, min_modalities=2)` writes all three files
into `output_dir` (created if missing):

- `dataset_register.csv`
- `article_connections.csv`
- `article_connections_long.csv`

```bash
python export_register_csv.py --db data/article_dataset_index.db --output-dir data/csv [--min-modalities 2]
```

---

## Running the full harvest

All four `build_*_register.py` scripts are idempotent (safe to re-run —
already-registered accessions are skipped) and safe to run **concurrently**
against the same SQLite file (WAL journal mode in `get_connection()`):

```bash
cd src/python
python build_pride_register.py --db ../../data/article_dataset_index.db &
python build_metabolights_register.py --db ../../data/article_dataset_index.db &
python build_workbench_register.py --db ../../data/article_dataset_index.db &
python build_gnps_register.py --db ../../data/article_dataset_index.db &
wait
python export_register_csv.py --db ../../data/article_dataset_index.db --output-dir ../../data/csv
```
