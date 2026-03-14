# OpenRabbit Production-Grade Testing Strategy

**Status:** 15 Test PRs Ready  
**Repository:** https://github.com/IbrahimNazir/openrabbit-e2e-tests

## Test Matrix: 15 PR Scenarios

| PR | Branch | Scenario | Expected |
|---|--------|----------|----------|
| 1 | pr/001-hardcoded-secret | Gitleaks detection | Stage 0: CRITICAL finding |
| 2 | pr/002-sql-injection | SQL injection pattern | Stage 2 FILE: Critical finding |
| 3 | pr/003-typescript-null-deref | Null dereference | Stage 2 HUNK: High finding |
| 4 | pr/004-auth-signature-change | Cross-file impact | Stage 3: Calling sites analysis |
| 5 | pr/005-style-issues | PEP8 violations | Stage 4: Style findings (LOW) |
| 6 | pr/006-auth-hardening | Multi-hunk security (7 hunks) | Stage 2+: Multiple findings |
| 7 | pr/007-dependabot-update | Bot-authored PR | SKIP (FilterEngine) |
| 8 | pr/008-docs-update | Docs-only changes | SKIP (FilterEngine) |
| 9 | pr/009-lockfile-update | Lockfile-only changes | SKIP (FilterEngine) |
| 10 | pr/010-large-refactor | 52+ files | SLOW_LANE queue |
| 11 | pr/011-query-optimization | Incremental index | RAG: Partial re-index |
| 12 | pr/012-admin-endpoint | Custom config | ignore backend/admin/** |
| 13 | pr/013-data-transformer | Data utility | AST validation |
| 14 | pr/014-payment-retry | Conversation test | /fix command support |
| 15 | pr/015-final-test | Installation baseline | Full repo indexing (142+ chunks) |

## Expected Logging Points

### Stage 0 - Linters
```
[STAGE_0] Linting initiated
[STAGE_0] gitleaks: findings=1 severity=CRITICAL
[STAGE_0] Linting completed duration_ms=450 findings_count=1
```

### Stage 1 - Complexity
```
[STAGE_1] Complexity analysis completed duration_ms=120
```

### Stage 2 - Bug Detection
```
[STAGE_2] FILE_LEVEL analysis: sql_injection detected
[STAGE_2] HUNK_LEVEL analysis: null_dereference detected
[STAGE_2] Analysis completed findings_count=X
```

### Stage 3 - Cross-File Impact
```
[STAGE_3] Cross-file impact: calling_sites=3
[STAGE_3] Impact analysis completed duration_ms=2100
```

### Stage 4 - Style Review
```
[STAGE_4] Style review: violations_count=3
[STAGE_4] Style review completed duration_ms=200
```

### Stage 5 - Synthesis
```
[STAGE_5] RAG context retrieval: chunks_retrieved=3
[STAGE_5] LLM synthesis: model=gemini tokens=4200
[STAGE_5] Synthesis completed findings_count=2
```

### FilterEngine
```
[FILTER_ENGINE] Pattern match: SKIP_BOT / SKIP_DOCS / SKIP_LOCKFILE / SLOW_LANE
```

### RAG Indexing
```
[RAG_INDEXER] Fetching tree: files_total=28
[RAG_INDEXER] Chunking: chunks_created=142
[RAG_INDEXER] Embeddings: vectors_created=142 duration_ms=8400
[RAG_INDEXER] Storage: vectors_upserted=142
[RAG_INDEXER] Indexing completed chunks_total=142
```

## How to Run Tests

### 1. Start Services
```bash
docker ps | grep -E "postgres|redis|qdrant"
celery -A app.tasks.celery_app worker -Q fast_lane,slow_lane -l info
```

### 2. Trigger Installation (Full Indexing)
```bash
export GITHUB_TOKEN=ghp_sometoken
python scripts/trigger_webhook.py --org IbrahimNazir --repo openrabbit-e2e-tests --action created

# Monitor
docker logs openrabbit-worker -f | grep "RAG_INDEXER"
```

### 3. Trigger PR Reviews
```bash
# Manual: Go to https://github.com/IbrahimNazir/openrabbit-e2e-tests/pulls
# Click on PR, wait for comment from OpenRabbit bot

# Or programmatic:
python scripts/trigger_webhook.py --org IbrahimNazir --repo openrabbit-e2e-tests --pr 1 --action opened
```

### 4. Monitor Logs
```bash
docker logs openrabbit-worker -f | grep -E "STAGE_|FILTER_ENGINE|RAG_"
```

### 5. Verify Comments
```bash
gh pr view IbrahimNazir/openrabbit-e2e-tests/1 --json comments
```

## Verification Checklist

- [ ] PR #1: Secret detected (CRITICAL) → Comment posted
- [ ] PR #2: SQL injection found → Comment with fix posted
- [ ] PR #3: Null deref found → Optional chaining suggestion
- [ ] PR #4: Cross-file impact logged → calling_sites count shown
- [ ] PR #5: Style violations → Line length flagged
- [ ] PR #6: Multi-hunk → RAG context included
- [ ] PR #7: Dependabot → SKIP logged, no comment
- [ ] PR #8: Docs → SKIP logged, no comment
- [ ] PR #9: Lockfile → SKIP logged, no comment
- [ ] PR #10: Large (52 files) → SLOW_LANE logged
- [ ] PR #11: Incremental → Only modified chunks indexed
- [ ] PR #12: Custom config → backend/admin/** ignored
- [ ] PR #13: AST validation → Suggestions correct
- [ ] PR #14: Conversation → /fix command works
- [ ] PR #15: Installation → 142+ chunks created, embedded

## Key Metrics to Track

| Metric | Target | Check |
|--------|--------|-------|
| Stage 0 Detection | 100% gitleaks | PR #1 comment |
| Stage 2 Accuracy | 100% SQL finding | PR #2 comment |
| Stage 3 Call Sites | 100% identified | PR #4 logs |
| Stage 4 Coverage | 100% PEP8 | PR #5 comment |
| FilterEngine Skip | 0% false neg | PRs #7-9 no comment |
| RAG Chunks | 142+ | PR #15 logs |
| Embeddings | 1024-dim | Qdrant check |
| Fast Lane | <30s | Monitor logs |
| Slow Lane | <5m | Background queue |

## Log Locations

```bash
# Real-time
docker logs openrabbit-worker -f

# Filtered
docker logs openrabbit-worker | grep "STAGE_"
docker logs openrabbit-worker | grep "RAG_INDEXER"
docker logs openrabbit-worker | grep "FILTER_ENGINE"

# Database
psql -U openrabbit -d openrabbit -c \
  "SELECT pr_number, findings_count FROM pr_reviews ORDER BY pr_number;"
```

## Debugging

### Check GitHub Webhooks
```bash
gh api repos/IbrahimNazir/openrabbit-e2e-tests/hooks
```

### Check Celery Tasks
```bash
celery -A app.tasks.celery_app inspect active
celery -A app.tasks.celery_app inspect reserved
```

### Check Database
```bash
psql -U openrabbit -d openrabbit -c \
  "SELECT pr_number, status, findings_count FROM pr_reviews;"

psql -U openrabbit -d openrabbit -c \
  "SELECT severity, count(*) FROM findings GROUP BY severity;"
```

### Check RAG Index
```bash
# Qdrant status
curl http://localhost:6333/health

# Check collection
curl http://localhost:6333/collections/code_chunks | jq '.result.points_count'
```

## Next Steps

1. Trigger installation webhook for full repo indexing
2. Monitor RAG indexer logs (expect 142+ chunks)
3. Trigger PR #1 webhook → expect CRITICAL secret finding
4. Monitor all stages: verify each logs timing + findings
5. Test conversation on PR #14 (reply with /fix)
6. Load test: trigger all 15 PRs in parallel

**This validates all pipeline stages work end-to-end.** ✅
