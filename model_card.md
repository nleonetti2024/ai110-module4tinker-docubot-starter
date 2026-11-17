# DocuBot Model Card

This model card is a short reflection on my DocuBot system after implementing retrieval and experimenting with all three modes:

1. Naive LLM over full docs
2. Retrieval only
3. RAG (retrieval plus LLM)

All results below come from running each mode on the 8 sample queries in `dataset.py` with `gemini-3.5-flash-lite`.

---

## 1. System Overview

**What is DocuBot trying to do?**

> DocuBot answers developer questions about a project using only the project's own documentation in `docs/`. It's meant to show the difference between an LLM that guesses, a search system that only returns evidence, and a RAG system that answers from that evidence and refuses when none exists.

**What inputs does DocuBot take?**

> A developer's question (typed or from `SAMPLE_QUERIES` in `dataset.py`), the `.md`/`.txt` files in `docs/` (`AUTH.md`, `API_REFERENCE.md`, `DATABASE.md`, `SETUP.md`), and the `GEMINI_API_KEY` environment variable for Modes 1 and 3.

**What outputs does DocuBot produce?**

> - Mode 1: a free-form LLM answer.
> - Mode 2: the top 1–3 documentation sections, each labeled with its filename and heading path, or the refusal "I do not know based on these docs."
> - Mode 3: a short LLM answer written from those sections that names the files it used, or the refusal "I do not know based on the docs I have."

---

## 2. Retrieval Design

**How does your retrieval system work?**

> - **Tokenizing:** lowercase the text, split on anything that isn't a letter or digit (so `auth_utils` becomes `auth` and `utils`), drop stopwords like *where / is / the / which*, and apply light suffix stemming so *generated / generation*, *connect / connection* and *users / user* match.
> - **Index:** `build_index` maps each token to the list of files that contain it, for example `"token" -> ["AUTH.md", "API_REFERENCE.md"]`. `retrieve` uses the index to skip files that share no words with the query.
> - **Unit of retrieval:** every candidate file is split into **sections at each markdown heading** (`#`, `##`, `###`). Each section is prefixed with its parent-heading path, e.g. `[Database Guide > Tables]` above `### users`, so a small section keeps its context. Sections that are only a heading with no text are skipped.
> - **Scoring:** `score_document` counts how many **distinct** query keywords appear in the section. Distinct counting stops a long section that repeats "token" many times from beating a short section that matches every word of the question.
> - **Choosing top snippets:** sort by score, then by how many query words appear in the section's own heading, then by shortest section. Return the top 3.

**What tradeoffs did you make?**

> - **Simplicity over accuracy:** keyword overlap only. There are no synonyms or embeddings, so "sign in" would not match "login".
> - **Heading sections instead of whole files or fixed-size slices:** snippets are much smaller and stay on one topic. The cost is that the score depends on how the doc author structured the headings.
> - **The stemmer is crude** (it produces "generat", "databas"), but it's applied the same way to queries and docs, so matching stays consistent.
> - **The refusal threshold is a fixed number** (2 matched keywords), which is easy to understand but not adaptive. It can refuse a short or oddly worded question that the docs actually answer.

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**

> - **Naive LLM mode:** calls Gemini once per question. Note that `naive_answer_over_full_docs` builds its prompt from the question **only**. It ignores `all_text`, so the model never sees the docs and answers purely from general knowledge.
> - **Retrieval only mode:** never calls the LLM. It prints the retrieved sections, or refuses.
> - **RAG mode:** runs `retrieve()` first. If the guardrail returns no snippets, it refuses **without calling the LLM at all**. Otherwise it sends only the top 3 sections to Gemini.

**What instructions do you give the LLM to keep it grounded?**

> The RAG prompt in `llm_client.py` tells the model to:
> - answer using only the provided snippets,
> - not invent functions, endpoints or configuration values,
> - reply exactly "I do not know based on the docs I have." when the snippets aren't enough,
> - name the files it relied on.

---

## 4. Experiments and Comparisons

The same 8 sample queries were run, with identical wording, in all three modes.

| Query | Naive LLM | Retrieval only | RAG | Notes |
|---|---|---|---|---|
| Where is the auth token generated? | **Not helpful.** Asked which framework I'm using (Firebase, OAuth2, NextAuth…). It has no idea `auth_utils.py` exists. | **Helpful.** #1 is `AUTH.md › Token Generation`, which names `generate_access_token` in `auth_utils.py`. | **Helpful.** "`generate_access_token` in `auth_utils.py`", citing AUTH.md. | RAG gives the one-sentence answer that retrieval buries in a full section. |
| What environment variables are required for authentication? | **Harmful.** Invented `API_KEY`, `CLIENT_ID`/`CLIENT_SECRET`, `JWT_SECRET`. None of these exist in the project. | **Helpful.** `SETUP.md › Required Variables` and `AUTH.md › Environment Variables`. | **Helpful.** `AUTH_SECRET_KEY` and `TOKEN_LIFETIME_SECONDS` with the 3600s default, citing both files. | Clearest example of confident hallucination. |
| How do I connect to the database? | **Harmful.** Invented `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD` variables and a JavaScript ORM example in a Python project. | **Helpful but noisy.** All 3 hits are from DATABASE.md, but the intro ranks above `Connection Configuration`. | **Helpful.** `DATABASE_URL`, the `sqlite:///app.db` default, the Postgres example and the `db.py` module. | Retrieval was "accurate but hard to read"; RAG cleaned it up. |
| Which endpoint lists all users? | **Weak.** Listed `/users`, `/api/users` and `/v1/users` as common guesses and asked for context. | **Helpful.** #1 is `GET /api/users`, including "Only accessible to admins". | **Mostly helpful.** `GET /api/users`, but it **dropped the admin-only restriction** that was in its snippet. | See failure case 3. |
| What does the /api/projects/<project_id> route return? | **Harmful.** Invented `id`, `created_at`, `updated_at`, `metadata`. | **Partly helpful.** #1 is the *list* route `GET /api/projects`; the right section is #2. | **Helpful.** Correct fields (`project_id`, `name`, `description`, `status`, `owner`) plus 401/403/404. | RAG compensated for retrieval's ranking mistake because the right section was still in the top 3. |
| Is there any mention of payment processing in these docs? | **Unhelpful.** "Please provide the documentation." It never saw the docs, so it can't even answer a yes/no question about them. | **Correct refusal.** | **Correct refusal**, from DocuBot itself ("…these docs."), so Gemini was never called. | The guardrail works. |
| How does a client refresh an access token? | **Harmful.** A full OAuth tutorial: `/token` endpoint, `grant_type=refresh_token`, `client_secret`. Nothing like this is in the docs. | **Helpful.** `AUTH.md › Client Workflow`, then `POST /api/refresh`. | **Partly helpful.** "Call `/api/refresh` when the token expires", but it left out the `Authorization: Bearer` header, and API_REFERENCE says the token must still be *valid*. | See failure case 4. |
| Which fields are stored in the users table? | **Harmful.** Invented `id`, `username`, `first_name`, `role`, `created_at` and more, and told me to check `schema.rb` (a Ruby on Rails file). | **Helpful.** #1 is `DATABASE.md › Tables › users`. | **Helpful.** Exactly `user_id`, `email`, `password_hash`, `joined_at`. | |

**What patterns did you notice?**

> - **Naive LLM looks impressive but is untrustworthy.** In this starter, `naive_answer_over_full_docs` never sends the docs, only the question. On 5 of 8 questions it produced long, well-formatted, confident answers made of generic "industry standard" details (JWT_SECRET, DB_HOST, OAuth `grant_type`, a `username` column) that don't exist in this project. On the other 3 it asked for context. That's more honest, but still useless.
> - **Retrieval only is clearly better than naive** whenever you need proof. Every result is real documentation with a filename, and it refused the payment question. But it's raw: full markdown sections with JSON blocks, the useful line is often buried, and its ranking mistakes (projects list vs. detail, DB intro vs. connection section) are left for the reader to sort out.
> - **RAG is clearly better than both** for readability. It turned three noisy database sections into one correct sentence about `DATABASE_URL`, and it fixed retrieval's projects ranking mistake by picking the right snippet. It also inherits the guardrail: the payment question was refused before the LLM was ever called.
> - **RAG is only as good as its snippets and how faithfully it summarizes them.** It never invented anything, but it did leave out important details that were in its snippets (failure cases 3 and 4).

---

## 5. Failure Cases and Guardrails

**Failure case 1: naive mode hallucinates project details**

> - **Question:** "Which fields are stored in the users table?"
> - **What happened:** naive mode confidently listed 10 fields (`id`, `username`, `first_name`, `role`, `created_at`, …) and pointed to `schema.rb`.
> - **What should have happened:** either the real fields (`user_id`, `email`, `password_hash`, `joined_at`) or "I do not know". RAG got this right.

**Failure case 2: retrieval ranks the wrong section first**

> - **Question:** "What does the /api/projects/<project_id> route return?"
> - **What happened:** retrieval ranked `GET /api/projects` (the list endpoint) above `GET /api/projects/<project_id>`. Both mention "projects", "returns" and "api", and keyword scoring ignores the `<project_id>` path structure.
> - **What should have happened:** the detail route should rank first. RAG happened to recover because the right section was still in the top 3; with `top_k=1` it would have answered about the wrong endpoint.

**Failure case 3 (RAG): dropped a security restriction**

> - **Question:** "Which endpoint lists all users?"
> - **What happened:** RAG answered "`GET /api/users`" and stopped. The snippet it was given says "Only accessible to admins" and "Returns 403 for non admin accounts."
> - **What should have happened:** the answer should include the admin-only restriction. A developer could build a feature on this endpoint for regular users and only find out from 403 errors. The prompt says "use only the snippets", but nothing makes the model include important conditions.

**Failure case 4 (RAG): didn't notice conflicting docs**

> - **Question:** "How does a client refresh an access token?"
> - **What happened:** RAG said to call `/api/refresh` "when the token expires" (from AUTH.md). But API_REFERENCE.md, also in its snippets, says the endpoint exchanges an existing ***valid*** token and needs the `Authorization: Bearer` header. RAG blended the two without pointing out the contradiction and omitted the header.
> - **What should have happened:** state the steps, including the header, and flag that the two docs disagree about expired vs. valid tokens.

**Failure case 5 (setup): the LLM itself was unavailable**

> The starter's model name `gemini-flash-lite-latest` had been retired, so every Mode 1 and Mode 3 call returned `404 NOT_FOUND` until I listed the available models and switched to `gemini-3.5-flash-lite`. Running modes back to back then hit the free tier's **15 requests/minute** limit (`429 RESOURCE_EXHAUSTED`). Both errors were shown as the "answer", which is safe (nothing invented), but retrieval-only mode was the only mode that kept working throughout.

**When should DocuBot say "I do not know based on the docs I have"?**

> - When the topic doesn't appear in the docs at all (payment processing, Kubernetes deployment, the weather).
> - When the docs mention the words but don't answer the question, e.g. "What is the admin password?" or "What's the production DATABASE_URL?". Only examples are documented, not real values.
> - Ideally also when the retrieved docs contradict each other, as in failure case 4: say so instead of picking one side.

**What guardrails did you implement?**

> - **Evidence threshold in `retrieve()`:** a section must match at least `MIN_MATCHED_TERMS = 2` distinct query keywords (or every keyword if the query has only one). If no section passes, `retrieve()` returns `[]`.
> - **Refusal happens before the LLM:** both answer modes check for empty retrieval first and return "I do not know…", so RAG never sends an empty or irrelevant context to Gemini. The payment question shows this: the refusal text is DocuBot's own ("…these docs"), not Gemini's ("…the docs I have").
> - **Stopword filtering:** "Where is the…" can't count as evidence.
> - **Small, capped context:** at most 3 heading-sized sections, so the model can't wander through the whole corpus.
> - **Prompt rules:** snippets only, no invented values, exact refusal phrase, cite files. In testing, RAG never invented a value and cited its files on every answer.

---

## 6. Limitations and Future Improvements

**Current limitations**

1. Keyword matching only: no synonyms ("sign in" ≠ "login") and no understanding of URL paths or code identifiers (failure case 2).
2. The fixed threshold of 2 keywords is arbitrary. It can refuse oddly phrased questions the docs do answer, and it accepts weak one-word queries.
3. RAG summaries can leave out important conditions (admin-only) and don't flag conflicting sources (failure cases 3 and 4).
4. Naive mode never sends the docs, so it isn't a fair "LLM with the docs pasted in" baseline.
5. The LLM modes depend on an external model name and rate limits that can change or run out (failure case 5).

**Future improvements**

1. Weight rare words higher (TF-IDF/BM25) and match URL paths exactly, so `/api/projects/<project_id>` beats `/api/projects`.
2. Add prompt rules such as "always include access restrictions, required headers and error codes from the snippets" and "if snippets disagree, say so", and print each snippet's score so users can judge the confidence.
3. Read the model name from `.env` (e.g. `GEMINI_MODEL`) and add retry with backoff on 429 errors, so the LLM modes don't break when a model is retired or a burst of questions hits the limit.

---

## 7. Responsible Use

**Where could this system cause real-world harm if used carelessly?**

> A developer could trust a fluent naive-mode answer and wire up a function, endpoint or env var that doesn't exist, or skip a security detail. For example, the docs say `GET /api/users` is admin-only and that tokens must never go in URL query parameters. RAG lowers this risk but doesn't remove it: if retrieval ranks the wrong section first, the LLM will confidently summarize the wrong evidence. Stale docs are another risk, because DocuBot only knows what's written, not what the code does today.

**What instructions would you give real developers who want to use DocuBot safely?**

- Prefer RAG or Retrieval-only mode, and treat Naive mode answers as unverified guesses.
- Check the cited file and section before acting on an answer, especially for auth, permissions and database settings.
- Treat "I do not know" as a signal to read the code or ask a teammate, not to rephrase until the bot says something.
- Keep `docs/` up to date; DocuBot is only as accurate as the docs it reads.
