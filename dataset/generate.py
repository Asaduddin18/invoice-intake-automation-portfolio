"""Recreate 50 original synthetic PDFs and ground truth. Python 3 + reportlab."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
import json, csv, copy
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle

ROOT=Path(__file__).resolve().parent
D=Decimal
money=lambda x: str(D(str(x)).quantize(D('.01'),rounding=ROUND_HALF_UP))
SUPPLIERS=[('Alder Packing Supplies Ltd','alder.example','Packaging materials'),('Bracken Industrial Components Ltd','bracken.example','Industrial components'),('Cedar Facilities Services Ltd','cedar.example','Facilities services'),('Dovetail Trade Supplies Ltd','dovetail.example','Unknown supplier')]
PRODUCTS=[('Double-wall shipping cartons, pack of 25','pack','24.75'),('Warehouse handling gloves, size L','pair','6.40'),('Machine coupling replacement, specification M24','unit','38.95'),('Preventive equipment inspection, October service','hour','65.00'),('Recycled pallet wrap, 400 mm x 250 m','roll','12.35'),('Storage bin labels, printed batch of 100','batch','18.20')]
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='TinyDemo',fontName='Helvetica',fontSize=8,leading=11))
styles.add(ParagraphStyle(name='MoneyDemo',fontName='Helvetica',fontSize=9,leading=12,alignment=TA_RIGHT))

def make(i):
 s=(i-1)%3; name,domain,category=SUPPLIERS[s]
 n=1+(i%5)
 if i in (9,24,39): n=28
 lines=[]
 rate=D('0.00') if i%7==0 else D('.20')
 for k in range(n):
  catalog = {0: [PRODUCTS[0],PRODUCTS[4],PRODUCTS[5]], 1: [PRODUCTS[1],PRODUCTS[2],('M12 zinc-plated fasteners, box of 100','box','14.85')], 2: [PRODUCTS[3],('Warehouse cleaning, scheduled service','hour','28.50'),('Equipment safety inspection, itemised service','visit','115.00')]}[s]
  desc,unit,price=catalog[(i+k)%len(catalog)]
  q=D('1.5') if i%6==0 and k==0 else D(str(1+(i+k)%9))
  amount=(q*D(price)).quantize(D('.01'),rounding=ROUND_HALF_UP)
  lines.append(dict(description=desc,quantity=str(q),unit=unit,unit_price=money(price),line_amount=money(amount),tax_rate=str(rate)))
 sub=sum(D(x['line_amount']) for x in lines); tax=(sub*rate).quantize(D('.01'),rounding=ROUND_HALF_UP)
 dt=date(2026,8,1)+timedelta(days=i*2)
 return dict(id=f'INV-{i:03}',filename=f'invoice_{i:03}.pdf',split='evaluation' if i>=31 else 'tuning',scenario='valid',supplier=dict(name=name,email=f'accounts@{domain}',address=f'{10+s} Fictional Trade Park, Demo Borough, United Kingdom',known=True,category=category),buyer=dict(name='Northmere Distribution Ltd',email='finance@northmere.example',address='50 Example Distribution Park, Demo Borough, United Kingdom'),invoice_number=f'{"AP BC CF".split()[s]}-2026-{1000+i}',invoice_date=dt.isoformat(),due_date=(dt+timedelta(days=[14,30,45][s])).isoformat(),currency='GBP',purchase_order_reference=f'PO-{7000+i}' if i%3 else None,line_items=lines,subtotal=money(sub),tax=money(tax),tax_rate=str(rate),displayed_total=money(sub+tax),calculated_total=money(sub+tax),layout=s+1,date_format=['%d/%m/%Y','%d %b %Y','%Y-%m-%d'][i%3],expected_validation=dict(outcome='eligible_for_draft',reason='Known supplier; complete fields; totals agree. Review setting determines approval requirement.'),duplicate_of=None,source_document_type='supplier_invoice',notes='Payment due under agreed trade terms. No bank details supplied.')

records=[make(i) for i in range(1,51)]
# 34 valid originals, 4 missing fields, 4 arithmetic errors, 2 unknowns, 6 duplicates.
for i,field in [(13,'invoice_number'),(14,'supplier'),(33,'due_date'),(34,'invoice_date')]:
 r=records[i-1];r[field]=None;r['scenario']='missing_'+field;r['expected_validation']=dict(outcome='hold',reason=f'Missing {field}; do not invent a value.')
for i,field in [(15,'displayed_total'),(16,'tax'),(35,'subtotal'),(36,'displayed_total')]:
 r=records[i-1];r[field]=money(D(r[field])+D('7.13'));r['scenario']='inconsistent_'+field;r['expected_validation']=dict(outcome='hold',reason=f'Printed {field} disagrees with independently calculated line arithmetic.')
for i in (17,37):
 r=records[i-1];name,domain,category=SUPPLIERS[3];r['supplier']=dict(name=name,email=f'accounts@{domain}',address='88 Fictional Supply Estate, Demo Borough, United Kingdom',known=False,category=category);r['scenario']='unknown_supplier';r['expected_validation']=dict(outcome='hold',reason='Supplier is outside seeded mapping; correct extraction alone does not authorize posting.')
for dest,src,changed in [(28,1,False),(29,2,True),(30,3,True),(48,31,False),(49,32,True),(50,38,True)]:
 r=copy.deepcopy(records[src-1]);r.update(id=f'INV-{dest:03}',filename=f'invoice_{dest:03}_resubmitted.pdf',scenario='duplicate_relayout' if changed else 'duplicate_renamed',duplicate_of=records[src-1]['id'])
 if changed:r['layout']=r['layout']%3+1;r['date_format']='%d %B %Y';r['notes']='Copy supplied again for accounts processing. No bank details supplied.'
 r['expected_validation']=dict(outcome='duplicate_if_original_processed',reason='Same supplier and invoice number as the referenced original. If this arrives first, process once and hold the later copy.')
 records[dest-1]=r

def render(r,path):
 layout=r['layout']; accent=[colors.HexColor('#183b50'),colors.HexColor('#315442'),colors.HexColor('#6a4734')][layout-1]
 def P(t,style='BodyText'):return Paragraph(str(t),styles[style])
 def fmt(v):return date.fromisoformat(v).strftime(r['date_format']) if v else '[not supplied]'
 supplier=r['supplier']; story=[]
 title=['SUPPLIER INVOICE','INVOICE / ACCOUNT CHARGES','TRADE INVOICE'][layout-1]
 story += [P(title,'Title'),Spacer(1,10)]
 sup=P((supplier['name']+'<br/>'+supplier['address']+'<br/>'+supplier['email']) if supplier else 'Supplier identity: [not supplied]')
 buyer=P('BILL TO<br/>'+r['buyer']['name']+'<br/>'+r['buyer']['address']+'<br/>'+r['buyer']['email'])
 meta=P(f"{'Invoice no.' if layout!=2 else 'Document reference'}: {r['invoice_number'] or '[not supplied]'}<br/>Issued: {fmt(r['invoice_date'])}<br/>Due: {fmt(r['due_date'])}<br/>Currency: GBP<br/>Customer PO: {r['purchase_order_reference'] or 'Not quoted'}")
 blocks=[sup,meta,buyer] if layout==1 else ([meta,buyer,sup] if layout==2 else [buyer,sup,meta])
 for b in blocks:story.extend([b,Spacer(1,12)])
 # Different ordering and labels require semantic rather than positional extraction.
 heads= ['Description','Qty / unit','Unit GBP','Net GBP'] if layout==1 else (['Net GBP','Item / service','Rate GBP','Quantity'] if layout==2 else ['Units','Description of supply','Line GBP','Price GBP'])
 header_style=ParagraphStyle('HeaderDemo',parent=styles['TinyDemo'],textColor=colors.white)
 rows=[[Paragraph(x,header_style) for x in heads]]
 for x in r['line_items']:
  vals=[x['description'],x['quantity']+' '+x['unit'],x['unit_price'],x['line_amount']]
  if layout==2:vals=[vals[3],vals[0],vals[2],vals[1]]
  if layout==3:vals=[vals[1],vals[0],vals[3],vals[2]]
  rows.append([P(v,'TinyDemo') for v in vals])
 widths=[260,80,80,80] if layout==1 else ([80,260,80,80] if layout==2 else [80,260,80,80])
 table=Table(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
 table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),accent),('TEXTCOLOR',(0,0),(-1,0),colors.white),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#f1f4f6'),colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),1,accent)]))
 # Paragraph color must be set separately for headers.

 story.extend([table,Spacer(1,15),P(f"Net subtotal: GBP {r['subtotal']}<br/>Tax ({D(r['tax_rate'])*100:g}% of net, exclusive): GBP {r['tax']}<br/><b>Total payable: GBP {r['displayed_total']}</b>")])
 story.extend([Spacer(1,12),P(r['notes'],'TinyDemo'),P('All names, contacts, addresses and transactions are fictional. Tax rates are test assumptions.','TinyDemo')])
 def footer(c,d):
  c.setFont('Helvetica',8);c.setFillColor(colors.HexColor('#555555'));c.drawString(45,28,'SYNTHETIC DEMO INVOICE | Not a commercial or tax document');c.drawRightString(550,28,f'Page {d.page}')
 SimpleDocTemplate(str(path),pagesize=(595.28,841.89),leftMargin=45,rightMargin=45,topMargin=35,bottomMargin=48).build(story,onFirstPage=footer,onLaterPages=footer)

for r in records:
 folder=ROOT/r['split'];folder.mkdir(exist_ok=True)
 render(r,folder/r['filename'])
 (folder/(Path(r['filename']).stem+'.json')).write_text(json.dumps(r,indent=2)+'\n')
(ROOT/'ground_truth.json').write_text(json.dumps(records,indent=2)+'\n')
with (ROOT/'manifest.csv').open('w',newline='') as f:
 fields=['id','filename','split','scenario','layout','invoice_number','displayed_total','duplicate_of'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:r[k] for k in fields} for r in records)
(ROOT/'supplier_master.json').write_text(json.dumps([dict(name=n,email=f'accounts@{d}',category=c,known=i<3) for i,(n,d,c) in enumerate(SUPPLIERS)],indent=2)+'\n')
print('Generated',len(records),'PDFs with matching JSON.')
