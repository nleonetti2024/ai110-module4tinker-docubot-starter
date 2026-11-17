"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob
import re

class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Build a retrieval index (implemented in Phase 1)
        self.index = self.build_index(self.documents)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Text helpers
    # -----------------------------------------------------------

    # Common words that carry no meaning for search. Without this list,
    # "Where is the ..." would match almost every section.
    STOPWORDS = {
        "a", "an", "the", "is", "are", "was", "be", "to", "of", "in", "on",
        "for", "and", "or", "it", "this", "that", "these", "those", "there",
        "do", "does", "i", "my", "we", "you", "what", "which", "where", "how",
        "when", "who", "why", "any", "all", "with", "by", "from", "as", "at",
        "can", "should", "if", "about", "me",
    }

    # A section must match at least this many distinct query keywords,
    # otherwise DocuBot refuses instead of guessing (guardrail).
    MIN_MATCHED_TERMS = 2

    def stem(self, word):
        """
        Very light stemming so related word forms match:
        "generated" / "generation" -> "generat", "users" -> "user",
        "connection" -> "connect". Not linguistically perfect, just consistent.
        """
        for suffix in ("ing", "ion", "ed", "s"):
            if word.endswith(suffix) and not word.endswith("ss") \
                    and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                break
        if word.endswith("e") and len(word) > 4:
            word = word[:-1]
        return word

    def tokenize(self, text):
        """
        Lowercase the text, split into words (underscores split too, so
        "auth_utils" gives "auth" and "utils"), drop stopwords, and stem.
        """
        words = re.findall(r"[a-z0-9]+", text.lower())
        return [self.stem(w) for w in words if w not in self.STOPWORDS]

    def split_into_sections(self, text):
        """
        Split a markdown document into sections, starting a new section at
        every heading line (#, ##, ###).

        Each section is prefixed with the path of its parent headings, e.g.
        "[Database Guide > Tables]" above "### users", so a small section
        still says what it belongs to (and "table" can match it).
        """
        sections = []
        heading_path = []   # list of (level, title) for the current position
        current = []
        breadcrumb = ""

        def flush():
            body = "\n".join(current).strip()
            # Skip empty sections and headings with no text under them.
            has_content = any(
                l.strip() and not l.startswith("#") for l in current
            )
            if body and has_content:
                sections.append(f"{breadcrumb}\n{body}" if breadcrumb else body)

        for line in text.splitlines():
            if line.startswith("#"):
                flush()
                current = []
                level = len(line) - len(line.lstrip("#"))
                title = line.lstrip("#").strip()
                heading_path = [(l, t) for l, t in heading_path if l < level]
                parents = [t for _, t in heading_path]
                breadcrumb = f"[{' > '.join(parents)}]" if parents else ""
                heading_path.append((level, title))
            current.append(line)
        flush()
        return sections

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, documents):
        """
        Build a tiny inverted index mapping lowercase words to the documents
        they appear in, e.g. {"token": ["AUTH.md", "API_REFERENCE.md"]}.
        """
        index = {}
        for filename, text in documents:
            for token in set(self.tokenize(text)):
                index.setdefault(token, []).append(filename)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        Relevance score = number of distinct query keywords that appear
        in the text. Counting distinct words (not total occurrences) stops a
        long section that repeats "token" ten times from beating a short
        section that matches every word of the question.
        """
        query_terms = set(self.tokenize(query))
        text_terms = set(self.tokenize(text))
        return len(query_terms & text_terms)

    def retrieve(self, query, top_k=3):
        """
        1. Use the index to find candidate documents containing any query word.
        2. Split those documents into sections and score each section.
        3. Drop sections below MIN_MATCHED_TERMS (the refusal guardrail).
        4. Return the top_k (filename, section_text) pairs, best first.
        """
        query_terms = set(self.tokenize(query))
        if not query_terms:
            return []

        candidate_files = set()
        for term in query_terms:
            candidate_files.update(self.index.get(term, []))

        # Short queries (e.g. "database") can only match 1 term.
        min_score = min(self.MIN_MATCHED_TERMS, len(query_terms))

        scored = []
        for filename, text in self.documents:
            if filename not in candidate_files:
                continue
            for section in self.split_into_sections(text):
                score = self.score_document(query, section)
                if score >= min_score:
                    heading = self.section_heading(section)
                    heading_hits = self.score_document(query, heading)
                    scored.append((score, heading_hits, filename, section))

        # Highest score first. Ties go to the section whose own heading
        # matches the query, then to the shorter (more focused) section.
        scored.sort(key=lambda item: (-item[0], -item[1], len(item[3])))
        return [(filename, section) for _, _, filename, section in scored[:top_k]]

    def section_heading(self, section):
        """Return the section's own heading line (skipping the breadcrumb)."""
        for line in section.splitlines():
            if line.startswith("#"):
                return line
        return ""

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
