// Coordinates always remain X=east, Y=north; length/width are user-facing names.
export function layoutToForm(l) {
  const axis=l.length_axis||'ew';
  return {...l,length_axis:axis,planting_direction:l.planting_direction||'ns',
    collection_lane_count:l.collection_lane_count??0,collection_direction:l.collection_direction||'ns',
    length_value:axis==='ew'?l.width:l.height,width_value:axis==='ew'?l.height:l.width};
}
export function formToLayout(v) {
  return {template:v.template,width:v.length_axis==='ew'?v.length_value:v.width_value,
    height:v.length_axis==='ew'?v.width_value:v.length_value,calibrated:v.calibrated,
    aisle_side:v.aisle_side,aisle_width:v.aisle_width,bays:v.bays,length_axis:v.length_axis,
    planting_direction:v.planting_direction,collection_lane_count:v.collection_lane_count,
    collection_direction:v.collection_direction};
}
export function layoutGeometry(l) {
  const w=l.width,h=l.height,a=l.aisle_width;
  let aisle;
  if(l.aisle_side==='west')aisle={x:0,y:0,w:a,h};
  else if(l.aisle_side==='east')aisle={x:w-a,y:0,w:a,h};
  else aisle={x:0,y:l.aisle_side==='south'?0:l.aisle_side==='north'?h-a:(h-a)/2,w,h:a};
  let regions;
  if(l.aisle_side==='center')regions=[{x:0,y:0,w,h:(h-a)/2},{x:0,y:(h+a)/2,w,h:(h-a)/2}];
  else if(l.aisle_side==='west')regions=[{x:a,y:0,w:w-a,h}];
  else if(l.aisle_side==='east')regions=[{x:0,y:0,w:w-a,h}];
  else regions=[{x:0,y:l.aisle_side==='south'?a:0,w,h:h-a}];
  const ns=(l.collection_direction||'ns')==='ns', lanes=[];
  const low=Math.min(...regions.map(r=>ns?r.x:r.y));
  const extent=regions.reduce((sum,r)=>sum+(ns?r.w:r.h),0);
  // North/south lanes split by a central cross-aisle share one lane number.
  const total=ns&&regions.length===2?w:extent;
  for(let i=0;i<(l.collection_lane_count||0);i++){
    let pos=(i+1)*total/(l.collection_lane_count+1)+low;
    if(!ns&&regions.length===2&&pos>regions[0].h)pos+=a;
    const segments=regions.filter(r=>ns?pos>=r.x&&pos<=r.x+r.w:pos>=r.y&&pos<=r.y+r.h).map(r=>ns?
      {x1:pos,y1:r.y,x2:pos,y2:r.y+r.h}:{x1:r.x,y1:pos,x2:r.x+r.w,y2:pos});
    lanes.push({number:i+1,segments});
  }
  return {aisle,lanes};
}
