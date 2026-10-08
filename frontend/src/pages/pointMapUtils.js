export function newPointId() {
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  bytes[6]=(bytes[6]&15)|64; bytes[8]=(bytes[8]&63)|128;
  const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

export function nextName(name, existing, increment=true) {
  const value=(name||'A1').trim();const match=value.match(/^(.*?)(\d+)$/);
  if(!match)return existing.includes(value)?`${value}1`:value;
  const prefix=match[1],width=match[2].length;
  let n=BigInt(match[2])+(increment?1n:0n);
  let candidate=prefix+n.toString().padStart(width,'0');
  while(existing.includes(candidate)){n+=1n;candidate=prefix+n.toString().padStart(width,'0');}
  return candidate;
}

export function editableDocument(doc) {
  const fields=['id','point_name','zone','x','y','note','sample_seconds','collect_soil','collect_environment','take_photo','enabled'];
  return {revision:doc.revision,layout:doc.layout,defaults:doc.defaults,next_name:doc.next_name,
    points:doc.points.map(p=>Object.fromEntries(fields.map(k=>[k,p[k]])))};
}

export function matchVisit(points,visit) {
  if(!visit?.mapping_id)return null;
  return points.find(p=>p.binding?.mapping_id===visit.mapping_id &&
    p.binding.nav_point_code===visit.nav_point_code) || null;
}

export function staleArrival(visit,now) {
  const stamp=Date.parse(visit?.arrived_at);
  return !Number.isFinite(stamp)||now-stamp>60000||stamp-now>5000;
}
