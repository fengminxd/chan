"""Minute replay of existing nested_1h policy; no changes to strategy modules.
Streaming merge/graph and range-query caches are checked against reference functions.
"""
from bisect import bisect_left
from collections import Counter
from dataclasses import replace, asdict
from datetime import timedelta
from pathlib import Path
import hashlib,json,sys,time
P=Path(__file__).resolve().parent; ROOT=P.parents[1];sys.path.insert(0,str(ROOT))
import entry_plan as ep
from chan import (read_csv,resample,parse_time,json_default,MergedBar,Fractal,Stroke,
                  merge_inclusions,find_fractals,build_strokes)
from chan_time import PERIODS,period_delta
from chan_theory import macd
START=parse_time('2026-09-17T12:00:00Z'); MIN=timedelta(minutes=1)
POL=ep.EntryPolicy(profile='nested_1h')
SOURCE=json.loads((P/'source.json').read_text()); END=parse_time(SOURCE['as_of'])
BARS=read_csv(P/'history_1m.csv'); TIMES=[x.time for x in BARS]
assert hashlib.sha256((P/'history_1m.csv').read_bytes()).hexdigest()==SOURCE['history_sha256']

def dump(name,x): (P/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=json_default))

class Range:
    def __init__(self,bars):
        self.n=1
        while self.n<len(bars):self.n*=2
        self.lo=[(float('inf'),0)]*(self.n*2);self.hi=[(-float('inf'),0)]*(self.n*2)
        for i,c in enumerate(bars):self.lo[self.n+i]=(c.low,-i);self.hi[self.n+i]=(c.high,i)
        for i in range(self.n-1,0,-1):self.lo[i]=min(self.lo[i*2:i*2+2]);self.hi[i]=max(self.hi[i*2:i*2+2])
    def query(self,l,r):
        assert 0<=l<r<=closed_count
        l+=self.n;r+=self.n;a=(float('inf'),0);b=(-float('inf'),0)
        while l<r:
            if l&1:a=min(a,self.lo[l]);b=max(b,self.hi[l]);l+=1
            if r&1:r-=1;a=min(a,self.lo[r]);b=max(b,self.hi[r])
            l//=2;r//=2
        return a,b
closed_count=len(BARS);RMQ=Range(BARS)

class Frame:
    def __init__(self,p):
        self.period=p;self.width=period_delta(PERIODS[p]);self.bars=[];self.merged=[];self.direction='up'
        self.fractals=[];self.parents=[];self.lengths=[];self.roots=[];self.best={};self.path=[]
        self.version=0;self.cache={};self.stroke_cache=None;self.last_key=None
    def append(self,c):
        self.bars.append(c);new=MergedBar(c.time,c.time,c.high,c.low,c.time,c.time)
        if not self.merged:self.merged.append(new);return
        old=self.merged[-1]
        contains=(old.high>=new.high and old.low<=new.low) or (new.high>=old.high and new.low<=old.low)
        if contains:
            h=new.high>=old.high if self.direction=='up' else new.high<=old.high
            l=new.low>=old.low if self.direction=='up' else new.low<=old.low
            self.merged[-1]=MergedBar(old.start,new.end,new.high if h else old.high,new.low if l else old.low,
                                     new.high_time if h else old.high_time,new.low_time if l else old.low_time)
        else:
            self.direction='up' if new.high>old.high else 'down';self.merged.append(new)
            i=len(self.merged)-3
            if i>=1:
                a,b,d=self.merged[i-1:i+2];f=None
                if b.high>max(a.high,d.high) and b.low>max(a.low,d.low):f=Fractal(i,'top',b.high,b.high_time)
                elif b.low<min(a.low,d.low) and b.high<min(a.high,d.high):f=Fractal(i,'bottom',b.low,b.low_time)
                if f:self.add_fractal(f)
    def add_fractal(self,f):
        i=len(self.fractals);low=self.merged[f.index].low;high=self.merged[f.index].high;pos=f.index;candidates=[]
        for j in range(i-1,-1,-1):
            a=self.fractals[j]
            for k in range(pos-1,a.index-1,-1):low=min(low,self.merged[k].low);high=max(high,self.merged[k].high)
            pos=a.index
            if (f.kind=='top' and high>f.price) or (f.kind=='bottom' and low<f.price):break
            if a.kind==f.kind or f.index-a.index<4:continue
            if (f.kind=='top' and a.price==low and a.price<f.price) or (f.kind=='bottom' and a.price==high and a.price>f.price):candidates.append(j)
        parent=min(candidates,key=lambda j:(self.roots[j],-self.lengths[j],-j)) if candidates else None
        self.fractals.append(f);self.parents.append(parent)
        self.lengths.append(self.lengths[parent]+1 if parent is not None else 0)
        self.roots.append(self.roots[parent] if parent is not None else i)
        if self.lengths[-1]:self.best[self.roots[-1]]=i
        self.version+=1
    def strokes(self):
        key=(self.version,self.merged[-1] if self.merged else None)
        if key==self.last_key:return self.stroke_cache
        if getattr(self, 'path_version', None) != self.version:
            path=[]
            if self.best:
                cursor=self.best[min(self.best)]
                while cursor is not None:path.append(self.fractals[cursor]);cursor=self.parents[cursor]
            self.path=path[::-1];self.path_version=self.version
        path=self.path
        self.stroke_cache=[Stroke(a,b,i==len(path)-2,
            self.merged[path[i+2].index+2].end+self.width if i<len(path)-2 else None)
            for i,(a,b) in enumerate(zip(path,path[1:]))]
        self.last_key=key;return self.stroke_cache
    def leg(self,s,i,now):
        key=(s.start,s.end,s.provisional,s.confirmed_at)
        if not s.provisional and key in self.cache:return self.cache[key]
        end,price=s.end.time,s.end.price;finish=min(now,end+self.width)
        if s.provisional:
            l=bisect_left(TIMES,s.start.time)
            if l<closed_count:
                low,high=RMQ.query(l,closed_count);idx=-low[1] if s.direction=='down' else high[1]
                end=TIMES[idx];price=low[0] if s.direction=='down' else high[0]
                midnight=end.replace(hour=0,minute=0,second=0,microsecond=0)
                finish=min(now,midnight+((end-midnight)//self.width+1)*self.width)
        if end<=s.start.time:return None
        l=bisect_left(TIMES,s.start.time);r=min(closed_count,bisect_left(TIMES,finish))
        if l>=r:return None
        low,high=RMQ.query(l,r);idx=-low[1] if s.direction=='down' else high[1]
        out=dict(index=i,start=s.start.time,end=end,finish=finish,start_price=s.start.price,price=price,
                 low=low[0],high=high[0],direction=s.direction,provisional=s.provisional,
                 focus_time=TIMES[idx],focus_price=low[0] if s.direction=='down' else high[0],evidence_at=s.confirmed_at)
        if not s.provisional:self.cache[key]=out
        return out
    def legs(self,now,earliest=None):
        ss=self.strokes();begin=0
        if earliest is not None:begin=bisect_left(ss,earliest,key=lambda s:s.start.time)
        result=[x for i in range(begin,len(ss)) if (x:=self.leg(ss[i],i,now))]
        last=result[-1] if result else self.leg(ss[-1],len(ss)-1,now) if ss else None
        if last:
            start,price=last['focus_time'],last['focus_price'];l=bisect_left(TIMES,start)+1
            if l<closed_count:
                low,high=RMQ.query(l,closed_count);down=last['direction']=='up';idx=-low[1] if down else high[1]
                end_price=low[0] if down else high[0]
                if (down and end_price<price) or (not down and end_price>price):
                    midnight=TIMES[idx].replace(hour=0,minute=0,second=0,microsecond=0)
                    finish=min(now,midnight+((TIMES[idx]-midnight)//self.width+1)*self.width)
                    ll,hh=RMQ.query(l-1,min(closed_count,bisect_left(TIMES,finish)))
                    if not ((down and ll[0]<end_price) or (not down and hh[0]>end_price)):
                        result.append(dict(index=last['index']+1,start=start,end=TIMES[idx],finish=finish,
                            start_price=price,price=end_price,low=min(price,ll[0]),high=max(price,hh[0]),
                            direction='down' if down else 'up',focus_time=TIMES[idx],focus_price=end_price,
                            provisional=True,live_reverse=True,evidence_at=None))
        return result

# Cache optimization retains exactly the provenance emitted by observation_legs.
_cached_leg = Frame.leg
def native_leg(self, stroke, index, now):
    out = _cached_leg(self, stroke, index, now)
    if out is None:
        return None
    return {**out, "native_structure": dict(
        kind="stroke", index=index, start_time=stroke.start.time,
        start_price=stroke.start.price, start_confirmed=True, endpoint_time=stroke.end.time,
        endpoint_price=stroke.end.price, observed_at=now,
        focus_matches_endpoint=(out["focus_price"] == stroke.end.price and
                                stroke.end.time <= out["focus_time"] < stroke.end.time+self.width))}
Frame.leg = native_leg
_original_legs = Frame.legs
def sourced_legs(self, now, earliest=None):
    result = _original_legs(self, now, earliest)
    if result and result[-1].get("live_reverse"):
        ss = self.strokes()
        last = result[-2] if len(result)>1 else self.leg(ss[-1], len(ss)-1, now)
        result[-1] = {**result[-1], "native_structure": ep.reverse_structure(last, now)}
    return result
Frame.legs = sourced_legs

ORIG_SETUP=ep.execution_setups;ORIG_STATUS=ep.setup_status;ORIG_ADVANCE=ep.advance_plan

def fast_setups(bars,interval,side,period,as_of,policy,initial_direction='up'):
    frame=FRAMES[period];merged=frame.merged;kind='bottom' if side=='long' else 'top';result=[]
    for f in reversed(frame.fractals):
        if f.time<interval['start']:break
        if f.kind!=kind or f.time>=interval['end']:continue
        if f.price!=interval['focus_price'] or not f.time<=interval['focus_time']<f.time+frame.width:continue
        triple=merged[f.index-1:f.index+2];evidence=merged[f.index+2].end+frame.width
        if evidence>as_of or triple[0].start<interval['start']:continue
        trigger=max(b.high for b in triple)+policy.trigger_buffer if side=='long' else min(b.low for b in triple)-policy.trigger_buffer
        stop=f.price-policy.stop_buffer if side=='long' else f.price+policy.stop_buffer
        if (side=='long' and trigger<=stop) or (side=='short' and trigger>=stop):continue
        result.append(dict(period=period,anchor_time=f.time,anchor_price=f.price,
            structure='stable_bottom_fractal' if side=='long' else 'stable_top_fractal',evidence_at=evidence,
            trigger_price=trigger,stop_price=stop,trigger_rule='1m_close_gt' if side=='long' else '1m_close_lt',
            id=ep._key([period,side,f.time,f.price,trigger,stop])))
    return result

def fast_status(setup,candidate,minutes):
    return ORIG_STATUS(setup,candidate,BARS[bisect_left(TIMES,setup['anchor_time']):closed_count])
def fast_advance(plan,bars,as_of,policy):
    return ORIG_ADVANCE(plan,BARS[bisect_left(TIMES,ep._time(plan['evaluated_at'])):closed_count],as_of,policy)
ep.execution_setups=fast_setups;ep.setup_status=fast_status;ep.advance_plan=fast_advance

class LazyFrames:
    def __init__(self,now,earliest):self.now=now;self.earliest=earliest;self.cache={}
    def __getitem__(self,p):
        if p not in self.cache:self.cache[p]=dict(bars=FRAMES[p].bars,legs=FRAMES[p].legs(self.now,self.earliest),initial_direction='up')
        return self.cache[p]

def validate(now,roots,main):
    checks=[]
    for p,f in FRAMES.items():
        merged=merge_inclusions(f.bars);assert merged==f.merged,(p,'merged')
        fs=find_fractals(merged[:-1]);assert fs==f.fractals,(p,'fractals')
        ss=build_strokes(fs,merged)
        ss=[replace(s,confirmed_at=merged[ss[i+1].end.index+2].end+f.width) if not s.provisional else s for i,s in enumerate(ss)]
        assert ss==f.strokes(),(p,'strokes')
        # Full reference observation legs are expensive for 1m: sample all relevant strokes with explicit index remapping.
        ss_check=ss if p=='1h' else ss[-12:]
        block={'strokes':ss_check,'period':p}
        reference=ep.observation_legs(block,BARS[:closed_count],now)
        offset=len(ss)-len(ss_check)
        for x in reference:
            x['index']+=offset
            x['native_structure']['index']+=offset
        ours=[x for i,s in enumerate(ss_check,offset) if (x:=f.leg(s,i,now))]
        assert reference==ours,(p,'legs')
        live=ep.developing_reverse(reference,BARS[:closed_count],now,p)
        all_ours=f.legs(now)
        ours_live=all_ours[-1] if all_ours and all_ours[-1].get('live_reverse') else None
        assert live==ours_live,(p,'live_reverse')
        for c in roots:
            for setup in fast_setups(f.bars,c['interval'],c['side'],p,now,POL):assert setup['evidence_at']<=now
            assert fast_setups(f.bars,c['interval'],c['side'],p,now,POL)==ORIG_SETUP(f.bars,c['interval'],c['side'],p,now,POL),(p,'setups')
        checks.append(dict(period=p,bars=len(f.bars),strokes=len(ss),reference_match=True))
    return dict(as_of=now,checks=checks)

FRAMES={p:Frame(p) for p in ep.CHAIN}
AGG={p:resample(BARS,PERIODS['1m'],PERIODS[p],END)[0] for p in ep.CHAIN}
MACD=macd(AGG['1h']);POS={p:0 for p in ep.CHAIN}
CHECK_TIMES={START,parse_time('2026-09-27T05:00:00Z'),parse_time('2026-09-27T14:00:00Z'),END}

def run():
    global closed_count
    before={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['entry_plan.py','chan.py','chan_theory.py','chan_structure.py']}
    plans=[];records={};candidate_records={};validations=[];counts=Counter();triggered=[];events=[];start_wall=time.monotonic();snapshots=0
    with (P/'events.jsonl').open('w') as output:
        for i,bar in enumerate(BARS):
            closed_count=i+1;now=bar.time+MIN
            for p,f in FRAMES.items():
                pos=POS[p]
                if pos<len(AGG[p]) and AGG[p][pos].time+f.width<=now:
                    f.append(AGG[p][pos]);POS[p]+=1
            if now<START:continue
            main=FRAMES['1h'];legs=main.legs(now)
            block={'strokes':main.strokes(),'period':'1h','theory':{'macd':{'points':MACD[:len(main.bars)]}}}
            sequences=[legs]+([legs[:-1]] if legs and legs[-1].get('live_reverse') else [])
            roots=[{**c,'entry_model':'nested_1h'} for seq in sequences for c in ep.hourly_candidates(block,seq,now)]
            old={x.get('candidate_id',x['id']):x for x in plans};new=[]
            for c in roots:
                key=ep._key([c['id'],c['candidate_time'],c['candidate_price']])
                if key not in candidate_records:candidate_records[key]={'first_seen':now,'candidate':c,'last_seen':now,'states':Counter()}
                r=candidate_records[key];r['last_seen']=now
                frames=LazyFrames(now,c['interval']['start'])
                loc=ep.locate(c,frames,now,POL,old.get(c['id']))
                plan=ep.make_plan(c,loc,FRAMES['1m'].bars,now,POL,old.get(c['id']))
                r['states'][plan['state']]+=1
                new.append(plan)
            active={x['id'] for x in new}
            withdrawn=[{**p,'state':'withdrawn','withdrawn_at':now,'actionable':False,'new_trigger':False,
                        'reason':'候选或执行结构已改变；旧计划不再有效'} for p in old.values() if p['id'] not in active]
            fresh=lambda p:p['state']=='triggered' and p['triggered_at']==now
            directions={p['side'] for p in new if p['state']=='armed' or fresh(p)};conflict=len(directions)>1
            changes=[]
            for p in new:
                p['actionable']=not conflict and fresh(p);p['new_trigger']=fresh(p)
                p['execution_group_id']=p['setup']['id'] if p.get('setup') else None
                counts[p['state']]+=1
                if p.get('setup'):
                    assert p['evidence_at']<=now
                    if p['id'] not in records:records[p['id']]={'first_seen':now,'first':p,'states':[]}
                    rec=records[p['id']]
                    if not rec['states'] or rec['states'][-1]['state']!=p['state']:
                        rec['states'].append({'time':now,'state':p['state']});changes.append(p)
                    rec['last']=p
                if fresh(p):triggered.append(p)
            for p in withdrawn:
                if p['id'] in records:records[p['id']]['withdrawals']=records[p['id']].get('withdrawals',[])+[now]
            if changes or withdrawn:output.write(json.dumps({'as_of':now,'changes':changes,'withdrawn':withdrawn},default=json_default,ensure_ascii=False)+'\n')
            plans=new;snapshots+=1
            if now in CHECK_TIMES:
                validations.append(validate(now,roots,main));print('REFERENCE VERIFIED',now,flush=True)
            if snapshots%1000==0 or now==END:
                print('REPLAY',snapshots,now,'signals',len(triggered),'plans',len(records),'seconds',round(time.monotonic()-start_wall,1),flush=True)
    after={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in before};assert before==after
    dump('replay_summary.json',dict(start=START,as_of=END,last_close=BARS[-1].close,snapshots=snapshots,
         warmup_start=BARS[0].time,policy=asdict(POL),state_counts=counts,trigger_count=len(triggered),
         actionable_count=sum(p['actionable'] for p in triggered),plan_count=len(records),
         candidate_observation_count=len(candidate_records),current_plans=plans,code_sha256=before,
         validation=validations,core_code_unchanged=True))
    dump('triggers.json',triggered);dump('plans.json',list(records.values()));dump('candidates.json',list(candidate_records.values()))
    print('COMPLETE',len(triggered),flush=True)
if __name__=='__main__':run()
