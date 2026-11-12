"""Evaluate verified application scores without silently changing operating settings."""
import argparse
import csv
import json
import math
from pathlib import Path

def wilson_upper(errors,total,z=1.96):
    p=errors/total
    return (p+z*z/(2*total)+z*math.sqrt(p*(1-p)/total+z*z/(4*total*total)))/(1+z*z/total)

def evaluate(rows,target_far):
    genuine=[float(r['score']) for r in rows if r['label']=='genuine']
    impostor=[float(r['score']) for r in rows if r['label']=='impostor']
    if not genuine or not impostor:raise ValueError('Both genuine and impostor track scores are required.')
    if any(not math.isfinite(v) or not -1<=v<=1 for v in genuine+impostor):raise ValueError('Cosine scores must be finite values between -1 and 1.')
    sweep=[]
    for threshold in [round(.4+i*.01,2) for i in range(56)]:
        false_accepts=sum(s>=threshold for s in impostor)
        false_rejects=sum(s<threshold for s in genuine)
        sweep.append({'threshold':threshold,'false_accepts':false_accepts,'false_rejects':false_rejects,
                      'far':false_accepts/len(impostor),'frr':false_rejects/len(genuine),
                      'far_wilson_upper_95':wilson_upper(false_accepts,len(impostor))})
    eligible=[r for r in sweep if r['far_wilson_upper_95']<=target_far]
    chosen=min(eligible,key=lambda r:(r['frr'],r['threshold'])) if eligible else None
    return {'genuine_tracks':len(genuine),'impostor_tracks':len(impostor),'target_far':target_far,
            'recommended_threshold':chosen['threshold'] if chosen else None,
            'reason':'A score-only operating candidate; validate all other gates separately.' if chosen else 'No threshold is supported at the requested false-acceptance bound. Collect more independent tracks or improve discrimination.',
            'caution':'Intervals assume independent trials. This does not measure liveness, quality, margin, tracking, or attendance performance. Settings were not changed.',
            'sweep':sweep}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scores',type=Path);parser.add_argument('--target-far',type=float,default=.001);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 0<args.target_far<1:parser.error('--target-far must be between 0 and 1')
    try:
        with args.scores.open(newline='',encoding='utf-8-sig') as f:
            rows=list(csv.DictReader(f))
        if any(r.get('label') not in ('genuine','impostor') or 'score' not in r for r in rows):raise ValueError('Use CSV columns label,score; labels genuine or impostor.')
        report=evaluate(rows,args.target_far)
    except (ValueError,OSError,KeyError) as exc:parser.error(str(exc))
    output=json.dumps(report,indent=2)
    if args.output:args.output.write_text(output,encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sweep'},indent=2))

if __name__=='__main__':main()
