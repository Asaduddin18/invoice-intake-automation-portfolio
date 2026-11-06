function reportSummary(items) {
  if (!items.length||!items.every(x=>x.request_ok===true&&x.report_status==='verified'&&typeof x.month==='string'&&/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(x.month)&&Number.isSafeInteger(x.deliveries)&&x.deliveries>=0)) throw Error('Not all report results were verified. Inspect saved reports.');
  const seen=new Set();
  for(const item of items){if(seen.has(item.month))throw Error('Duplicate reporting month. Inspect workflow loop.');seen.add(item.month);}
  return {status:'completed',refreshed_months:items.length,verified_reports:items.map(x=>({month:x.month,deliveries:x.deliveries})),
    automatic_retry:false,operator_message:'All requested monthly reports were refreshed and read back. No accounting operation was requested.'};
}
