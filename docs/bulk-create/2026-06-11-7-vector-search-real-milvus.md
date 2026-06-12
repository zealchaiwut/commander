# vector-search-demo — wire real Milvus backend

**Date:** 2026-06-11
**Project:** zealchaiwut/vector-search-demo
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

From the sprint-3.1 readiness check: the Milvus plumbing exists (docker-compose
stack, `@zilliz/milvus2-sdk-node` dependency, `src/milvus/schema.ts` with the
HNSW/COSINE collection definition, `ping` command) but the entire data path is
still the file-backed mock — `src/data/collection.js` persists to
`collection.json`, `src/data/embedder.js` produces TF-IDF vectors with
vocab-sized dims (incompatible with the schema's fixed 384-dim FloatVector),
and `src/core/search.js` ranks by TF-IDF cosine over the JSON file. The real
384-dim embedder (`src/embeddings/index.js`, Xenova MiniLM) exists but nothing
imports it. The env-var mismatch (`MILVUS_ADDRESS` vs `MILVUS_HOST`/`MILVUS_PORT`)
was already fixed inline on develop.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Wire the init and ingest commands to the real Milvus collection. Replace the file-backed collection in src/data/collection.js with the Milvus SDK: the init command must call createCollection from src/milvus/schema.ts (which already defines the documents collection with an HNSW/COSINE index on the 384-dim embedding field) instead of writing collection.json, and the ingest command must upsert chunk rows into that Milvus collection instead of the JSON file. Note that runIngest and runInit are currently synchronous and the Milvus SDK is async, so the command entry points need to become async and the CLI must await them. Keep the attachments directory behavior unchanged. Acceptance: with the docker-compose Milvus stack running, "init" creates and loads the documents collection in Milvus (verifiable via hasCollection), "ingest" reports the same docs/chunks count as today, and the entity count in Milvus matches the chunk count; collection.json is no longer written.
---
Replace the TF-IDF embedder in the ingest path with the real MiniLM embedder. src/data/embedder.js currently builds TF-IDF vectors whose dimension equals the corpus vocabulary size, which cannot be inserted into the fixed 384-dim FloatVector field. Switch ingest to use createEmbedder from src/embeddings/index.js (Xenova/all-MiniLM-L6-v2, 384-dim, mean pooling, normalized) to embed all chunk texts in a batch. The model and dim should come from config (EMBEDDING_MODEL, DIM in .env) rather than being hardcoded in two places. Document in the README that the first ingest downloads the model (~90 MB) from HuggingFace. Acceptance: ingest produces 384-dim float vectors for every chunk and inserts them into Milvus without a dimension-mismatch error; running ingest twice does not duplicate rows (upsert by id is preserved).
---
Wire search to real Milvus vector search. Replace the TF-IDF ranking in src/core/search.js with: embed the query using the same MiniLM embedder used at ingest, run client.search against the documents collection (COSINE metric, ef-style over-fetch like the current EF=64 behavior), then keep the existing per-article chunk collapsing and best-passage extraction so the response shape (id, headline, details, score, attachment_url, best_passage) is unchanged. Both the CLI search command and the Fastify GET /search route call searchDocuments, so making it async propagates to both — update the call sites. Acceptance: with Milvus running and data ingested, CLI search and GET /search?q=... return ranked results from Milvus with the same response shape as today, and an empty collection returns an empty result list rather than an error.
---
Port article CRUD to Milvus and update docs and tests. listArticles, getArticle, and deleteArticle in src/data/collection.js (used by the article edit/delete features from sprint 3) currently scan collection.json; reimplement them with Milvus query/delete by id-prefix expression so article-level operations work against the real collection. Update README.md (Architecture section still describes the file-backed path and labels Milvus keys as unused) and ensure the existing live-Milvus test suites (tests/test_milvus_client__2.py, test_milvus_schema__3.py, gated on MILVUS_HOST) still pass, extending them to cover ingest-to-search round trip. Acceptance: with MILVUS_HOST set and the stack running, the live test suites pass including a new end-to-end test that ingests, searches, fetches one article, deletes it, and confirms it no longer appears in search results.
---
Wire the manual article-upload web form to the real Milvus collection. The Add Article form (public/index.html, from issue 17) and its endpoints POST /articles and POST /articles/bulk in src/server.mjs accept headline, details, and attachment_url, but they embed with the file-backed TF-IDF path and upsert into collection.json. Rework the create and update paths (POST /articles, POST /articles/bulk, PUT /articles/:id) to embed headline plus details with the same MiniLM embedder used at ingest and upsert the row into the Milvus documents collection, keeping the existing validation (400 on missing headline or details), the returned {id} contract, and the form UX unchanged. A manually added article must be findable via search immediately after creation (flush or load as needed so the insert is visible to queries). Acceptance: with the stack running, submitting the web form with a headline, contents, and link creates a row in the Milvus collection carrying id, headline, details, attachment_url, and a 384-dim embedding, the article appears in GET /search results for a matching query right away, and the existing acceptance tests for issue 17 and issue 18 still pass against the Milvus-backed endpoints.
```

## Notes

- Ticket order matters: 1 (init/ingest) → 2 (embedder) can land together; 3
  (search) depends on 1+2; 4 (CRUD/docs/tests) and 5 (manual upload form →
  Milvus) depend on 1–3. Mark dependencies so the sprint manager levels them
  correctly.
- The manual-upload web form itself already exists (issue #17: Headline /
  Details / Attachment URL → POST /articles, plus /articles/bulk); ticket 5 is
  backend rewiring only, no new UI.
- Already done inline on develop (not a ticket): `MILVUS_ADDRESS` fallback in
  `src/milvus/client.js` and `src/milvus/schema.ts` (`MILVUS_HOST`/`MILVUS_PORT`
  still take precedence for the live tests).
- Local prerequisites before running the sprint: Docker Desktop running,
  `npm run milvus:up` (wait ~90 s for healthcheck), `npm install` in the
  coder/tester clones, network access for the one-time MiniLM model download.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
