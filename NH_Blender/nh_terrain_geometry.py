"""Adaptive convex terrain decomposition with measured vertical error.

Start with exact triangle prisms. Merge neighbouring footprints only when the
result is convex, does not bridge holes/layers, meets the triangle budget and
its final upper surface stays within both user tolerances.
"""
import heapq
import math
import bmesh
from mathutils import Vector

_EPS=1e-7

def _key(p):
    return tuple(round(float(v),6) for v in p)

def _area(poly):
    return abs(sum(p[0]*poly[(i+1)%len(poly)][1]-poly[(i+1)%len(poly)][0]*p[1] for i,p in enumerate(poly)))*.5

def _bounds(poly):
    return min(p[0] for p in poly),max(p[0] for p in poly),min(p[1] for p in poly),max(p[1] for p in poly)

def _overlap(a,b):
    return a[0]<=b[1]+_EPS and b[0]<=a[1]+_EPS and a[2]<=b[3]+_EPS and b[2]<=a[3]+_EPS

def _plane(tri):
    a,b,c=tri
    n=(b-a).cross(c-a)
    if abs(n.z)<1e-12: return None
    return (-n.x/n.z,-n.y/n.z,n.dot(a)/n.z)

def _z(plane,p):
    return plane[0]*p[0]+plane[1]*p[1]+plane[2]

def _clip(subject,clip):
    # Both polygons are convex; preserve all intersection vertices for extrema.
    if sum(p[0]*clip[(i+1)%len(clip)][1]-clip[(i+1)%len(clip)][0]*p[1] for i,p in enumerate(clip))<0:
        clip=list(reversed(clip))
    output=list(subject)
    for i,a in enumerate(clip):
        b=clip[(i+1)%len(clip)];source=output;output=[]
        if not source: break
        def side(p): return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
        prev=source[-1];dp=side(prev)
        for cur in source:
            dc=side(cur)
            if (dc>=-_EPS)!=(dp>=-_EPS):
                denominator=dp-dc
                if abs(denominator)>1e-20:
                    t=dp/denominator
                    output.append((prev[0]+t*(cur[0]-prev[0]),prev[1]+t*(cur[1]-prev[1])))
            if dc>=-_EPS: output.append((cur[0],cur[1]))
            prev,dp=cur,dc
    return output

def _hull(points,thickness):
    # Center coordinates before BMesh to retain precision far from scene origin.
    origin=sum(points,Vector())/len(points)
    unique=list(dict.fromkeys(tuple(float(x) for x in p-origin) for p in points))
    bm=bmesh.new()
    try:
        seeds=[bm.verts.new(p) for p in unique]
        seeds.extend(bm.verts.new((p[0],p[1],p[2]-thickness)) for p in unique)
        bmesh.ops.convex_hull(bm,input=seeds,use_existing_faces=False)
        unused=[v for v in bm.verts if not v.link_faces]
        if unused:bmesh.ops.delete(bm,geom=unused,context='VERTS')
        bmesh.ops.dissolve_limit(bm, angle_limit=1e-5, verts=list(bm.verts), edges=list(bm.edges), use_dissolve_boundaries=False)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=1e-6)
        bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
        bmesh.ops.triangulate(bm,faces=list(bm.faces))
        bm.normal_update();bm.verts.index_update()
        vertices=[v.co.copy()+origin for v in bm.verts]
        faces=[[v.index for v in f.verts] for f in bm.faces]
        upper=[]
        for f in bm.faces:
            if f.normal.z>1e-7:
                tri=[v.co.copy()+origin for v in f.verts]
                xy=[(float(p.x),float(p.y)) for p in tri]
                plane=_plane(tri)
                if plane is not None and _area(xy)>1e-12:
                    upper.append((xy,plane,_bounds(xy)))
        if not upper or abs(bm.calc_volume())<1e-12:return None
        # Reject numerical slivers/non-convex results rather than committing a
        # malformed component. Use final world coordinates, as QA/export do.
        for face in faces:
            a,b,c=(vertices[i] for i in face)
            normal=(b-a).cross(c-a)
            if normal.length<1e-10:return None
            normal.normalize()
            if any(normal.dot(p-a)>2e-5 for p in vertices):return None
        return vertices,faces,upper
    finally:bm.free()

def _error(hull,triangles,boundary=()):
    minimum=math.inf;maximum=-math.inf;worst_under=None
    edge_min=math.inf;edge_max=-math.inf
    for tri,plane,bbox in triangles:
        xy=[(float(p.x),float(p.y)) for p in tri]
        covered=0.0
        for top,top_plane,top_box in hull[2]:
            if not _overlap(bbox,top_box):continue
            poly=_clip(xy,top)
            if len(poly)<3:continue
            covered+=_area(poly)
            for p in poly:
                source_z=_z(plane,p);error=_z(top_plane,p)-source_z
                if error<minimum:
                    minimum=error;worst_under=Vector((p[0],p[1],source_z))
                maximum=max(maximum,error)
                for i,a in enumerate(boundary):
                    b=boundary[(i+1)%len(boundary)]
                    length=math.hypot(b[0]-a[0],b[1]-a[1])
                    cross=abs((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]))
                    if cross<=1e-6*max(length,1e-6):
                        edge_min=min(edge_min,error);edge_max=max(edge_max,error)
                        break
        if covered<_area(xy)-max(1e-7,_area(xy)*1e-5):return None
    return minimum,maximum,worst_under,edge_min,edge_max

def _surface_record(points):
    points=tuple(Vector(p) for p in points)
    plane=_plane(points)
    if plane is None:return None
    return points,plane,_bounds(points)

def _crossing_surfaces(left,right):
    for tri,plane,bbox in left:
        xy=[(p.x,p.y) for p in tri]
        for other,other_plane,other_box in right:
            if not _overlap(bbox,other_box):continue
            poly=_clip(xy,[(p.x,p.y) for p in other])
            # Positive XY overlap is a duplicate or another surface, not a
            # shared boundary. Neither may be combined by area bookkeeping.
            if len(poly)>=3 and _area(poly)>1e-7:return True
    return False


def _final_convex(vertices,faces):
    diagonal=(Vector(tuple(max(p[i] for p in vertices) for i in range(3)))-
              Vector(tuple(min(p[i] for p in vertices) for i in range(3)))).length
    # Recheck after the float32 vertical shift, using the same tolerance as QA.
    tolerance=max(2e-5,diagonal*1e-6)
    for face in faces:
        a,b,c=(vertices[i] for i in face)
        normal=(b-a).cross(c-a).normalized()
        if any(normal.dot(p-a)>tolerance for p in vertices):return False
    return True

def _candidate(left,right,above,below,thickness,max_triangles,patch_size):
    from .nh_collider import _fake_terrain_convex_hull_xy
    if _crossing_surfaces(left['triangles'],right['triangles']):return None
    triangles=left['triangles']+right['triangles']
    points=list({_key(p):p for tri,_,_ in triangles for p in tri}.values())
    bbox=_bounds(points)
    if max(bbox[1]-bbox[0],bbox[3]-bbox[2])>patch_size+1e-5:return None
    footprint=_fake_terrain_convex_hull_xy([(p.x,p.y) for p in points])
    total_area=left['area']+right['area']
    if abs(_area(footprint)-total_area)>max(1e-7,total_area*1e-6):return None
    # Preserve the footprint while adding only the upper support points needed
    # by the error test. This avoids copying visual triangulation into colliders.
    by_xy={}
    for p in points:
        key=(round(p.x,6),round(p.y,6))
        if key not in by_xy or by_xy[key].z<p.z:by_xy[key]=p
    selected=[by_xy[(round(x,6),round(y,6))] for x,y in footprint]
    selected.append(max(points,key=lambda p:p.z))
    selected=list({_key(p):p for p in selected}.values())
    for _ in range(max_triangles):
        hull=_hull(selected,thickness)
        if hull is None or len(hull[1])>max_triangles:return None
        error=_error(hull,triangles,footprint)
        if error is None:return None
        low,high,worst,edge_low,edge_high=error
        # A single vertical shift can use the allowance on either side. The
        # chosen shift has the smallest magnitude that meets both limits.
        shift_min=-below-low;shift_max=above-high
        # Each side contributes at most half of the smaller height allowance
        # at a seam. Thus neighbouring slabs cannot create a 2*tolerance step.
        seam_half=min(above,below)*.5
        shift_min=max(shift_min,-seam_half-edge_low)
        shift_max=min(shift_max,seam_half-edge_high)
        if shift_min<=shift_max+1e-6:
            shift=min(max(0.0,shift_min),shift_max)
            vertices=[p+Vector((0,0,shift)) for p in hull[0]]
            if not _final_convex(vertices,hull[1]):return None
            return dict(vertices=vertices,faces=hull[1],triangles=triangles,area=total_area,
                        above=max(0.0,high+shift),below=max(0.0,-low-shift),
                        boundary_error=max(abs(edge_low+shift),abs(edge_high+shift)))
        if worst is None or min((p-worst).length for p in selected)<1e-5:return None
        selected.append(worst)
    return None

def build_terrain(triangles,*,patch_size,min_patch_size,depression_error,hill_error,thickness,max_triangles=32):
    from .nh_collider import _fake_terrain_clip_polygon_to_rect_xy
    if not triangles:raise RuntimeError('No selected terrain triangles')
    patch_size=max(.25,float(patch_size));thickness=max(.05,float(thickness))
    above=max(0,float(depression_error));below=max(0,float(hill_error))
    max_triangles=max(8,int(max_triangles))
    # min_patch_size remains accepted for older scripts. Accuracy is now
    # guaranteed by exact triangle leaves, so no inaccurate minimum cell is kept.
    del min_patch_size
    min_x=min(p.x for t in triangles for p in t['points']);min_y=min(p.y for t in triangles for p in t['points'])
    nodes={};edge_owners={};next_id=0;seen=set();ignored_vertical=0
    for entry in triangles:
        tri=tuple(Vector(p) for p in entry['points']);key=tuple(sorted(_key(p) for p in tri))
        if key in seen:continue
        seen.add(key)
        if _plane(tri) is None or _area(tri)<1e-10:
            ignored_vertical+=1;continue
        box=_bounds(tri)
        ix0=int(math.floor((box[0]-min_x)/patch_size));ix1=int(math.floor((box[1]-min_x)/patch_size))
        iy0=int(math.floor((box[2]-min_y)/patch_size));iy1=int(math.floor((box[3]-min_y)/patch_size))
        if (ix1-ix0+1)*(iy1-iy0+1)>100000:raise RuntimeError('Patch Size would create too many terrain cells; increase it')
        for ix in range(ix0,ix1+1):
            for iy in range(iy0,iy1+1):
                x=min_x+ix*patch_size;y=min_y+iy*patch_size
                polygon=_fake_terrain_clip_polygon_to_rect_xy(tri,x,x+patch_size,y,y+patch_size)
                polygon=list(polygon)
                changed=True
                while changed and len(polygon)>3:
                    changed=False
                    for j,p in enumerate(polygon):
                        a,b=polygon[j-1],polygon[(j+1)%len(polygon)]
                        cross=abs((p.x-a.x)*(b.y-p.y)-(p.y-a.y)*(b.x-p.x))
                        if cross<1e-7*max((p-a).length+(b-p).length,1e-6):
                            polygon.pop(j);changed=True;break
                if len(polygon)<3 or _area(polygon)<1e-10:continue
                records=[]
                for j in range(1,len(polygon)-1):
                    record=_surface_record((polygon[0],polygon[j],polygon[j+1]))
                    if record and _area(record[0])>1e-10:records.append(record)
                if not records:continue
                hull=_hull(polygon,thickness)
                if hull is None:raise RuntimeError('A terrain fragment produced a degenerate hull')
                # Clipping can turn a triangle into a six-sided polygon. Even
                # initial leaves must respect the requested component budget.
                leaves=[(polygon,records,hull)] if len(hull[1])<=max_triangles else [
                    (list(record[0]),[record],_hull(record[0],thickness)) for record in records]
                for boundary,surfaces,leaf in leaves:
                    if leaf is None:raise RuntimeError('A terrain fragment produced a degenerate hull')
                    node=dict(vertices=leaf[0],faces=leaf[1],triangles=surfaces,area=_area(boundary),above=0.,below=0.,boundary_error=0.,neighbors=set())
                    nodes[next_id]=node
                    for j,p in enumerate(boundary):
                        edge=tuple(sorted((_key(p),_key(boundary[(j+1)%len(boundary)]))))
                        if edge[0]!=edge[1]:edge_owners.setdefault(edge,[]).append(next_id)
                    next_id+=1
                if next_id>50000:raise RuntimeError('Terrain exceeds 50,000 initial components; select a smaller area or increase Patch Size')
    if not nodes:raise RuntimeError('Select a surface with nonzero XY area; vertical walls cannot be terrain')
    for owners in edge_owners.values():
        for i in owners:
            nodes[i]['neighbors'].update(j for j in owners if j!=i)
    heap=[]
    def enqueue(i,j):
        a,b=sorted((i,j))
        heapq.heappush(heap,(-(nodes[a]['area']+nodes[b]['area']),a,b))
    for i,node in nodes.items():
        for j in node['neighbors']:
            if i<j:enqueue(i,j)
    initial=len(nodes);merges=0;attempts=0
    while heap:
        _,i,j=heapq.heappop(heap)
        if i not in nodes or j not in nodes:continue
        attempts+=1
        candidate=_candidate(nodes[i],nodes[j],above,below,thickness,max_triangles,patch_size)
        if candidate is None:continue
        neighbors=(nodes[i]['neighbors']|nodes[j]['neighbors'])-{i,j}
        candidate['neighbors']=neighbors
        del nodes[i];del nodes[j]
        index=next_id;next_id+=1;nodes[index]=candidate;merges+=1
        for other in neighbors:
            nodes[other]['neighbors'].difference_update((i,j));nodes[other]['neighbors'].add(index)
            enqueue(index,other)
    vertices=[];faces=[]
    for node in nodes.values():
        offset=len(vertices);vertices.extend(node['vertices']);faces.extend(tuple(offset+i for i in f) for f in node['faces'])
    stats=dict(components=len(nodes),source_tris=len(triangles),initial_components=initial,merged_components=merges,
               merge_attempts=attempts,split_cells=0,max_depth=0,skipped_existing=0,ignored_vertical=ignored_vertical,
               max_above=max(n['above'] for n in nodes.values()),max_below=max(n['below'] for n in nodes.values()),
               max_component_triangles=max(len(n['faces']) for n in nodes.values()),
               seam_step_bound=2*max(n['boundary_error'] for n in nodes.values()),
               build_mode='ADAPTIVE_CONVEX',verts=len(vertices),faces=len(faces))
    return vertices,faces,stats
