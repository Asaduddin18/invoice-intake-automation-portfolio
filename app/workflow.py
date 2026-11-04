"""Connect review decisions to durable accounting, with no model calls."""
from app.extraction_runs import ExtractionRuns
from app.connection import ConnectionError
from app.pipeline import InvoicePipeline
from app.review import ReviewStore, ReviewError


async def submit_case(connection, case_id, expected_revision):
    pipeline = InvoicePipeline(connection)
    tenant = pipeline.tenant()
    store = ReviewStore(connection.store.directory)
    with store.gate():
        case = store.get(tenant, case_id)
        if not case or case['revision'] != expected_revision:
            raise ReviewError('This page is stale. Reload before submitting.')
        status, issues = store.decision(tenant, case)
        if status != 'ready':
            result = {'status': status, 'message': '; '.join(issues)}
            store.outcome(tenant, case_id, result)
            return result
        source = ExtractionRuns(connection.store.directory).source(case_id)
        # Prevent edits in the gap before the pipeline takes the workflow gate.
        store.outcome(tenant, case_id, {'status': 'processing', 'message': 'Submission started; reconcile before editing.'})
    try:
        result = await pipeline.submit(case['candidate'], source, case['filename'],
            source_key=f'review:{case_id}:r{case["revision"]}',
            authorization={'case_id': case_id, 'revision': case['revision']})
    except (ValueError, OSError, ConnectionError):
        result = {'status': 'uncertain', 'message': 'Processing did not finish. Inspect the ledger and reconcile before editing or retrying.'}
    store.outcome(tenant, case_id, result)
    return result
