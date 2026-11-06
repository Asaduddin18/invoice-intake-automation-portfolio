// Operational summaries only. Never copy raw error text or invoice fields.
function normalize(input, operation, mode) {
  const allowedCodes = new Set(['API_NOT_CONFIGURED','UNAUTHORIZED','SERVER_REQUEST_REQUIRED','INVALID_REQUEST',
    'UNKNOWN_REPORT_MONTH','CONNECTION_REQUIRED','OPERATION_BUSY','OPERATION_FAILED','STATUS_UNAVAILABLE']);
  const expected = {intake:'INTAKE_COMPLETED',status:'STATUS_READY',report:'REPORT_VERIFIED'};
  const body = input && typeof input.body === 'object' && input.body !== null ? input.body : null;
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const month = value => typeof value === 'string' && /^[0-9]{4}-(0[1-9]|1[0-2])$/.test(value);
  const correlation = body && typeof body.correlation_id === 'string' && /^[a-f0-9]{32}$/.test(body.correlation_id) ? body.correlation_id : null;
  const base = {operation,request_ok:false,manual_invocation:mode==='manual'||mode==='cli'||mode==='test',
    correlation_id:correlation,automatic_retry:false,code:'TRANSPORT_FAILED',
    next_action:operation==='report'?'inspect_saved_report':'inspect_saved_state'};
  if (!body) return base;
  base.code = allowedCodes.has(body.code) ? body.code : 'RESPONSE_INVALID';
  if (input.statusCode !== 200 || body.code !== expected[operation] || !body.data || typeof body.data !== 'object') return base;
  const data = body.data;
  if (operation === 'report') {
    if (!month(data.month) || !integer(data.deliveries) || data.report_status !== 'verified') return base;
    return {...base,request_ok:true,code:body.code,month:data.month,deliveries:data.deliveries,
      report_status:'verified',next_action:'none'};
  }
  const statuses = new Set(['verified','held','awaiting_approval','awaiting_review','ready','unsupported',
    'needs_inspection','received','extracted','extracting','uncertain','processing','other']);
  if (!integer(data.deliveries) || !integer(data.duplicate_deliveries) || data.duplicate_deliveries>data.deliveries ||
      !data.counts || Array.isArray(data.counts) || typeof data.counts!=='object' ||
      !Array.isArray(data.available_report_months) || !data.available_report_months.every(month) ||
      !['automatic','required'].includes(data.review_policy) || data.new_extraction_budget!==0) return base;
  const counts={};
  for (const [key,value] of Object.entries(data.counts)) {
    if (!statuses.has(key) || !integer(value)) return base;
    counts[key]=value;
  }
  if (Object.values(counts).reduce((a,b)=>a+b,0)!==data.deliveries) return base;
  if (operation==='status' && (!data.readiness || !['xero_saved','google_saved','xero_configured','google_configured'].every(k=>data.readiness[k]===true))) {
    return {...base,code:'CONNECTION_REQUIRED',next_action:'inspect_local_connections'};
  }
  let poll;
  if (operation==='intake') {
    if (!data.poll || !integer(data.poll.new_deliveries) || !integer(data.poll.already_recorded) || typeof data.poll.more_pages!=='boolean') return base;
    poll={new_deliveries:data.poll.new_deliveries,already_recorded:data.poll.already_recorded,more_pages:data.poll.more_pages};
  }
  const attention = Object.entries(counts).some(([key,value])=>key!=='verified'&&value>0);
  return {...base,request_ok:true,code:body.code,deliveries:data.deliveries,counts,
    duplicate_deliveries:data.duplicate_deliveries,review_policy:data.review_policy,
    available_report_months:[...new Set(data.available_report_months)].sort(),new_extraction_budget:0,
    attention_required:attention,...(poll?{poll}:{}),next_action:attention?'inspect_invoice_app':'none',
    operator_message:attention?'The request completed. Review the held, unsupported or pending deliveries in the invoice app.':'The request completed. No pending delivery exceptions are recorded.'};
}
