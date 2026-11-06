function reportItems(input) {
  const months=input.available_report_months;
  if (!Array.isArray(months)||!months.every(m=>typeof m==='string'&&/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(m))) throw Error('Reporting month list is invalid. Inspect saved status.');
  const unique=[...new Set(months)].sort();
  return unique.length ? unique.map(month=>({json:{month,has_month:true}})) : [{json:{status:'nothing_to_report',has_month:false,operator_message:'No saved reporting months exist. No report API call was made.'}}];
}
