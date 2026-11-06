function safeFailure(input) {
  const ids={northmereP10Intake:'intake',northmereP10Reports:'reporting'};
  const operation=['intake','status','report'].includes(input.operation)?input.operation:(ids[input.workflow?.id]||'workflow');
  const codes=new Set(['API_NOT_CONFIGURED','UNAUTHORIZED','SERVER_REQUEST_REQUIRED','INVALID_REQUEST','UNKNOWN_REPORT_MONTH',
    'CONNECTION_REQUIRED','OPERATION_BUSY','OPERATION_FAILED','STATUS_UNAVAILABLE','TRANSPORT_FAILED','RESPONSE_INVALID']);
  return {status:'action_required',operation,code:codes.has(input.code)?input.code:'WORKFLOW_FAILED',
    correlation_id:typeof input.correlation_id==='string'&&/^[a-f0-9]{32}$/.test(input.correlation_id)?input.correlation_id:null,
    execution_ref:typeof input.execution?.id==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(input.execution.id)?input.execution.id:null,
    automatic_retry:false,next_action:operation==='report'||operation==='reporting'?'inspect_saved_report':'inspect_saved_state',
    operator_message:'Inspect the saved operation before retrying. Do not rerun invoice creation to fix a report. No notification was sent.'};
}
