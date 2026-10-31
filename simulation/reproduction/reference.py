"""Digitize vector lines, not estimates read off a screenshot or author CSV."""
import hashlib
from pathlib import Path
import fitz

RATIOS=[1,2,3,4,5,6,7,8,9,10,12,13,14,15,16,17,18,19,20,22.5,25,27.5,30,35,40,50,60,70]
COLORS={'fast_raw':(.537255,.305882,.835294),'fast_all':(0,.082353,1),'ideal':(0,0,0),'spreadout':(1,0,.631373)}


def decode_line(points,box,xrange,yrange):
    x0,y0,x1,y1=box
    if x1<=x0 or y1<=y0:raise ValueError('invalid axis rectangle')
    return [(xrange[0]+(x-x0)/(x1-x0)*(xrange[1]-xrange[0]),
        yrange[0]+(y1-y)/(y1-y0)*(yrange[1]-yrange[0])) for x,y in points]


def extract(pdf):
    pdf=Path(pdf);doc=fitz.open(pdf)
    pages=[p for p in doc if 'Figure 17:' in p.get_text()]
    if len(pages)!=1:raise ValueError('paper Figure17 page not uniquely identified')
    page=pages[0];drawings=page.get_drawings();boxes=set()
    for d in drawings:
        r=d['rect']
        if d.get('color')==(0,0,0) and r.width>50 and r.height>40 and r.y1<200 and len(d['items'])<=4:
            boxes.add(tuple(round(v,4) for v in r))
    axes=sorted(boxes)
    if len(axes)!=2:raise ValueError('paper axis rectangles not uniquely identified')
    result=dict(pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),page_index=page.number,
        axes=[list(v) for v in axes],scale={},ratio={},clipped_non_grid=[],reference_type='PDF vector digitization; not author raw data')
    for panel,box,xrange,yrange,grid in [('scale',axes[0],(16,320),(0,80),list(range(16,321,16))),
                                      ('ratio',axes[1],(1,70),(0,1.4),RATIOS)]:
        for name,color in COLORS.items():
            if panel=='ratio' and name=='fast_all':continue
            matches=[]
            for d in drawings:
                r=d['rect'];c=d.get('color')
                if c is None or sum(abs(c[k]-color[k]) for k in range(3))>.02:continue
                if len(d['items'])<15 or r.x0<box[0]-.01 or r.x1>box[2]+.01 or r.y0<box[1]-.01 or r.y1>box[3]+.01:continue
                if any(item[0]!='l' for item in d['items']):continue
                matches.append(d)
            if len(matches)!=1:raise ValueError(f'curve {panel}/{name} not unique')
            items=matches[0]['items'];points=[tuple(items[0][1]),*[tuple(it[2]) for it in items]]
            values=decode_line(points,box,xrange,yrange);decoded=[]
            for (x,y),(px,py) in zip(values,points):
                near=min(grid,key=lambda k:abs(k-x))
                if abs(near-x)>.03:
                    result['clipped_non_grid'].append(dict(panel=panel,curve=name,x=x,y=y,pdf_x=px,pdf_y=py));continue
                decoded.append(dict(x=near,y=y,pdf_x=px,pdf_y=py))
            if len({v['x'] for v in decoded})!=len(decoded):raise ValueError('duplicate digitized point')
            result[panel][name]=decoded
    return result
