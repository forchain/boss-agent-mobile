# 0008. LangGraph Resume Lifecycle Orchestration and Structured Profile Document Normalization

We decided to govern candidate resume parsing, semantic diffing, and versioned ingestion via a stateful LangGraph orchestrator (`ResumeLifecycleGraph`), elevate candidate memory representation from rigid relational dictionaries to a first-class lossless Markdown `Structured Profile Document` (`profile_document`), and incorporate deterministic self-healing normalization.

## Context
Previously, resume parsing was handled by ad-hoc scripts and procedural web endpoints:
- Ingestion and update workflows bypassed LangGraph, lacking unified state machine governance, clear stage boundaries, and auditable telemetry.
- Extraction prompts strictly enforced conventional HR employment dictionaries (`company`, `start_date`, `end_date`), creating severe impedance mismatches for project-driven, open-source, or entrepreneurial candidate backgrounds where formal company titles or explicit months were absent.
- The LLM fell back to stuffing all achievements, tech stacks, and project narratives into a single string while leaving structural array fields (`work_experiences`, `projects`, `target_positions`) empty or null.
- Downstream greeting drafting and semantic job matching consume candidate memory strictly as a unified markdown prompt block (`format_for_prompt()`); no downstream component performs granular SQL queries on individual work or project item fields.

## Decision
1. **Resume Lifecycle Graph (`ResumeLifecycleGraph`)**:
   Orchestrate the end-to-end resume lifecycle as a stateful LangGraph pipeline comprising:
   - `text_extractor`: Lossless extraction of ground-truth raw text from `.pdf`, `.docx`, `.txt`, and `.md` files.
   - `profile_document_generator`: High-fidelity LLM structuring producing core metadata (`name`, `years_of_experience`, `target_positions`, `core_skills`) along with an unabbreviated, standardized `Structured Profile Document` in Markdown.
   - `profile_normalizer`: Deterministic self-healing node ensuring missing fields or null values are repaired and default collections are guaranteed.
   - `diff_analyzer`: Structural and semantic changelog computation against any existing active profile in PocketBase.
   - `profile_persister`: Atomic persistence to PocketBase `candidate_profiles` and append-only version logging in `resume_revisions`.
2. **First-Class Structured Profile Document (`profile_document`)**:
   Elevate candidate memory to an organized, readable Markdown document comprising standardized sections:
   - Executive Positioning & Overview
   - Technology Taxonomy & Specialization Matrix
   - Key Projects & Architectural Accomplishments (lossless details, metrics, links)
   - Measurable Breakthroughs & Business Impact
   - Credentials, Language, and Ground Truth Reference
   Backwards compatibility is preserved in PocketBase by storing this document seamlessly in `raw_summary` while keeping `raw_resume_text` as the ground-truth text backup.
3. **Streamlined Web Console Experience**:
   Refactor the profile management UI from brittle nested form inputs into a developer-centric layout featuring top-level metadata tags, a full markdown profile previewer/editor, and an interactive incremental revision drawer.
