# RAG Notes: How LifeStream Search Works and Why

A one-page shared reference for Stage 2 (F8–F9).

## 1. The basic pattern

Retrieval-augmented generation (RAG) answers a question in three steps
(Lewis et al., 2020; survey: Gao et al., 2023):

1. **Index:** split documents into chunks, turn each into a vector (an
   *embedding*) and store the vectors.
2. **Retrieve:** embed the question the same way and fetch the chunks whose
   vectors are closest (cosine similarity).
3. **Generate:** give the LLM only those chunks and ask it to answer from them,
   citing which ones it used.

The LLM never sees the whole library, only the few relevant chunks. Answers stay
grounded in what was recorded, and every claim can point to a video and timestamp.

Gao et al. group RAG systems into *naive* (the three steps above), *advanced* (adds
pre-retrieval steps such as query rewriting and post-retrieval steps such as
re-ranking) and *modular*. LifeStream is naive RAG plus two advanced steps:
date filtering from the question, and parent→child re-ranking.

## 2. Choices made in LifeStream

| Choice | What we do | Why |
|---|---|---|
| Embedding model | `BAAI/bge-small-en-v1.5`, 384 dimensions, local CPU | Strong for its size on retrieval benchmarks (BGE: Xiao et al., 2023; MTEB: Muennighoff et al., 2022); free, offline, no extra API key. Questions get bge's query instruction prefix. |
| Vector store | Postgres + pgvector, HNSW index, cosine distance | One database for vectors *and* metadata (dates, speakers), so date filters are plain SQL. HNSW (Malkov & Yashunin, 2018) keeps search fast as the library grows. |
| Chunk types | (a) each summary paragraph: overview, each topic, decisions, action items; (b) ~2-minute transcript windows cut at speaker turns with 30 s overlap | Summary paragraphs answer most questions precisely. Transcript chunks catch details the summary left out. Cutting at turns never splits a sentence; overlap keeps exchanges that cross a cut. |
| What gets embedded | The overview embeds a header (title, date, speakers); every other chunk embeds only its own content | With a shared header, all paragraphs of one video look alike and the generic overview wins. Embedding content alone keeps specific paragraphs distinct. |
| Re-ranking | Summary paragraphs +0.05; if a video's overview ranks first, its best specific paragraph moves in front of it (parent→child) | The overview finds the right video; the specific paragraph is the answer. |
| Time phrases | Rules turn "last week", "yesterday", "on Nov 4", "in October"... into a date range, used as a SQL filter; an LLM handles unusual wording | A date filter removes look-alike content from other weeks, which similarity alone can't. Rules are predictable and testable. Weeks run Monday–Sunday. |
| Generation | Claude answers only from numbered excerpts, cites excerpt numbers, and says "not found" when they don't contain the answer | Grounding and honest failure matter more than fluent guesses. |
| Speaker names | Applied before indexing, so names are searchable; re-indexed when names change | "What did Alice say" needs the name in the chunk text. |

## 3. How we measure it

- **Retrieval data test (F9):** generated videos with known answers. We report
  **recall@k** (is a correct chunk in the top k?) and **MRR**, the mean
  reciprocal rank (1/rank of the first correct chunk, averaged over questions).
  Current: recall@3 = 100%, MRR = 0.90 on 10 questions.
- **M2:** "What did we discuss about the frontend last week?" must return the
  frontend topic paragraph of last week's meeting first, not a near-identical
  meeting from three weeks earlier. Current: passes.
- **Real videos:** 16 hand-written questions (`labels/questions.yaml`), 15/16
  with the right video in the top 3. The miss is an ambiguous phrase: "last
  Tuesday" asked on a Wednesday.

## 4. Known limits and next steps

- **Keyword-only matches** (rare names, product codes) can be missed by
  embeddings. Next step: hybrid search, adding Postgres full-text and merging the
  two rankings with reciprocal rank fusion (Cormack et al., 2009).
- **Ambiguous time phrases** ("last Tuesday") follow one fixed reading; the
  answer always shows the dates it searched.
- **Re-ranking with a cross-encoder** would improve ordering at extra cost per
  query. Not needed at current library sizes.

## References

- Lewis et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS.
- Gao et al. (2023). *Retrieval-Augmented Generation for Large Language Models: A Survey.* arXiv:2312.10997.
- Xiao et al. (2023). *C-Pack: Packaged Resources To Advance General Chinese Embedding* (BGE models). arXiv:2309.07597.
- Muennighoff et al. (2022). *MTEB: Massive Text Embedding Benchmark.* arXiv:2210.07316.
- Malkov & Yashunin (2018). *Efficient and Robust Approximate Nearest Neighbor Search Using Hierarchical Navigable Small World Graphs.* IEEE TPAMI.
- Cormack, Clarke & Büttcher (2009). *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods.* SIGIR.
