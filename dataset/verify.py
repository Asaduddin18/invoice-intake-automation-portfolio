"""Independent arithmetic, PDF text, split and rendering checks. Needs pdfplumber, Pillow, pypdfium2."""
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import json, argparse
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageDraw
ROOT=Path(__file__).resolve().parent
ap=argparse.ArgumentParser();ap.add_argument('--render-dir',type=Path,required=True);args=ap.parse_args();args.render_dir.mkdir(parents=True,exist_ok=True)
rs=json.loads((ROOT/'ground_truth.json').read_text());assert len(rs)==50
ids={r['id']:r for r in rs};pages=[]
D=Decimal;quant=lambda x:x.quantize(D('.01'),rounding=ROUND_HALF_UP)
for r in rs:
 p=ROOT/r['split']/r['filename'];sub=sum(quant(D(x['quantity'])*D(x['unit_price'])) for x in r['line_items']);tax=quant(sub*D(r['tax_rate']));assert str(quant(sub+tax))==r['calculated_total']
 for x in r['line_items']:assert quant(D(x['quantity'])*D(x['unit_price']))==D(x['line_amount'])
 if not r['scenario'].startswith('inconsistent'):
  assert sub==D(r['subtotal']) and tax==D(r['tax']) and sub+tax==D(r['displayed_total'])
 else:assert sub!=D(r['subtotal']) or tax!=D(r['tax']) or sub+tax!=D(r['displayed_total'])
 if r['duplicate_of']:
  original=ids[r['duplicate_of']];assert original['split']==r['split'];assert original['invoice_number']==r['invoice_number'];assert original['supplier']==r['supplier'];assert original['line_items']==r['line_items']
 with pdfplumber.open(p) as pdf:
  txt='\n'.join(page.extract_text() or '' for page in pdf.pages)
  for v in [r['invoice_number'],r['subtotal'],r['tax'],r['displayed_total'],r['supplier']['name'] if r['supplier'] else None]:
   if v:assert v in txt,(p,v)
  for x in r['line_items']:assert x['description'] in txt,(p,x['description'])
  for page in pdf.pages:
   assert 'SYNTHETIC DEMO INVOICE' in (page.extract_text() or '')
   for char in page.chars:assert 25<=char['x0'] and char['x1']<=575 and char['top']>=20 and char['bottom']<=825,(p,char)
 doc=pdfium.PdfDocument(str(p))
 for j in range(len(doc)):
  doc[j].render(scale=1.1).to_pil().save(args.render_dir/f"{r['id']}-{j+1}.png")
 doc.close()
 pages.extend(sorted(args.render_dir.glob(r['id']+'-*.png')))
for batch in range(0,len(pages),12):
 group=pages[batch:batch+12];sheet=Image.new('RGB',(1200,4*445),'#d9dde0');draw=ImageDraw.Draw(sheet)
 for j,p in enumerate(group):
  im=Image.open(p);im.thumbnail((380,415));x=(j%3)*400;y=(j//3)*445;sheet.paste(im,(x,y+22));draw.text((x+5,y+3),p.name,fill='black')
 sheet.save(args.render_dir/f'contact-{batch//12+1}.jpg')
report={'pdf_count':len(rs),'page_count':len(pages),'arithmetic':'passed','searchable_text_and_expected_fields':'passed','page_bounds':'passed','duplicate_split_isolation':'passed','visual_review':'Contact sheets require manual inspection; see README for final review record.'}
(ROOT/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
