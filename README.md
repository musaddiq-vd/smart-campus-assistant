# Smart Campus Assistant

An AI student helpdesk for a fictional college, **XYZ College of Engineering**, built with LangGraph on AWS Bedrock.

Students sign in with their email and password and ask about the college (exam notices, fees, rules, hostel, library, branches, placements) or about themselves (fee status, books issued, attendance, results, upcoming exams). Admins sign in to a separate portal with college-wide student overviews.

All college documents and student records are **synthetic**.

## How a question is answered
A cheap router picks the lightest path that can answer, because running a full research loop for every question costs 5-20x more tokens than single-shot RAG:

```
                ┌── simple ───▶ simple_answer ────────────────────────────────▶ answer
question ─▶ router ── personal ─▶ personal_answer (own record + policy docs) ──▶ answer
                └── deep ─────▶ planner ─▶ researcher ⟲ (gap check) ─▶ writer ─▶ cited report
```

- **router**: Amazon Nova Micro labels the question `simple`, `personal` or `deep`.
- **simple_answer**: one retrieval and one Nova Lite call.
- **personal_answer**: reads only the signed-in student's record, plus related policy docs. Guests and admins get no personal data, and questions about another student are refused.
- **deep research**: a planner splits the question into sub-questions, a researcher retrieves and takes notes (with a bounded gap-check loop), and a writer produces a report with citations.

## Stack
| Part | Choice |
|---|---|
| Orchestration | LangGraph |
| LLMs | Amazon Nova Micro (router), Nova Lite (answers) on Bedrock |
| Embeddings | Amazon Titan Text Embeddings v2 |
| Vector store | Amazon S3 Vectors (`langchain_aws.AmazonS3Vectors`) |
| UI | Streamlit: login, student portal, admin portal |
| Auth | Email + password, PBKDF2-SHA256 hashes, login throttling |
| Data | Synthetic markdown docs in `data/college/` and student records in `data/students.json` |

## Quick start
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
pip install -e .
cp .env.example .env              # then add your AWS settings
python -m smart_campus_assistant.auth seed-demo   # creates demo accounts
```

`seed-demo` writes `data/users.json` (password hashes only) and `data/demo_credentials.csv` (demo passwords for local use). Both files are git-ignored. Manage accounts with `add-user`, `set-password` and `list-users`.

### Try it offline (no AWS needed)
```bash
SCA_DEMO_MODE=1 streamlit run app.py
```

### Run with AWS
1. In the AWS console (us-east-1), create an S3 vector bucket named as in `VECTOR_BUCKET_NAME`, and enable Bedrock model access for Nova Micro, Nova Lite and Titan Text Embeddings v2.
2. Index the documents: `python -m smart_campus_assistant.ingest` (use `--dry-run` to only count chunks).
3. Run the app: `streamlit run app.py`, or open LangGraph Studio with `langgraph dev`.

### Evaluation (command line only)
`python eval/run_eval.py` runs 45 labelled questions and compares the router against an "always deep" baseline (route accuracy, tokens, cost, latency). Use `--fake` to run it offline.

## Project layout
```
src/smart_campus_assistant/   graph, nodes, prompts, retriever, ingest, auth, student data
ui/                           Streamlit pages and components
eval/                         labelled questions and the eval runner
tests/                        offline tests (fake LLM and retriever)
data/                         synthetic college docs and student records
```

## Tests
```bash
python -m pytest -q
```
All tests run offline.
